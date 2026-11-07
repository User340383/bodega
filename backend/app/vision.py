"""VisionModel boundary: reading sales lines from a sanitized photo or masked text (M3 5.4, 9.2, 9.3).

Implementations return a validated ExtractionResult or raise one of three provider errors.
They never retry: InferenceService owns the single retry, so the Anthropic client is created
with max_retries=0.

AI_PROVIDER selects the implementation:
- "claude": ClaudeVisionModel (needs AI_API_KEY).
- "mock": MockVisionModel. Deterministic and free. It parses simple typed lines and returns
  clearly labelled sample rows for photos. It says nothing about real-provider quality.
- anything else: UnavailableVisionModel, which makes every capture fall back to manual entry.
"""
import base64
import json
import os
import re
import threading
from abc import ABC, abstractmethod
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field, ValidationError

DEFAULT_EXTRACTION_MODEL = "claude-sonnet-5-5"
MAX_LINES = 100


class ProviderTimeoutError(Exception):
    """The provider did not answer in time. Retryable."""


class ProviderRetryableError(Exception):
    """429, 5xx, network failure or schema-invalid output. Retryable."""


class ProviderFatalError(Exception):
    """Any other 4xx, a refusal, or no provider configured. Not retried."""


class ExtractedLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    itemName: str | None = None
    quantity: Decimal | None = None
    unitPrice: Decimal | None = None
    itemNameConfidence: float | None = Field(default=None, ge=0, le=1)
    quantityConfidence: float | None = Field(default=None, ge=0, le=1)
    unitPriceConfidence: float | None = Field(default=None, ge=0, le=1)


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    overallConfidence: float = Field(ge=0, le=1)
    lines: list[ExtractedLine] = Field(default_factory=list, max_length=MAX_LINES)


EXTRACTION_PROMPT = """You transcribe sales logs from small shops. The input is a photo of a handwritten or printed
sales page, or pasted sales text. Record one line per item sold, in the order written.
For each line return itemName (as written, without quantity or price), quantity (number sold)
and unitPrice (price of one unit). If only a quantity and a line total are written, set
unitPrice = total / quantity rounded to 2 decimals and give unitPriceConfidence no higher than 0.7.
If a value cannot be read, return null for it with confidence 0. Each confidence is 0 to 1 and
states how sure you are that the value matches what is written. overallConfidence states how
readable the whole page is. Ignore totals, dates, headings, people's names, phone numbers and
notes that are not sales. Never invent items that are not written. Call record_sales once."""

_NULLABLE_NUMBER = {"type": ["number", "null"]}
RECORD_SALES_TOOL = {
    "name": "record_sales",
    "description": "Record every sales line read from the sales log, with a confidence for each value.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["overallConfidence", "lines"],
        "properties": {
            "overallConfidence": {"type": "number", "description": "0 to 1: how readable the whole page is."},
            "lines": {
                "type": "array",
                "description": "One entry per item sold, at most 100.",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["itemName", "quantity", "unitPrice", "itemNameConfidence",
                                 "quantityConfidence", "unitPriceConfidence"],
                    "properties": {
                        "itemName": {"type": ["string", "null"]},
                        "quantity": _NULLABLE_NUMBER,
                        "unitPrice": _NULLABLE_NUMBER,
                        "itemNameConfidence": _NULLABLE_NUMBER,
                        "quantityConfidence": _NULLABLE_NUMBER,
                        "unitPriceConfidence": _NULLABLE_NUMBER,
                    },
                },
            },
        },
    },
}


def parse_tool_input(raw: dict) -> ExtractionResult:
    """Validate the record_sales tool input. Schema problems are retryable per M3 5.4."""
    try:
        return ExtractionResult.model_validate(raw)
    except ValidationError as exc:
        raise ProviderRetryableError("schema-invalid output") from exc


class VisionModel(ABC):
    name: str

    @abstractmethod
    def extract_from_image(self, image: bytes, prompt: str, timeout: float) -> ExtractionResult:
        """`image` is an already sanitized JPEG."""

    @abstractmethod
    def extract_from_text(self, text: str, prompt: str, timeout: float) -> ExtractionResult:
        """`text` is already masked."""


class UnavailableVisionModel(VisionModel):
    name = "unavailable"

    def extract_from_image(self, image, prompt, timeout):
        raise ProviderFatalError("no extraction provider configured")

    def extract_from_text(self, text, prompt, timeout):
        raise ProviderFatalError("no extraction provider configured")


# Matches "Bread 3 1.50", "Rice (lb) 2.5 x 0.80", "Milk, 2, $2.25". Name first, then quantity, then price.
_TEXT_LINE = re.compile(r"^\s*(?P<name>.*?[^\d\s,;:x×$-].*?)[\s,;:]+(?P<qty>\d+(?:\.\d+)?)"
                        r"(?:\s*[x×@,;:]?\s*\$?\s*(?P<price>\d+(?:\.\d+)?))?\s*$", re.IGNORECASE)


