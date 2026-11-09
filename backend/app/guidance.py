"""GuidanceEngine: optional provider wording around figures the backend already calculated."""
import logging
from decimal import Decimal
from fractions import Fraction
import queue
import threading
from typing import Literal

from pydantic import BaseModel

from .analysis import AnalysisResponse, ItemEstimate, Trend, normalize_item
from . import readability
from .grounding import ALLOWED_WORDS, MAX_MESSAGE_CHARS, MAX_MESSAGE_WORDS, check_grounding
from .llm import ItemEvidence, ProviderNotConfigured, WordingProvider, WordingRequest

logger = logging.getLogger(__name__)

PROVIDER_TIMEOUT_SECONDS = 15.0
MAX_GUIDANCE_WORDS = 150  # NFR-734
MAX_GUIDANCE_GRADE = 8.0  # NFR-734 (Flesch-Kincaid)

# M3 9.7 fixed notices (exact text).
STOCK_NOTICE = ("These numbers come only from the sales you logged. "
                "Check what you already have in stock before you buy.")
TEMPLATE_LABEL = "Standard advice"

# Fixed text only (NFR-731: cause + one action, <= 30 words, grade <= 8, no codes or provider names).
FAILURE_REASONS = {
    "INSUFFICIENT_HISTORY": "Log at least 7 days of sales to get advice.",
    "PROVIDER_NOT_CONFIGURED": "AI advice is turned off, so this is standard advice. You can still use it.",
    "PROVIDER_TIMEOUT": "The AI took too long, so this is standard advice. You can ask again.",
    "PROVIDER_FAILURE": "The AI did not answer, so this is standard advice. You can ask again.",
    "EMPTY_RESPONSE": "The AI sent back nothing, so this is standard advice. You can ask again.",
    "MALFORMED_JSON": "The AI sent back text we could not use, so this is standard advice. You can ask again.",
    "INVALID_SCHEMA": "The AI sent back text we could not use, so this is standard advice. You can ask again.",
    "UNKNOWN_ITEM": "The AI named an item you did not sell, so this is standard advice. You can ask again.",
    "DUPLICATE_ITEM": "The AI named an item twice, so this is standard advice. You can ask again.",
    "MISSING_ITEM": "The AI left out an item, so this is standard advice. You can ask again.",
    "ITEM_NOT_NAMED": "The AI did not name each item, so this is standard advice. You can ask again.",
    "QUANTITY_MISMATCH": "The AI changed a number, so this is standard advice. You can ask again.",
    "UNSUPPORTED_NUMBER": "The AI used a number that is not in your sales, so this is standard advice. You can ask again.",
    "TREND_MISMATCH": "The AI got the sales trend wrong, so this is standard advice. You can ask again.",
    "MESSAGE_TOO_LONG": "The AI advice was too long, so this is standard advice. You can ask again.",
    "UNSAFE_MARKUP": "The AI advice was not plain text, so this is standard advice. You can ask again.",
    "UNSUPPORTED_CLAIM": "The AI said something your sales do not show, so this is standard advice. You can ask again.",
    "HARD_TO_READ": "The AI advice was too hard to read, so this is standard advice. You can ask again.",
}

# M3 9.6 trendPhrase.
TREND_PHRASES = {
    "UP": "sales are rising",
    "DOWN": "sales are slowing",
    "STABLE": "sales are steady",
    "INSUFFICIENT_DATA": "not enough history for a trend yet",
}


class GuidanceItem(BaseModel):
    itemName: str
    suggestedQty: Decimal
    currentAverage: Decimal
    previousAverage: Decimal | None
    trend: Trend
    message: str  # Plain text. Clients must render it as text, never as HTML.


class GuidanceResponse(BaseModel):
    asOf: str
    status: Literal["READY", "INSUFFICIENT_HISTORY"]
    items: list[GuidanceItem]
    source: Literal["AI_WORDED", "TEMPLATE"]
    sourceLabel: str | None  # "Standard advice" whenever source is TEMPLATE (M3 9.7).
    groundingPassed: bool  # Describes the provider's own output, never the fallback.
    failedRule: str | None
    failureReason: str | None
    providerCalled: bool
    # NFR-734: advice is limited to 150 words, so it covers the items with the highest suggested
    # stock that fit; this many eligible items were left out (their figures stay on the dashboard).
    omittedItems: int = 0
    provider: str | None
    stockNotice: str


def template_message(estimate: ItemEstimate) -> str:
    """M3 9.6 fallback template, built exclusively from validated backend figures."""
    return (f"{estimate.itemName}: you sold about {estimate.currentAverageDisplay} per logged day over your "
            f"last 7 logged days ({TREND_PHRASES[estimate.trend]}). Consider having {estimate.suggestedQty} "
            f"on hand for your next 7 selling days.")


