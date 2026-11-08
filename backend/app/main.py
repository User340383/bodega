
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID
from typing import Literal

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
    Query,
    Response,
)
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from contextlib import asynccontextmanager
import logging
import psycopg
from . import privacy, repository
from .ingestion import InferenceService
from .storage import StorageError, get_object_storage
from .vision import VisionModel, get_vision_model
from .analysis import AnalysisResponse, BaselineAnalyzer, IsoDate, resolve_as_of
from .clock import Clock, get_clock
from .database import migrate
from .guidance import GuidanceEngine, GuidanceResponse
from .llm import WordingProvider, get_wording_provider
from .security import FailedAttemptLimiter, allowed_origins, check_required_settings, require_owner_access

@asynccontextmanager
async def lifespan(app):
    check_required_settings()
    app.state.access_limiter = FailedAttemptLimiter()
    migrate()
    yield


app = FastAPI(title="Bodega API", version="0.1.0", lifespan=lifespan)
# Production serves the SPA from its own Render service, so FRONTEND_URL is the only allowed origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.exception_handler(psycopg.Error)
async def database_error(request, exc):
    logging.getLogger(__name__).error("Database operation failed (%s)", type(exc).__name__)
    return JSONResponse(status_code=500, content={"detail": "Sales could not be saved or loaded. Your draft is unchanged; please try again."})


@app.exception_handler(RequestValidationError)
async def request_validation_error(request, exc):
    if request.method == "POST" and request.url.path == "/api/v1/extract":
        # Do not echo raw input or validation context into the response.
        details = [{key: error[key] for key in ("loc", "msg", "type")} for error in exc.errors()]
        return JSONResponse(status_code=400, content={"detail": details})
    return await request_validation_exception_handler(request, exc)


def extraction_validation_error(field, message):
    return HTTPException(400, detail=[{
        "loc": ["body", field] if field else ["body"],
        "msg": message, "type": "value_error",
    }])


class ConfidenceBands(BaseModel):
    itemName: str = "UNSCORED"
    quantity: str = "UNSCORED"
    unitPrice: str = "UNSCORED"


class ConfirmLine(BaseModel):
    draftLineId: str | None = Field(default=None, min_length=1)
    source: Literal["AI_EXTRACTED", "MANUAL"] | None = None
    itemName: str = Field(min_length=1)
    quantity: Decimal = Field(gt=0, allow_inf_nan=False)
    unitPrice: Decimal = Field(ge=0, allow_inf_nan=False)
    verifiedFields: list[str] = Field(default_factory=list)

    @field_validator("itemName")
    @classmethod
    def nonblank_item(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Item name cannot be blank")
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


class SalesRecordResponse(BaseModel):
    id: UUID
    submissionId: UUID
    itemName: str
    quantity: Decimal
    unitPrice: Decimal
    saleDate: date
    source: str


class SalesHistoryResponse(BaseModel):
    records: list[SalesRecordResponse]
    limit: int
    offset: int


class GuidanceRequest(BaseModel):
    asOf: IsoDate | None = None


@app.get("/health")
def health():
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
    return {**detail, "inputType": capture["input_type"], "maskedText": capture["masked_text"],
            "imageUrl": _signed_image_url(storage, capture["image_key"])}


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
        if not text or len(text) > privacy.MAX_TEXT_CHARS:
            raise extraction_validation_error("text", "Text must contain 1-5,000 characters.")

    today = clock.today()
    if saleDate > today:
        raise extraction_validation_error("saleDate", "Sale date cannot be in the future.")
    if saleDate < today - timedelta(days=365):
        raise extraction_validation_error("saleDate", "Sale date cannot be more than 365 days ago.")

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
            raise HTTPException(503, "We could not save your photo. Please try again.") from None
        try:
            submission_id = repository.create_submission(saleDate, image_key=image_key, input_type="IMAGE")
        except Exception:
            try:
                storage.delete_object(image_key)
            except StorageError:
                logging.getLogger(__name__).warning("orphaned image could not be deleted")
            raise
        service.extract_from_image(submission_id, sanitized)
    else:
        # 2b. Text: mask phones/emails first; only the masked text is stored or sent.
        masked = privacy.mask_text(text)
        submission_id = repository.create_submission(saleDate, input_type="TEXT", masked_text=masked)
        service.extract_from_text(submission_id, masked)

    return _capture_response(submission_id, storage)


@app.post("/api/v1/confirm", status_code=status.HTTP_201_CREATED)
def confirm(request: ConfirmRequest, response: Response, _: None = Depends(require_owner_access)):
    ids, created = repository.save_sales_records(request)
    response.status_code = 201 if created else 200
    return {"submissionId": request.submissionId, "recordIds": ids, "created": created}


@app.get("/api/v1/submissions/{submission_id}")
def submission(submission_id: UUID, _: None = Depends(require_owner_access),
               storage=Depends(get_object_storage)):
    return _capture_response(submission_id, storage)


@app.post("/api/v1/submissions/{submission_id}/manual-review")
def manual_review(submission_id: UUID, _: None = Depends(require_owner_access)):
    repository.start_manual_review(submission_id)
    return repository.get_submission(submission_id)


@app.get("/api/v1/sales", response_model=SalesHistoryResponse)
def sales(limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0),
          _: None = Depends(require_owner_access)):
    return {"records": repository.get_confirmed_records(limit, offset), "limit": limit, "offset": offset}


@app.get("/api/v1/analysis", response_model=AnalysisResponse)
def analysis(
    asOf: IsoDate | None = None,
    _: None = Depends(require_owner_access),
    clock: Clock = Depends(get_clock),
):
    return BaselineAnalyzer().analyze(resolve_as_of(asOf, clock, ["query", "asOf"]))


# A sync route: FastAPI runs it on a worker thread, so waiting on a provider never blocks the event loop.
@app.post("/api/v1/guidance", response_model=GuidanceResponse)
def guidance(
    request: GuidanceRequest,
    _: None = Depends(require_owner_access),
    clock: Clock = Depends(get_clock),
    provider: WordingProvider = Depends(get_wording_provider),
):
    as_of = resolve_as_of(request.asOf, clock, ["body", "asOf"])
    return GuidanceEngine(provider).generate(BaselineAnalyzer().analyze(as_of))
