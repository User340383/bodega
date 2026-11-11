"""José's ingestion pipeline: timeout/retry, confidence bands, and POST /api/v1/extract end to end.

Unit tests at the top need no database. API tests use conftest's isolated-schema `client`
(FixedClock at 2026-04-30) with MockVisionModel and InMemoryObjectStorage injected, so no
paid API or real bucket is ever called.
"""
import io
import logging
from datetime import timedelta
from decimal import Decimal

import pytest
from PIL import Image

from app import ingestion
from app.database import connect
from app.ingestion import InferenceService, apply_confidence_bands
from app.main import app
from app.storage import InMemoryObjectStorage, get_object_storage
from app.vision import (ExtractedLine, ExtractionResult, MockVisionModel, ProviderRetryableError,
                        UnavailableVisionModel, get_vision_model, parse_tool_input)
from conftest import AS_OF

PHONE = "809-555-1234"
EMAIL = "ana@example.com"


def result(*lines, overall=0.9):
    return ExtractionResult(overallConfidence=overall, lines=list(lines))


def line(name="Bread", qty="3", price="1.50", nc=0.95, qc=0.95, pc=0.95):
    return ExtractedLine(itemName=name, quantity=None if qty is None else Decimal(qty),
                         unitPrice=None if price is None else Decimal(price),
                         itemNameConfidence=nc, quantityConfidence=qc, unitPriceConfidence=pc)


# ---------- confidence bands (M3 5.4 boundaries: 0.40, 0.60, 0.85, null) ----------

@pytest.mark.parametrize("confidence,band,kept", [
    (1.0, "HIGH", True), (0.85, "HIGH", True), (0.8499, "MEDIUM", True), (0.60, "MEDIUM", True),
    (0.5999, "LOW", False), (0.0, "LOW", False), (None, "UNSCORED", True),
])
def test_field_band_boundaries(confidence, band, kept):
    draft = apply_confidence_bands(result(line(qc=confidence)), "TEXT")
    assert draft["status"] == "NEEDS_VERIFICATION"
    first = draft["payload"]["lines"][0]
    assert first["bands"]["quantity"] == band
    assert first["quantity"] == ("3" if kept else None)  # LOW values are cleared.


def test_missing_value_is_low_and_blank():
    first = apply_confidence_bands(result(line(price=None, pc=0.99)), "TEXT")["payload"]["lines"][0]
    assert first["bands"]["unitPrice"] == "LOW" and first["unitPrice"] is None


@pytest.mark.parametrize("overall,status", [(0.40, "NEEDS_VERIFICATION"), (0.3999, "REJECTED"), (0.0, "REJECTED")])
def test_overall_confidence_threshold(overall, status):
    draft = apply_confidence_bands(result(line(), overall=overall), "IMAGE")
    assert draft["status"] == status
    if status == "REJECTED":
        assert draft["payload"]["lines"] == [] and draft["payload"]["message"].startswith("We could not read this photo")  # M3 9.4


def test_all_null_lines_are_dropped_and_no_lines_is_rejected():
    empty = ExtractedLine(itemNameConfidence=0, quantityConfidence=0, unitPriceConfidence=0)
    assert apply_confidence_bands(result(empty, empty), "TEXT") == {
        "status": "REJECTED", "payload": {"version": 1, "message": "No sales lines found. Retake the photo or type the sales below.", "lines": []}}
    assert apply_confidence_bands(result(), "IMAGE")["status"] == "REJECTED"
    kept = apply_confidence_bands(result(empty, line(), empty, line("Milk")), "TEXT")["payload"]["lines"]
    assert [(entry["lineId"], entry["itemName"]) for entry in kept] == [(1, "Bread"), (2, "Milk")]


def test_every_readable_draft_needs_verification_even_when_all_high():
    assert apply_confidence_bands(result(line()), "TEXT")["status"] == "NEEDS_VERIFICATION"


