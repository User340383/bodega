"""Bodega API (M3 4.2). Every endpoint except /health requires the owner access code (NFR-718).

Error body for every non-2xx response (M3 4.2):
    {"error": {"code": "VALIDATION_ERROR", "message": "plain English", "fields": {"saleDate": "..."}},
     "detail": ...}
`detail` repeats FastAPI's original detail (string or [{loc, msg, type}]) for clients written
before the envelope; it never contains submitted values.
"""
import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import privacy, repository, retention
from .analysis import AnalysisResponse, BaselineAnalyzer, IsoDate, resolve_as_of
from .clock import Clock, get_clock
from .database import connect, migrate
from .guidance import GuidanceEngine, GuidanceResponse
from .ingestion import InferenceService
from .llm import WordingProvider, get_wording_provider
from .logging_setup import RequestLogMiddleware, configure_logging
from .security import FailedAttemptLimiter, allowed_origins, check_required_settings, require_owner_access
from .storage import StorageError, get_object_storage
from .vision import VisionModel, get_vision_model

logger = logging.getLogger(__name__)

MAX_QUANTITY = Decimal("99999.99")
MAX_UNIT_PRICE = Decimal("9999999.99")
MAX_ITEM_CHARS = 100
MAX_PAST_DAYS = 365
MAX_HISTORY_RANGE_DAYS = 366


@asynccontextmanager
async def lifespan(app):
    configure_logging()
    check_required_settings()
    app.state.access_limiter = FailedAttemptLimiter()
    migrate()
    # Startup sweep turns submissions interrupted by a restart into AI_UNAVAILABLE (M3 5.6).
    await asyncio.to_thread(retention.safe_sweep, get_object_storage())
    task = asyncio.create_task(retention.sweep_forever(get_object_storage))
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Bodega API", version="1.0.0", lifespan=lifespan)
# Production serves the SPA from its own Render service, so FRONTEND_URL is the only allowed origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["X-Request-ID", "X-Records-Deleted"],
)
app.add_middleware(RequestLogMiddleware)


# ---- Errors (M3 4.2 envelope, NFR-731 plain-English messages) ------------------------------
ERROR_CODES = {400: "VALIDATION_ERROR", 401: "UNAUTHORIZED", 404: "NOT_FOUND", 409: "INVALID_STATE",
               429: "RATE_LIMITED", 500: "DATABASE_ERROR", 503: "SERVICE_UNAVAILABLE"}
GENERIC_MESSAGES = {
    400: "Check the highlighted fields and try again.",
    401: "Your access code was not accepted. Please sign in again.",
    404: "We could not find that. It may have been deleted.",
    409: "This cannot be changed right now. Refresh and try again.",
    500: "Your sales could not be saved or loaded. Your draft is unchanged; please try again.",
}


class ApiError(HTTPException):
    """HTTPException with a specific envelope code (for example INSUFFICIENT_HISTORY)."""

    def __init__(self, status_code, code, message):
        super().__init__(status_code, message)
        self.code = code


def _field_name(loc):
    parts = [str(part) for part in (loc or []) if part not in ("body", "query", "path", "header", "form")]
    return ".".join(parts)


def _clean_message(message):
    return str(message).removeprefix("Value error, ")


def error_response(status_code, *, code=None, message=None, fields=None, detail=None, headers=None):
    body = {"error": {"code": code or ERROR_CODES.get(status_code, "REQUEST_FAILED"),
                      "message": message or GENERIC_MESSAGES.get(status_code, "Request failed. Please try again."),
                      "fields": fields or {}}}
    if detail is not None:
        body["detail"] = detail
    return JSONResponse(status_code=status_code, content=body, headers=headers)


def _validation_details(errors):
    # Never echo submitted values or validation context (raw input stays out of responses).
    return [{"loc": list(error["loc"]), "msg": _clean_message(error["msg"]), "type": error["type"]} for error in errors]


def _fields(details):
    fields = {}
    for item in details:
        name = _field_name(item.get("loc"))
        if name and name not in fields:
            fields[name] = item.get("msg", "Invalid value")
    return fields


@app.exception_handler(RequestValidationError)
async def request_validation_error(request, exc):
    details = _validation_details(exc.errors())
    first = details[0]["msg"] if len(details) == 1 else None
    return error_response(400, message=first, fields=_fields(details), detail=details)


@app.exception_handler(StarletteHTTPException)
async def http_error(request, exc):
    status_code = 400 if exc.status_code == 422 else exc.status_code
    detail = exc.detail
    if isinstance(detail, list):
        return error_response(status_code, code=getattr(exc, "code", None), fields=_fields(detail), detail=detail,
                              message=detail[0].get("msg") if len(detail) == 1 else None)
    message = None if status_code == 401 else detail
    return error_response(status_code, code=getattr(exc, "code", None), message=message, detail=detail,
                          headers=getattr(exc, "headers", None))