class MockVisionModel(VisionModel):
    """Deterministic, free provider for local development and tests.

    Test hooks (constructor only, never selectable through the API):
    - canned: an ExtractionResult returned for every call instead of the built-in behaviour.
    - fail_modes: one entry per attempt, consumed in order: "timeout", "retryable", "fatal",
      "hang" (blocks until `release` is set or hang_seconds passes) or None for success.
    `calls` records what each attempt received, so tests can assert what reached the "AI".
    """
    name = "mock"

    def __init__(self, canned: ExtractionResult | None = None, fail_modes=(), hang_seconds: float = 60.0):
        self.canned = canned
        self.fail_modes = list(fail_modes)
        self.hang_seconds = hang_seconds
        self.release = threading.Event()
        self.calls: list[tuple[str, object]] = []

    def _maybe_fail(self):
        mode = self.fail_modes.pop(0) if self.fail_modes else None
        if mode == "timeout":
            raise ProviderTimeoutError("simulated timeout")
        if mode == "retryable":
            raise ProviderRetryableError("simulated 503")
        if mode == "fatal":
            raise ProviderFatalError("simulated 400")
        if mode == "hang":
            self.release.wait(self.hang_seconds)

    def extract_from_image(self, image, prompt, timeout):
        self.calls.append(("image", image))
        self._maybe_fail()
        if self.canned is not None:
            return self.canned
        # A mock cannot read photos: return obviously labelled sample rows with mixed confidence.
        return ExtractionResult(overallConfidence=0.9, lines=[
            ExtractedLine(itemName="Sample bread (mock)", quantity=Decimal("3"), unitPrice=Decimal("1.50"),
                          itemNameConfidence=0.95, quantityConfidence=0.7, unitPriceConfidence=0.95),
            ExtractedLine(itemName="Sample milk (mock)", quantity=Decimal("2"), unitPrice=None,
                          itemNameConfidence=0.9, quantityConfidence=0.92, unitPriceConfidence=0.0),
        ])

    def extract_from_text(self, text, prompt, timeout):
        self.calls.append(("text", text))
        self._maybe_fail()
        if self.canned is not None:
            return self.canned
        lines = []
        for raw in text.splitlines():
            match = _TEXT_LINE.match(raw)
            if not match or len(lines) >= MAX_LINES:
                continue
            try:
                quantity = Decimal(match["qty"])
                price = Decimal(match["price"]) if match["price"] else None
            except InvalidOperation:
                continue
            lines.append(ExtractedLine(
                itemName=match["name"].strip(" -:,;"), quantity=quantity, unitPrice=price,
                itemNameConfidence=0.9, quantityConfidence=0.9,
                unitPriceConfidence=0.9 if price is not None else 0.0,
            ))
        return ExtractionResult(overallConfidence=0.9 if lines else 0.5, lines=lines)


class ClaudeVisionModel(VisionModel):
    """Anthropic Messages API with a strict record_sales tool.

    Claude Sonnet 5.5 rejects a forced tool_choice and non-default temperature, so the request
    uses tool_choice auto, a strict schema and the prompt's "Call record_sales once"; a reply
    without the tool call is treated as schema-invalid output (retryable).
    """
    name = "claude"

    def __init__(self, api_key: str, model_name: str = DEFAULT_EXTRACTION_MODEL):
        import anthropic
        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=0)
        self.model_name = model_name

    def _call(self, content: list, prompt: str, timeout: float) -> ExtractionResult:
        anthropic = self._anthropic
        try:
            response = self.client.beta.messages.create(
                model=self.model_name,
                max_tokens=4096,
                system=prompt,
                tools=[RECORD_SALES_TOOL],
                tool_choice={"type": "auto"},
                messages=[{"role": "user", "content": content}],
                # On a safety-classifier decline the API re-runs the request on a fallback model.
                # Sent as extra_body so older SDK releases without the typed parameter still work.
                betas=["server-side-fallback-2026-07-01"],
                extra_body={"fallbacks": "default"},
                timeout=timeout,
            )
        except anthropic.APITimeoutError as exc:  # Subclass of APIConnectionError: check first.
            raise ProviderTimeoutError("provider timeout") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderRetryableError("network error") from exc
        except anthropic.RateLimitError as exc:
            raise ProviderRetryableError("HTTP 429") from exc
        except anthropic.InternalServerError as exc:
            raise ProviderRetryableError("HTTP 5xx") from exc
        except anthropic.APIStatusError as exc:
            raise ProviderFatalError(f"HTTP {exc.status_code}") from exc

        if response.stop_reason == "refusal":
            raise ProviderFatalError("refusal")
        for block in response.content:
            if block.type == "tool_use" and block.name == "record_sales":
                raw = block.input if isinstance(block.input, dict) else json.loads(block.input)
                return parse_tool_input(raw)
        raise ProviderRetryableError("no record_sales call")

    def extract_from_image(self, image, prompt, timeout):
        return self._call([{
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(image).decode()},
        }], prompt, timeout)

    def extract_from_text(self, text, prompt, timeout):
        return self._call([{"type": "text", "text": text}], prompt, timeout)


def get_vision_model() -> VisionModel:
    """FastAPI dependency; tests replace it through app.dependency_overrides."""
    provider = os.getenv("AI_PROVIDER", "").strip().lower()
    if provider == "mock":
        return MockVisionModel()
    if provider == "claude" and os.getenv("AI_API_KEY"):
        return ClaudeVisionModel(os.environ["AI_API_KEY"],
                                 os.getenv("AI_EXTRACTION_MODEL") or DEFAULT_EXTRACTION_MODEL)
    return UnavailableVisionModel()