def test_contacts_in_ai_output_are_masked_before_storage():
    draft = apply_confidence_bands(result(line(name=f"Ana {PHONE} {EMAIL}")), "IMAGE")
    assert draft["payload"]["lines"][0]["itemName"] == "Ana [removed] [removed]"


# Extraction never rounds or clears a number before review (audit: 1.234 became 1.23). A value
# outside the approved rules (M3 7.2) is shown as read, never banded HIGH, and must be corrected:
# review flags it and POST /confirm rejects it (tests/test_database.py, final limits).
@pytest.mark.parametrize("qty,price,expect_qty,expect_price,qty_band", [
    ("1.234", "2", "1.234", "2", "MEDIUM"),           # audit regression: three decimals kept as read
    ("-1", "1.50", "-1", "1.50", "MEDIUM"),           # negative quantity kept, must be corrected
    ("0", "1.50", "0", "1.50", "MEDIUM"),             # quantity must be > 0
    ("100000", "1.50", "100000", "1.50", "MEDIUM"),   # above 99,999.99
    ("2.555", "0", "2.555", "0", "MEDIUM"),           # not rounded; price 0 allowed
    ("2.50", "1.5", "2.50", "1.5", "HIGH"),           # valid values unchanged, as written
    ("3.00", "1.20", "3", "1.20", "HIGH"),            # only an all-zero fraction is dropped
])
def test_numbers_are_kept_as_read_and_rule_breaking_values_force_review(qty, price, expect_qty, expect_price, qty_band):
    first = apply_confidence_bands(result(line(qty=qty, price=price, qc=0.99)), "TEXT")["payload"]["lines"][0]
    assert (first["quantity"], first["unitPrice"], first["bands"]["quantity"]) == (expect_qty, expect_price, qty_band)


def test_price_with_extra_decimals_is_kept_and_needs_review():
    first = apply_confidence_bands(result(line(qty="2", price="0.555", qc=0.99)), "TEXT")["payload"]["lines"][0]
    assert first["unitPrice"] == "0.555" and first["bands"]["unitPrice"] == "MEDIUM"


def test_float_from_the_provider_json_reaches_review_unrounded():
    """The real provider sends JSON numbers; the SDK gives floats. 1.234 must stay 1.234."""
    parsed = parse_tool_input({"overallConfidence": 0.9, "lines": [{
        "itemName": "Rice (lb)", "quantity": 1.234, "unitPrice": 0.8, "itemNameConfidence": 0.99,
        "quantityConfidence": 0.99, "unitPriceConfidence": 0.99}]})
    first = apply_confidence_bands(parsed, "TEXT")["payload"]["lines"][0]
    assert (first["quantity"], first["unitPrice"], first["bands"]["quantity"]) == ("1.234", "0.8", "MEDIUM")


def test_schema_invalid_tool_output_is_retryable():
    with pytest.raises(ProviderRetryableError):
        parse_tool_input({"overallConfidence": 2, "lines": []})
    with pytest.raises(ProviderRetryableError):
        parse_tool_input({"overallConfidence": 0.9, "lines": [{"itemName": "x", "extra": 1}]})


# ---------- timeout and single retry (M3 5.4, SD-02) ----------

@pytest.fixture
def statuses(monkeypatch):
    """Record status writes instead of touching the database."""
    recorded = []
    monkeypatch.setattr(ingestion.repository, "update_status",
                        lambda sid, status, draft_payload=None, **_: recorded.append((status, draft_payload)))
    return recorded


def service(model, **kwargs):
    sleeps = []
    kwargs.setdefault("sleep", sleeps.append)
    return InferenceService(model, **kwargs), sleeps


def test_success_on_first_attempt(statuses):
    model = MockVisionModel()
    svc, sleeps = service(model)
    assert svc.extract_from_text("sid", "Bread 3 1.50")["status"] == "NEEDS_VERIFICATION"
    assert len(model.calls) == 1 and sleeps == []
    assert [status for status, _ in statuses] == ["CLASSIFYING", "NEEDS_VERIFICATION"]


