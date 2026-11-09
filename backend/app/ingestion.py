"""InferenceService: provider calls with a 30 s limit and one retry, then confidence banding (M3 5.4, 9.4).

Never raises for AI failures: the submission ends in exactly one open state.
- NEEDS_VERIFICATION: a readable result with at least one line (every such draft is reviewed).
- REJECTED: overall confidence below 0.40, or no sales lines.
- AI_UNAVAILABLE: attempt 2 failed, or a non-retryable provider error.
Only masked values and bands are stored (SUBMISSIONS.draft_payload); logs never contain input
text, images, prompts or model output.
"""
import logging
import queue
import threading
import time
from decimal import ROUND_HALF_UP, Decimal

from . import repository
from .privacy import mask_text
from .vision import (EXTRACTION_PROMPT, ExtractionResult, ProviderFatalError, ProviderRetryableError,
                     ProviderTimeoutError, VisionModel)

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 30.0
MAX_ATTEMPTS = 2
RETRY_DELAY_SECONDS = 1.0

READABLE_THRESHOLD = 0.40
HIGH_THRESHOLD = 0.85
MEDIUM_THRESHOLD = 0.60

MAX_ITEM_CHARS = 100
MAX_QUANTITY = Decimal("99999.99")
MAX_UNIT_PRICE = Decimal("9999999.99")
CENT = Decimal("0.01")

MESSAGES = {
    # SRS FR-210 and FR-211 fix these opening words; the second sentence is the action (NFR-731).
    "UNREADABLE_IMAGE": "Image could not be read. Retake it with good light, the page flat and the whole page in the frame.",
    "UNREADABLE_TEXT": "We could not find sales lines in this text. You can type the sales in the form below.",
    "NO_LINES": "No sales lines found. Retake the photo or type the sales below.",
    "AI_UNAVAILABLE": "AI reading is temporarily unavailable. Type your sales in the form below. We kept your input.",
    "MOCK_IMAGE": "Demo mode: the mock AI cannot read photos, so these sample rows are not from your page. Edit or remove them.",
}


def _clean_name(value):
    if value is None:
        return None, False
    name = mask_text(" ".join(str(value).split()))
    if not name:
        return None, False
    if len(name) > MAX_ITEM_CHARS:
        return name[:MAX_ITEM_CHARS].rstrip(), True
    return name, False


def _clean_number(value, maximum, allow_zero):
    """Return (value or None, adjusted). Out-of-range values are cleared; extra decimals are rounded."""
    if value is None:
        return None, False
    number = Decimal(value)
    if not number.is_finite() or number > maximum or number < 0 or (number == 0 and not allow_zero):
        return None, False
    rounded = number.quantize(CENT, rounding=ROUND_HALF_UP)
    if rounded == 0 and not allow_zero:
        return None, False
    # "3.00" -> "3" (to_integral_value, not normalize, which would turn 100 into "1E+2").
    return rounded.to_integral_value() if rounded == rounded.to_integral_value() else rounded, rounded != number


def band_for(value, confidence):
    if value is None:
        return "LOW"
    if confidence is None:
        return "UNSCORED"
    if confidence >= HIGH_THRESHOLD:
        return "HIGH"
    if confidence >= MEDIUM_THRESHOLD:
        return "MEDIUM"
    return "LOW"


def apply_confidence_bands(result: ExtractionResult, input_type: str) -> dict:
    """Pure function: model output -> {status, payload}. Payload is the stored draft format."""
    if result.overallConfidence < READABLE_THRESHOLD:
        key = "UNREADABLE_IMAGE" if input_type == "IMAGE" else "UNREADABLE_TEXT"
        return {"status": "REJECTED", "payload": {"version": 1, "message": MESSAGES[key], "lines": []}}

    lines = []
    for extracted in result.lines:
        name, name_adjusted = _clean_name(extracted.itemName)
        quantity, quantity_adjusted = _clean_number(extracted.quantity, MAX_QUANTITY, allow_zero=False)
        price, price_adjusted = _clean_number(extracted.unitPrice, MAX_UNIT_PRICE, allow_zero=True)
        if name is None and quantity is None and price is None:
            continue
        values = {"itemName": name, "quantity": quantity, "unitPrice": price}
        confidences = {"itemName": extracted.itemNameConfidence, "quantity": extracted.quantityConfidence,
                       "unitPrice": extracted.unitPriceConfidence}
        adjusted = {"itemName": name_adjusted, "quantity": quantity_adjusted, "unitPrice": price_adjusted}
        line = {"lineId": len(lines) + 1, "bands": {}}  # M3 7.3: positive integer line IDs.
        for field, value in values.items():
            band = band_for(value, confidences[field])
            if band == "HIGH" and adjusted[field]:
                band = "MEDIUM"  # We changed what the model read, so the owner must check it.
            line["bands"][field] = band
            line[field] = None if band == "LOW" or value is None else str(value)
        lines.append(line)

    if not lines:
        return {"status": "REJECTED", "payload": {"version": 1, "message": MESSAGES["NO_LINES"], "lines": []}}
    return {"status": "NEEDS_VERIFICATION", "payload": {"version": 1, "message": None, "lines": lines}}


