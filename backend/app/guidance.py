"""GuidanceEngine: optional provider wording around figures the backend already calculated."""
import logging
import queue
import threading
from typing import Literal

from pydantic import BaseModel

from .analysis import AnalysisResponse, ItemEstimate, Trend, normalize_item
from .grounding import ALLOWED_WORDS, MAX_MESSAGE_CHARS, check_grounding
from .llm import ItemEvidence, ProviderNotConfigured, WordingProvider, WordingRequest

logger = logging.getLogger(__name__)

PROVIDER_TIMEOUT_SECONDS = 15.0

STOCK_NOTICE = (
    "These are sales-based planning suggestions, not low-stock alerts. Bodega does not record "
    "current inventory, so check your actual stock before ordering."
)

# Fixed text only: provider output and exception details never reach a response or a log.
FAILURE_REASONS = {
    "INSUFFICIENT_HISTORY": "At least 7 logged sales dates are needed. No wording provider was called.",
    "PROVIDER_NOT_CONFIGURED": "No wording provider is configured, so template wording was used.",
    "PROVIDER_TIMEOUT": "The wording provider did not respond in time, so template wording was used.",
    "PROVIDER_FAILURE": "The wording provider failed, so template wording was used.",
    "EMPTY_RESPONSE": "The wording provider returned nothing, so template wording was used.",
    "MALFORMED_JSON": "The wording provider returned malformed data, so template wording was used.",
    "INVALID_SCHEMA": "The wording provider returned an invalid structure, so template wording was used.",
    "UNKNOWN_ITEM": "The provider named a product not in your sales analysis, so its wording was rejected.",
    "DUPLICATE_ITEM": "The provider repeated a product, so its wording was rejected.",
    "MISSING_ITEM": "The provider omitted a product, so its wording was rejected.",
    "ITEM_NOT_NAMED": "The provider's message did not name its product, so its wording was rejected.",
    "QUANTITY_MISMATCH": "The provider changed or omitted a planning quantity, so its wording was rejected.",
    "UNSUPPORTED_NUMBER": "The provider used a number the analysis does not support, so its wording was rejected.",
    "TREND_MISMATCH": "The provider's wording contradicted the calculated trend, so its wording was rejected.",
    "MESSAGE_TOO_LONG": "The provider's message was too long, so its wording was rejected.",
    "UNSAFE_MARKUP": "The provider's message was not plain text, so its wording was rejected.",
    "UNSUPPORTED_CLAIM": "The provider made a claim outside the sales evidence, so its wording was rejected.",
}

_TREND_SENTENCES = {
    "UP": "Sales are trending up compared with the previous 7 logged dates.",
    "DOWN": "Sales are trending down compared with the previous 7 logged dates.",
    "STABLE": "Sales are stable compared with the previous 7 logged dates.",
    "INSUFFICIENT_DATA": "There is not enough history for a trend yet.",
}


class GuidanceItem(BaseModel):
    itemName: str
    suggestedQty: str
    currentAverage: str
    previousAverage: str | None
    trend: Trend
    message: str  # Plain text. Clients must render it as text, never as HTML.


class GuidanceResponse(BaseModel):
    asOf: str
    status: Literal["READY", "INSUFFICIENT_HISTORY"]
    items: list[GuidanceItem]
    source: Literal["AI_WORDED", "TEMPLATE"]
    groundingPassed: bool  # Describes the provider's own output, never the fallback.
    failedRule: str | None
    failureReason: str | None
    providerCalled: bool
    provider: str | None
    stockNotice: str


def template_message(estimate: ItemEstimate) -> str:
    """Fallback wording, built exclusively from validated backend figures."""
    trend = _TREND_SENTENCES[estimate.trend]
    if estimate.suggestedQty == "0":
        return f"{estimate.itemName}: no confirmed sales in the latest 7 logged dates, so no quantity is suggested. {trend}"
    about = "about " if estimate.currentAverageRounded else ""
    return (f"{estimate.itemName}: plan for {estimate.suggestedQty} units to cover 7 logged sales dates. "
            f"Average is {about}{estimate.currentAverage} per logged date. {trend}")