@pytest.mark.parametrize("first_failure", ["timeout", "retryable"])
def test_retryable_failure_waits_one_second_then_retries_once(statuses, first_failure):
    model = MockVisionModel(fail_modes=[first_failure, None])
    svc, sleeps = service(model)
    assert svc.extract_from_text("sid", "Bread 3 1.50")["status"] == "NEEDS_VERIFICATION"
    assert len(model.calls) == 2 and sleeps == [1.0]


@pytest.mark.parametrize("modes", [["timeout", "timeout"], ["retryable", "timeout"], ["timeout", "retryable"]])
def test_second_failure_ends_in_ai_unavailable_without_raising(statuses, modes):
    model = MockVisionModel(fail_modes=modes)
    svc, _ = service(model)
    draft = svc.extract_from_image("sid", b"jpeg")
    assert draft["status"] == "AI_UNAVAILABLE" and draft["payload"]["lines"] == []
    assert len(model.calls) == 2  # never a third attempt
    assert statuses[-1][0] == "AI_UNAVAILABLE"


def test_fatal_error_is_not_retried(statuses):
    model = MockVisionModel(fail_modes=["fatal"])
    svc, sleeps = service(model)
    assert svc.extract_from_text("sid", "Bread 3")["status"] == "AI_UNAVAILABLE"
    assert len(model.calls) == 1 and sleeps == []


def test_no_configured_provider_falls_back_to_manual_entry(statuses):
    svc, _ = service(UnavailableVisionModel())
    assert svc.extract_from_text("sid", "Bread 3")["status"] == "AI_UNAVAILABLE"


def test_unexpected_adapter_bug_still_reaches_manual_path(statuses):
    class Broken(MockVisionModel):
        def extract_from_text(self, text, prompt, timeout):
            self.calls.append(("text", text))
            raise RuntimeError("bug")
    model = Broken()
    svc, _ = service(model)
    assert svc.extract_from_text("sid", "Bread 3")["status"] == "AI_UNAVAILABLE"
    assert len(model.calls) == 1


def test_hung_provider_is_cut_off_at_the_deadline(statuses):
    model = MockVisionModel(fail_modes=["hang", "hang"], hang_seconds=5)
    svc, sleeps = service(model, timeout_seconds=0.05)
    try:
        draft = svc.extract_from_text("sid", "Bread 3")
    finally:
        model.release.set()
    assert draft["status"] == "AI_UNAVAILABLE"
    assert len(model.calls) == 2 and sleeps == [1.0]


def test_provider_receives_the_30_second_timeout_and_fixed_prompt(statuses):
    seen = {}

    class Spy(MockVisionModel):
        def extract_from_text(self, text, prompt, timeout):
            seen.update(prompt=prompt, timeout=timeout)
            return super().extract_from_text(text, prompt, timeout)
    service(Spy())[0].extract_from_text("sid", "Bread 3")
    assert seen["timeout"] == 30.0 and "Call record_sales once" in seen["prompt"]


def test_mock_text_parser_reads_simple_lines():
    parsed = MockVisionModel().extract_from_text("Bread 3 1.50\nRice (lb) 2.5 x 0.80\nMilk, 2\nTOTAL\n", "", 30)
    assert [(entry.itemName, entry.quantity, entry.unitPrice) for entry in parsed.lines] == [
        ("Bread", Decimal("3"), Decimal("1.50")), ("Rice (lb)", Decimal("2.5"), Decimal("0.80")),
        ("Milk", Decimal("2"), None)]


# ---------- POST /api/v1/extract end to end (isolated PostgreSQL schema) ----------

def jpeg_with_metadata(size=(64, 48)):
    image = Image.new("RGB", size, (10, 120, 40))
    exif = image.getexif()
    exif[0x010F] = "SecretCam"
    exif.get_ifd(0x8825)[1] = "N"
    out = io.BytesIO()
    image.save(out, format="JPEG", exif=exif.tobytes())
    return out.getvalue()


@pytest.fixture
def capture(client):
    """Inject the mock model and in-memory storage into the real route."""
    deps = {"model": MockVisionModel(), "storage": InMemoryObjectStorage()}
    app.dependency_overrides[get_vision_model] = lambda: deps["model"]
    app.dependency_overrides[get_object_storage] = lambda: deps["storage"]
    return deps