def _instructions() -> str:
    return (
        "Rewrite sales planning evidence as one short English plain-text message per item.\n"
        'Reply with only JSON: {"items": [{"itemName": "...", "suggestedQty": "...", "message": "..."}]}.\n'
        "Include every item exactly once. Copy itemName and suggestedQty exactly.\n"
        "Each message must contain the item name and its suggestedQty, for example "
        '"Bread: you sold about 4.0 per logged day over your last 7 logged days. '
        'Consider having 28 on hand for your next 7 selling days."\n'
        "Use only numbers from that item's evidence; write the average as averageOneDecimal. "
        "Describe only that item's trend value.\n"
        f"Keep each message under {MAX_MESSAGE_WORDS} words and {MAX_MESSAGE_CHARS} characters. "
        "Do not use markup.\n"
        "Other than the item name, use only these words: " + " ".join(sorted(ALLOWED_WORDS)) + "\n"
        "Never mention prices, profit, weather, competitors, guarantees, or whether stock is low."
    )


def _text(value):
    return None if value is None else format(value, "f")


def select_for_guidance(eligible: list[ItemEstimate]) -> list[ItemEstimate]:
    """Items whose advice fits NFR-734's 150 words, using the fixed M3 template as the budget.

    The M3 template is about 28 words per item, so 6 or more items cannot all fit. Items with the
    highest suggested stock come first (ties by name). Only the selected items are sent to the
    provider, so M3 9.6 rule 1 (every item sent appears exactly once) still holds.
    """
    ordered = sorted(eligible, key=lambda e: (-Fraction(e.suggestedQty), e.itemName.casefold()))
    chosen, used = [], 0
    for estimate in ordered:
        words = len(readability.words(template_message(estimate)))
        if chosen and used + words > MAX_GUIDANCE_WORDS:
            break
        chosen.append(estimate)
        used += words
    return chosen


class GuidanceEngine:
    def __init__(self, provider: WordingProvider, timeout_seconds: float = PROVIDER_TIMEOUT_SECONDS):
        self._provider = provider
        self._timeout_seconds = timeout_seconds

    def generate(self, analysis: AnalysisResponse) -> GuidanceResponse:
        eligible = select_for_guidance([e for e in analysis.estimates if e.suggestedQty is not None])
        omitted = sum(e.suggestedQty is not None for e in analysis.estimates) - len(eligible)
        if not eligible:
            return self._response(analysis, "INSUFFICIENT_HISTORY", [], "INSUFFICIENT_HISTORY", provider_called=False)

        evidence = tuple(ItemEvidence(
            itemName=estimate.itemName, suggestedQty=_text(estimate.suggestedQty),
            currentAverage=_text(estimate.currentAverage), previousAverage=_text(estimate.previousAverage),
            trend=estimate.trend, changePercent=_text(estimate.changePercent),
            windowLoggedDays=estimate.windowLoggedDays, averageOneDecimal=estimate.currentAverageDisplay,
        ) for estimate in eligible)

        raw, rule = self._call_provider(WordingRequest(_instructions(), evidence))
        messages = None
        if rule is None:
            result = check_grounding(raw, evidence)
            rule, messages = result.failed_rule, result.messages
            if rule is None:
                # NFR-734: displayed guidance <= 150 words and grade <= 8, else withheld (as FR-504).
                text = " ".join(messages.values())
                if len(readability.words(text)) > MAX_GUIDANCE_WORDS:
                    rule = "MESSAGE_TOO_LONG"
                elif readability.grade(text) > MAX_GUIDANCE_GRADE:
                    rule = "HARD_TO_READ"
        if rule:
            # Rule code and provider name only: raw prompts, responses and sales stay out of logs.
            logger.warning("Guidance used template fallback (rule=%s, provider=%s)", rule, self._provider.name,
                           extra={"operation": "guidance.wording", "errorCategory": rule, "rule": rule})

        items = [GuidanceItem(
            itemName=estimate.itemName, suggestedQty=estimate.suggestedQty,
            currentAverage=estimate.currentAverage, previousAverage=estimate.previousAverage,
            trend=estimate.trend,
            message=messages[normalize_item(estimate.itemName)] if messages else template_message(estimate),
        ) for estimate in eligible]
        return self._response(analysis, "READY", items, rule, provider_called=True, omitted=omitted)

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

    def _response(self, analysis, status, items, rule, *, provider_called, omitted=0) -> GuidanceResponse:
        return GuidanceResponse(
            asOf=analysis.asOf.isoformat(), status=status, items=items,
            source="TEMPLATE" if rule else "AI_WORDED", sourceLabel=TEMPLATE_LABEL if rule else None,
            groundingPassed=rule is None,
            failedRule=rule, failureReason=FAILURE_REASONS.get(rule),
            providerCalled=provider_called, provider=self._provider.name if provider_called else None,
            stockNotice=STOCK_NOTICE, omittedItems=omitted,
        )
