
import os
from datetime import date
from decimal import Decimal
from uuid import UUID
from typing import Literal

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    UploadFile,
    status,
    Query,
    Response,
)
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from contextlib import asynccontextmanager
import logging
import psycopg
from . import repository
from .analysis import AnalysisResponse, BaselineAnalyzer, IsoDate, resolve_as_of
from .clock import Clock, get_clock
from .database import migrate
from .guidance import GuidanceEngine, GuidanceResponse
from .llm import WordingProvider, get_wording_provider

@asynccontextmanager
async def lifespan(app):
    migrate()
    yield


app = FastAPI(title="Bodega API", version="0.1.0", lifespan=lifespan)


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


def require_owner_access(
    authorization: str | None = Header(default=None),
):
    expected_code = os.getenv("APP_ACCESS_CODE")

    if not expected_code:
        raise HTTPException(
            status_code=503,
            detail="API access code is not configured.",
        )

    if authorization != f"Bearer {expected_code}":
        raise HTTPException(status_code=401, detail="Unauthorized")


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


@app.post("/api/v1/extract", responses={
    400: {"description": "Invalid extraction input, including missing or malformed form fields"},
    "4XX": {"description": "Input or access error"},
})
async def extract(
    saleDate: date = Form(...),
    file: UploadFile | None = File(default=None),
    text: str | None = Form(default=None),
    _: None = Depends(require_owner_access),
):
    if (file is None) == (text is None):
        raise extraction_validation_error(None, "Provide exactly one image file or text input.")

    if file is not None:
        if file.content_type not in ("image/jpeg", "image/png"):
            raise extraction_validation_error("file", "Only JPEG and PNG images are accepted.")

        contents = await file.read(10 * 1024 * 1024 + 1)
        if len(contents) > 10 * 1024 * 1024:
            raise extraction_validation_error("file", "Image must not exceed 10 MiB.")

    elif not text or not text.strip() or len(text) > 5000:
        raise extraction_validation_error("text", "Text must contain 1-5,000 characters.")

    if saleDate > date.today():
        raise extraction_validation_error("saleDate", "Sale date cannot be in the future")
    # Integration boundary only: no AI extraction, raw-input storage or fabricated rows.
    submission_id = repository.create_submission(saleDate)
    repository.update_status(submission_id, "CLASSIFYING")
    repository.update_status(submission_id, "AI_UNAVAILABLE")
    return {
        "submissionId": str(submission_id), "status": "AI_UNAVAILABLE",
        "inputType": "IMAGE" if file is not None else "TEXT",
        "saleDate": saleDate.isoformat(),
        "message": "Extraction is not implemented. Enter reviewed sales rows manually.",
        "manualEntry": True, "imageUrl": None, "maskedText": None, "lines": [],
    }


@app.post("/api/v1/confirm", status_code=status.HTTP_201_CREATED)
def confirm(request: ConfirmRequest, response: Response, _: None = Depends(require_owner_access)):
    ids, created = repository.save_sales_records(request)
    response.status_code = 201 if created else 200
    return {"submissionId": request.submissionId, "recordIds": ids, "created": created}


@app.get("/api/v1/submissions/{submission_id}")
def submission(submission_id: UUID, _: None = Depends(require_owner_access)):
    return repository.get_submission(submission_id)


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