def stored_submission(submission_id):
    with connect() as conn:
        return conn.execute("SELECT status, input_type, image_key, masked_text, draft_payload::text FROM submissions WHERE id=%s",
                            (submission_id,)).fetchone()


def submission_count():
    with connect() as conn:
        return conn.execute("SELECT count(*) FROM submissions").fetchone()[0]


def test_text_is_masked_before_storage_and_before_the_model(client, capture):
    text = f"Bread 3 1.50\nCall Ana {PHONE} or {EMAIL}\nMilk 2"
    response = client.post("/api/v1/extract", data={"saleDate": AS_OF.isoformat(), "text": text})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "NEEDS_VERIFICATION" and body["inputType"] == "TEXT" and not body["manualEntry"]
    assert body["imageUrl"] is None
    assert PHONE not in body["maskedText"] and EMAIL not in body["maskedText"] and "[removed]" in body["maskedText"]

    sent = capture["model"].calls[0][1]
    assert PHONE not in sent and EMAIL not in sent  # The AI never sees contacts.
    status, input_type, image_key, masked_text, payload = stored_submission(body["submissionId"])
    assert (status, input_type, image_key) == ("NEEDS_VERIFICATION", "TEXT", None)
    assert PHONE not in masked_text and PHONE not in payload and EMAIL not in payload

    lines = {entry["itemName"]: entry for entry in body["lines"]}
    assert lines["Bread"]["quantity"] == "3" and lines["Bread"]["unitPrice"] == "1.50"  # as typed
    assert lines["Milk"]["bands"]["unitPrice"] == "LOW" and lines["Milk"]["unitPrice"] is None

    # The reviewed draft confirms through Gabriel's transaction with AI provenance kept.
    confirm = client.post("/api/v1/confirm", json={"submissionId": body["submissionId"], "lines": [
        {"draftLineId": lines["Bread"]["lineId"], "itemName": "Bread", "quantity": "3", "unitPrice": "1.50"},
        {"draftLineId": lines["Milk"]["lineId"], "itemName": "Milk", "quantity": "2", "unitPrice": "2.25"},
    ]})
    assert confirm.status_code == 201, confirm.text
    assert {record["source"] for record in client.get("/api/v1/sales").json()["records"]} == {"AI_EXTRACTED"}


def test_photo_metadata_is_stripped_before_storage_and_before_the_model(client, capture):
    original = jpeg_with_metadata()
    assert b"SecretCam" in original
    response = client.post("/api/v1/extract", data={"saleDate": AS_OF.isoformat()},
                           files={"file": ("page.jpg", original, "image/jpeg")})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["inputType"] == "IMAGE" and body["status"] == "NEEDS_VERIFICATION"
    assert body["imageUrl"].startswith("memory://images/") and "Demo mode" in body["message"]

    [(key, (stored, content_type))] = capture["storage"].objects.items()
    sent = capture["model"].calls[0][1]
    assert content_type == "image/jpeg" and stored == sent  # Only the sanitized copy exists.
    assert b"SecretCam" not in stored
    with Image.open(io.BytesIO(stored)) as image:
        assert len(image.getexif()) == 0
    assert stored_submission(body["submissionId"])[2] == key

    detail = client.get(f"/api/v1/submissions/{body['submissionId']}").json()
    assert detail["inputType"] == "IMAGE" and detail["imageUrl"] == body["imageUrl"]


def test_file_type_comes_from_magic_bytes_not_the_client_header(client, capture):
    png = io.BytesIO()
    Image.new("RGB", (8, 8)).save(png, format="PNG")
    response = client.post("/api/v1/extract", data={"saleDate": AS_OF.isoformat()},
                           files={"file": ("page.bin", png.getvalue(), "application/octet-stream")})
    assert response.status_code == 200
    fake = client.post("/api/v1/extract", data={"saleDate": AS_OF.isoformat()},
                       files={"file": ("page.jpg", b"not really a jpeg", "image/jpeg")})
    assert fake.status_code == 400 and fake.json()["detail"][0]["loc"] == ["body", "file"]