@app.exception_handler(psycopg.Error)
async def database_error(request, exc):
    logger.error("database operation failed", extra={"errorCategory": type(exc).__name__})
    return error_response(500, detail=GENERIC_MESSAGES[500])


def extraction_validation_error(field, message):
    return HTTPException(400, detail=[{
        "loc": ["body", field] if field else ["body"],
        "msg": message, "type": "value_error",
    }])


def _json_safe(value):
    """Exact decimals leave the API as strings (see docs/INTEGRATION_DECISIONS.md, D-07)."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (date, UUID)):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


# ---- Request models --------------------------------------------------------------------------
def _two_decimals(value: Decimal, label: str) -> Decimal:
    if value != value.quantize(Decimal("0.01")):
        raise ValueError(f"{label} can have at most 2 decimal places")
    return value


class SalesLine(BaseModel):
    """One sales row (FR-202 as clarified in M3 section 3; M3 7.2 limits)."""
    itemName: str = Field(min_length=1)
    quantity: Decimal = Field(gt=0, le=MAX_QUANTITY, allow_inf_nan=False)
    unitPrice: Decimal = Field(ge=0, le=MAX_UNIT_PRICE, allow_inf_nan=False)

    @field_validator("itemName")
    @classmethod
    def valid_item(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Item name cannot be blank")
        if len(value) > MAX_ITEM_CHARS:
            raise ValueError(f"Item name can have at most {MAX_ITEM_CHARS} characters")
        return value

    @field_validator("quantity")
    @classmethod
    def quantity_places(cls, value):
        return _two_decimals(value, "Quantity")

    @field_validator("unitPrice")
    @classmethod
    def price_places(cls, value):
        return _two_decimals(value, "Unit price")


class ConfirmLine(SalesLine):
    draftLineId: int | str | None = None
    source: Literal["AI_EXTRACTED", "MANUAL"] | None = None
    verifiedFields: list[str] = Field(default_factory=list)

    @field_validator("draftLineId", mode="before")
    @classmethod
    def valid_link(cls, value):
        if value is None:
            return None
        if isinstance(value, bool) or (isinstance(value, int) and value < 1) or \
                (isinstance(value, str) and not value.strip()) or not isinstance(value, (int, str)):
            raise ValueError("Use the draft row's line ID")
        return value

    @field_validator("verifiedFields")
    @classmethod
    def valid_verified_fields(cls, value):
        if set(value) - {"itemName", "quantity", "unitPrice"}:
            raise ValueError("Unknown verified field")
        return value


class ConfirmRequest(BaseModel):
    submissionId: UUID
    lines: list[ConfirmLine] = Field(min_length=1, max_length=100)


class ManualEntryRequest(BaseModel):
    submissionId: UUID
    saleDate: IsoDate
    lines: list[SalesLine] = Field(min_length=1, max_length=100)

    @field_validator("submissionId")
    @classmethod
    def version_four(cls, value):
        if value.version != 4:
            raise ValueError("Use a UUID v4 submission ID generated once per form")
        return value


class SalesRecordResponse(BaseModel):
    recordId: UUID
    submissionId: UUID
    saleDate: date
    lineNo: int
    itemName: str
    quantity: Decimal
    unitPrice: Decimal
    lineTotal: Decimal
    source: str


class SalesHistoryResponse(BaseModel):
    start: date
    end: date
    records: list[SalesRecordResponse]


class OpenSubmission(BaseModel):
    submissionId: UUID
    status: str
    inputType: str
    saleDate: date
    createdAt: str
    lineCount: int


class GuidanceRequest(BaseModel):
    asOf: IsoDate | None = None


def _check_sale_date(sale_date, clock, field_loc):
    today = clock.today()
    if sale_date > today:
        raise HTTPException(400, detail=[{"loc": field_loc, "msg": "Sale date cannot be in the future.", "type": "value_error"}])
    if sale_date < today - timedelta(days=MAX_PAST_DAYS):
        raise HTTPException(400, detail=[{"loc": field_loc, "msg": "Sale date cannot be more than 365 days ago.", "type": "value_error"}])


# ---- Routes ----------------------------------------------------------------------------------
@app.get("/health")
def health():
    """Render health check. 200 only once migrations have run and the database answers."""
    try:
        with connect() as conn:
            conn.execute("SELECT 1")
    except (psycopg.Error, KeyError, ValueError):
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return {"status": "ok"}


def _signed_image_url(storage, image_key):
    if not image_key:
        return None
    try:
        return storage.get_signed_url(image_key)
    except StorageError:
        return None  # The draft is still usable without the preview.


def _capture_response(submission_id, storage):
    """ExtractResponse / SubmissionDetail: Gabriel's draft view plus José's capture inputs."""
    detail = repository.get_submission(submission_id)
    capture = repository.get_capture_input(submission_id)
    return _json_safe({**detail, "inputType": capture["input_type"], "maskedText": capture["masked_text"],
                       "imageUrl": _signed_image_url(storage, capture["image_key"])})


# A sync route: FastAPI runs it on a worker thread, so the up-to-61 s provider wait
# (30 s, 1 s pause, 30 s) never blocks the event loop.
@app.post("/api/v1/extract", responses={
    400: {"description": "Invalid extraction input, including missing or malformed form fields"},
    503: {"description": "The photo could not be stored; nothing was created and no AI call was made"},
    "4XX": {"description": "Input or access error"},
})
def extract(
    saleDate: date = Form(...),
    file: UploadFile | None = File(default=None),
    text: str | None = Form(default=None),
    _: None = Depends(require_owner_access),
    clock: Clock = Depends(get_clock),
    model: VisionModel = Depends(get_vision_model),
    storage=Depends(get_object_storage),
):
    # 1. Validate everything before anything is stored or sent (M3 validateInput).
    if (file is None) == (text is None):
        raise extraction_validation_error(None, "Provide exactly one image file or text input.")

    image = None
    if file is not None:
        image = file.file.read(privacy.MAX_IMAGE_BYTES + 1)
        if len(image) > privacy.MAX_IMAGE_BYTES or privacy.detect_image_type(image) is None:
            raise extraction_validation_error("file", "Choose a JPEG or PNG photo under 10 MB.")
    else:
        text = text.strip()
        problem = privacy.text_problem(text)
        if problem:
            raise extraction_validation_error("text", problem)

    _check_sale_date(saleDate, clock, ["body", "saleDate"])

    service = InferenceService(model)
    if image is not None:
        # 2a. Photo: strip metadata, store only the sanitized copy, then create the submission.
        try:
            sanitized = privacy.sanitize_image(image)
        except privacy.InvalidImageError as exc:
            raise extraction_validation_error("file", str(exc)) from None
        del image  # The original bytes (with EXIF/GPS) are not kept.
        try:
            image_key = storage.put_object(sanitized, "image/jpeg")
        except StorageError:
            raise ApiError(503, "STORAGE_UNAVAILABLE", "We could not save your photo. Please try again.") from None
        try:
            submission_id = repository.create_submission(saleDate, image_key=image_key, input_type="IMAGE")
        except Exception:
            try:
                storage.delete_object(image_key)
            except StorageError:
                logger.warning("orphaned image could not be deleted")
            raise
        service.extract_from_image(submission_id, sanitized)
    else:
        # 2b. Text: mask phones/emails first; only the masked text is stored or sent.
        masked = privacy.mask_text(text)
        submission_id = repository.create_submission(saleDate, input_type="TEXT", masked_text=masked)
        service.extract_from_text(submission_id, masked)

    return _capture_response(submission_id, storage)


@app.post("/api/v1/confirm", status_code=status.HTTP_201_CREATED)
def confirm(request: ConfirmRequest, response: Response, _: None = Depends(require_owner_access),
            clock: Clock = Depends(get_clock)):
    ids, created = repository.save_sales_records(request, today=clock.today())
    response.status_code = 201 if created else 200
    return {"submissionId": request.submissionId, "recordIds": ids, "created": created}


@app.post("/api/v1/manual-entries", status_code=status.HTTP_201_CREATED)
def manual_entry(request: ManualEntryRequest, response: Response, _: None = Depends(require_owner_access),
                 clock: Clock = Depends(get_clock)):
    """Standalone manual entry (FR-104, SD-04): no AI request; the submission is created CONFIRMED."""
    _check_sale_date(request.saleDate, clock, ["body", "saleDate"])
    ids, created = repository.create_manual_submission(request.submissionId, request.saleDate, request.lines)
    response.status_code = 201 if created else 200
    return {"submissionId": request.submissionId, "recordIds": ids, "created": created}


@app.get("/api/v1/submissions", response_model=list[OpenSubmission])
def open_submissions(_: None = Depends(require_owner_access)):
    """Pending drafts (FR-216): NEEDS_VERIFICATION, AI_UNAVAILABLE and REJECTED, newest first."""
    return _json_safe(repository.list_open_submissions())


@app.get("/api/v1/submissions/{submission_id}")
def submission(submission_id: UUID, _: None = Depends(require_owner_access),
               storage=Depends(get_object_storage)):
    return _capture_response(submission_id, storage)


@app.delete("/api/v1/submissions/{submission_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_submission(submission_id: UUID, _: None = Depends(require_owner_access),
                      storage=Depends(get_object_storage)):
    """Discard an open draft or delete a confirmed submission (FR-216, FR-304, M3 7.4)."""
    deleted = repository.delete_submission(submission_id)
    # The image was queued in the same transaction; remove it now, or on the next sweep.
    try:
        retention.process_image_deletions(storage, limit=10)
    except psycopg.Error:
        logger.warning("image deletion deferred to the next retention sweep")
    return Response(status_code=204, headers={"X-Records-Deleted": str(deleted)})


class DeleteAllRequest(BaseModel):
    confirmation: str


@app.post("/api/v1/data/delete-all")
def delete_all_data(request: DeleteAllRequest, _: None = Depends(require_owner_access),
                    storage=Depends(get_object_storage)):
    """FR-304 / SRS AC-21: delete every sales record and all raw input after the owner types DELETE.

    Additive to M3 4.2 (which adds per-submission DELETE); any other confirmation deletes nothing.
    """
    if request.confirmation != "DELETE":
        raise HTTPException(400, detail=[{"loc": ["body", "confirmation"], "type": "value_error",
                                          "msg": "Type DELETE to confirm. Nothing was deleted."}])
    records, submissions = repository.delete_all_owner_data()
    try:
        retention.process_image_deletions(storage)
    except psycopg.Error:
        logger.warning("image deletion deferred to the next retention sweep")
    noun = "record" if records == 1 else "records"
    return {"recordsDeleted": records, "submissionsDeleted": submissions, "message": f"{records} {noun} deleted"}


@app.get("/api/v1/sales", response_model=SalesHistoryResponse)
def sales(start: IsoDate | None = Query(default=None), end: IsoDate | None = Query(default=None),
          _: None = Depends(require_owner_access), clock: Clock = Depends(get_clock)):
    """Confirmed sales by date (FR-303). Default: the last 28 days; range at most 366 days."""
    end = end or clock.today()
    start = start or end - timedelta(days=27)
    if start > end:
        raise HTTPException(400, detail=[{"loc": ["query", "start"], "msg": "Start date must be on or before the end date.", "type": "value_error"}])
    if (end - start).days + 1 > MAX_HISTORY_RANGE_DAYS:
        raise HTTPException(400, detail=[{"loc": ["query", "start"], "msg": "Choose a date range of at most 366 days.", "type": "value_error"}])
    return {"start": start, "end": end, "records": repository.get_confirmed_records(start, end)}


@app.get("/api/v1/analysis", response_model=AnalysisResponse)
def analysis(
    asOf: IsoDate | None = None,
    _: None = Depends(require_owner_access),
    clock: Clock = Depends(get_clock),
):
    return BaselineAnalyzer().analyze(resolve_as_of(asOf, clock, ["query", "asOf"]), save_snapshots=True)


# A sync route: FastAPI runs it on a worker thread, so waiting on a provider never blocks the event loop.
@app.post("/api/v1/guidance", response_model=GuidanceResponse)
def guidance(
    request: GuidanceRequest,
    _: None = Depends(require_owner_access),
    clock: Clock = Depends(get_clock),
    provider: WordingProvider = Depends(get_wording_provider),
):
    as_of = resolve_as_of(request.asOf, clock, ["body", "asOf"])
    analysis_result = BaselineAnalyzer().analyze(as_of, save_snapshots=True)
    if not any(estimate.suggestedQty is not None for estimate in analysis_result.estimates):
        # M3 4.2: 409 INSUFFICIENT_HISTORY; no provider is called (FR-503).
        raise ApiError(409, "INSUFFICIENT_HISTORY",
                       f"Log at least 7 days of sales to get advice ({analysis_result.loggedDays} of 7 so far).")
    return GuidanceEngine(provider).generate(analysis_result)


def _openapi_with_400_validation():
    """FastAPI documents request validation as 422; this API returns 400 (M3 4.2), so say so."""
    if app.openapi_schema:
        return app.openapi_schema
    from fastapi.openapi.utils import get_openapi
    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    for path in schema.get("paths", {}).values():
        for operation in path.values():
            responses = operation.get("responses", {})
            if "422" in responses:
                moved = responses.pop("422")
                responses.setdefault("400", {**moved, "description": "Validation error"})
    app.openapi_schema = schema
    return schema


app.openapi = _openapi_with_400_validation


@app.middleware("http")
async def no_store_api(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")  # Sales data is never cached.
    # NFR-711: browsers must keep using HTTPS (ignored on plain-HTTP local development).
    response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response