def _instructions() -> str:
    return (
        "Rewrite sales planning evidence as one short English plain-text message per item.\n"
        'Reply with only JSON: {"items": [{"itemName": "...", "suggestedQty": "...", "message": "..."}]}.\n'
        "Include every item exactly once. Copy itemName and suggestedQty exactly.\n"
        "Each message must contain the item name and its suggestedQty, for example "
        '"Bread: plan 28 units to cover 7 logged sales dates."\n'
        "Use only numbers from that item's evidence. Describe only that item's trend value.\n"
        f"Keep each message under {MAX_MESSAGE_CHARS} characters. Do not use markup.\n"
        "Other than the item name, use only these words: " + " ".join(sorted(ALLOWED_WORDS)) + "\n"
        "Never mention prices, profit, weather, competitors, guarantees, or whether stock is low."
    )


class GuidanceEngine:
    def __init__(self, provider: WordingProvider, timeout_seconds: float = PROVIDER_TIMEOUT_SECONDS):
        self._provider = provider
        self._timeout_seconds = timeout_seconds

    def generate(self, analysis: AnalysisResponse) -> GuidanceResponse:
        eligible = [estimate for estimate in analysis.estimates if estimate.suggestedQty is not None]
        if not eligible:
            return self._response(analysis, "INSUFFICIENT_HISTORY", [], "INSUFFICIENT_HISTORY", provider_called=False)

        evidence = tuple(ItemEvidence(
            itemName=estimate.itemName, suggestedQty=estimate.suggestedQty,
            currentAverage=estimate.currentAverage, previousAverage=estimate.previousAverage,
            trend=estimate.trend, changePercent=estimate.changePercent,
            windowLoggedDays=estimate.windowLoggedDays,
        ) for estimate in eligible)

        raw, rule = self._call_provider(WordingRequest(_instructions(), evidence))
        messages = None
        if rule is None:
            result = check_grounding(raw, evidence)
            rule, messages = result.failed_rule, result.messages
        if rule:
            # Rule code and provider name only: raw prompts, responses and sales stay out of logs.
            logger.warning("Guidance used template fallback (rule=%s, provider=%s)", rule, self._provider.name)

        items = [GuidanceItem(
            itemName=estimate.itemName, suggestedQty=estimate.suggestedQty,
            currentAverage=estimate.currentAverage, previousAverage=estimate.previousAverage,
            trend=estimate.trend,
            message=messages[normalize_item(estimate.itemName)] if messages else template_message(estimate),
        ) for estimate in eligible]
        return self._response(analysis, "READY", items, rule, provider_called=True)

    def _call_provider(self, request: WordingRequest) -> tuple[str | None, str | None]:
        """One attempt, no retry. A daemon thread bounds the wait even if a provider blocks in
        code that ignores cancellation; a late result is discarded with the abandoned queue."""
        outcome: queue.Queue = queue.Queue(maxsize=1)

        def attempt():
            try:
                outcome.put((self._provider.generate(request), None))
            except ProviderNotConfigured:
                outcome.put((None, "PROVIDER_NOT_CONFIGURED"))
            except Exception:  # Any provider defect becomes a safe fallback; details stay private.
                outcome.put((None, "PROVIDER_FAILURE"))

        threading.Thread(target=attempt, name="guidance-provider", daemon=True).start()
        try:
            return outcome.get(timeout=self._timeout_seconds)
        except queue.Empty:
            return None, "PROVIDER_TIMEOUT"

    def _response(self, analysis, status, items, rule, *, provider_called) -> GuidanceResponse:
        return GuidanceResponse(
            asOf=analysis.asOf.isoformat(), status=status, items=items,
            source="TEMPLATE" if rule else "AI_WORDED", groundingPassed=rule is None,
            failedRule=rule, failureReason=FAILURE_REASONS.get(rule),
            providerCalled=provider_called, provider=self._provider.name if provider_called else None,
            stockNotice=STOCK_NOTICE,
        )