def test_storage_failure_returns_503_creates_nothing_and_skips_ai(client, capture):
    capture["storage"] = InMemoryObjectStorage(fail_puts=True)
    response = client.post("/api/v1/extract", data={"saleDate": AS_OF.isoformat()},
                           files={"file": ("page.jpg", jpeg_with_metadata(), "image/jpeg")})
    assert response.status_code == 503
    assert submission_count() == 0 and capture["model"].calls == []


def test_ai_timeouts_fall_back_to_manual_entry_and_manual_rows_confirm(client, capture):
    capture["model"] = MockVisionModel(fail_modes=["retryable", "timeout"])
    response = client.post("/api/v1/extract", data={"saleDate": AS_OF.isoformat(), "text": "Bread 3 1.50"})
    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["manualEntry"], body["lines"]) == ("AI_UNAVAILABLE", True, [])
    assert body["message"].startswith("We could not read this right now")  # M3 9.4
    assert len(capture["model"].calls) == 2

    confirm = client.post("/api/v1/confirm", json={"submissionId": body["submissionId"], "lines": [
        {"itemName": "Bread", "quantity": "3", "unitPrice": "1.50"}]})
    assert confirm.status_code == 201, confirm.text
    assert client.get("/api/v1/sales").json()["records"][0]["source"] == "MANUAL"


def test_fatal_provider_error_is_not_retried_through_the_api(client, capture):
    capture["model"] = MockVisionModel(fail_modes=["fatal"])
    body = client.post("/api/v1/extract", data={"saleDate": AS_OF.isoformat(), "text": "Bread 3"}).json()
    assert body["status"] == "AI_UNAVAILABLE" and len(capture["model"].calls) == 1


def test_unreadable_page_is_rejected_with_retake_guidance(client, capture):
    capture["model"] = MockVisionModel(canned=result(line(), overall=0.1))
    body = client.post("/api/v1/extract", data={"saleDate": AS_OF.isoformat()},
                       files={"file": ("page.jpg", jpeg_with_metadata(), "image/jpeg")}).json()
    assert (body["status"], body["manualEntry"], body["lines"]) == ("REJECTED", True, [])
    assert "good light" in body["message"]


@pytest.mark.parametrize("sale_date,ok", [
    (AS_OF, True), (AS_OF - timedelta(days=365), True),
    (AS_OF - timedelta(days=366), False), (AS_OF + timedelta(days=1), False),
])
def test_sale_date_window_uses_the_business_clock(client, capture, sale_date, ok):
    response = client.post("/api/v1/extract", data={"saleDate": sale_date.isoformat(), "text": "Bread 3"})
    if ok:
        assert response.status_code == 200
    else:
        assert response.status_code == 400 and response.json()["detail"][0]["loc"] == ["body", "saleDate"]
        assert submission_count() == 0 and capture["model"].calls == []


def test_text_over_5000_characters_is_rejected_before_the_model(client, capture):
    response = client.post("/api/v1/extract", data={"saleDate": AS_OF.isoformat(), "text": "x" * 5001})
    assert response.status_code == 400 and capture["model"].calls == []
    assert client.post("/api/v1/extract", data={"saleDate": AS_OF.isoformat(),
                                                "text": "  Bread 3  " + " " * 5000}).status_code == 200


def test_logs_never_contain_input_text_or_contacts(client, capture, caplog):
    capture["model"] = MockVisionModel(fail_modes=["retryable", None])
    with caplog.at_level(logging.DEBUG, logger="app"):
        client.post("/api/v1/extract", data={"saleDate": AS_OF.isoformat(), "text": f"Secret bread 3 1.50 {PHONE}"})
    logged = "\n".join(record.getMessage() + str(record.__dict__) for record in caplog.records)
    assert "Secret bread" not in logged and PHONE not in logged and "[removed]" not in logged