def _call_with_deadline(function, timeout):
    """Run a provider call on a daemon thread; give up after `timeout` seconds even if it hangs."""
    outcome = queue.Queue(maxsize=1)

    def run():
        try:
            outcome.put(("ok", function()))
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller's thread
            outcome.put(("error", exc))

    threading.Thread(target=run, daemon=True, name="vision-call").start()
    try:
        kind, value = outcome.get(timeout=timeout)
    except queue.Empty:
        raise ProviderTimeoutError("deadline exceeded") from None
    if kind == "error":
        raise value
    return value


class InferenceService:
    def __init__(self, model: VisionModel, timeout_seconds=TIMEOUT_SECONDS, max_attempts=MAX_ATTEMPTS,
                 retry_delay_seconds=RETRY_DELAY_SECONDS, sleep=time.sleep):
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.retry_delay_seconds = retry_delay_seconds
        self.sleep = sleep

    def extract_from_image(self, submission_id, image: bytes) -> dict:
        return self._extract(submission_id, "IMAGE",
                             lambda: self.model.extract_from_image(image, EXTRACTION_PROMPT, self.timeout_seconds))

    def extract_from_text(self, submission_id, masked_text: str) -> dict:
        return self._extract(submission_id, "TEXT",
                             lambda: self.model.extract_from_text(masked_text, EXTRACTION_PROMPT, self.timeout_seconds))

    def _call_provider(self, submission_id, call):
        """Return an ExtractionResult, or None after the final failure."""
        for attempt in range(1, self.max_attempts + 1):
            started = time.monotonic()
            try:
                result = _call_with_deadline(call, self.timeout_seconds)
                logger.info("extraction attempt ok", extra={"submissionId": str(submission_id), "attempt": attempt,
                                                            "latencyMs": int((time.monotonic() - started) * 1000)})
                return result
            except (ProviderTimeoutError, ProviderRetryableError) as exc:
                error_code = "TIMEOUT" if isinstance(exc, ProviderTimeoutError) else "RETRYABLE"
            except ProviderFatalError:
                error_code = "FATAL"
            except Exception:  # An unexpected adapter bug must still end in the manual path.
                error_code = "UNEXPECTED"
                logger.exception("unexpected extraction error", extra={"submissionId": str(submission_id)})
            logger.warning("extraction attempt failed: %s", error_code,
                           extra={"submissionId": str(submission_id), "attempt": attempt, "errorCode": error_code,
                                  "operation": "vision.extract", "errorCategory": f"AI_{error_code}",
                                  "durationMs": round((time.monotonic() - started) * 1000, 2)})
            if error_code in ("FATAL", "UNEXPECTED") or attempt == self.max_attempts:
                return None
            self.sleep(self.retry_delay_seconds)
        return None

    def _extract(self, submission_id, input_type, call) -> dict:
        repository.update_status(submission_id, "CLASSIFYING")
        result = self._call_provider(submission_id, call)
        if result is None:
            draft = {"status": "AI_UNAVAILABLE",
                     "payload": {"version": 1, "message": MESSAGES["AI_UNAVAILABLE"], "lines": []}}
        else:
            draft = apply_confidence_bands(result, input_type)
            if (draft["status"] == "NEEDS_VERIFICATION" and input_type == "IMAGE"
                    and getattr(self.model, "name", "") == "mock"):
                draft["payload"]["message"] = MESSAGES["MOCK_IMAGE"]
        try:
            # Only from CLASSIFYING: if the stale sweep (120 s) already recorded AI_UNAVAILABLE,
            # that outcome stands and this late result is discarded.
            repository.update_status(submission_id, draft["status"], draft_payload=draft["payload"],
                                     expected_status="CLASSIFYING")
        except repository.StaleTransition:
            logger.warning("late extraction result discarded", extra={"submissionId": str(submission_id)})
        return draft
