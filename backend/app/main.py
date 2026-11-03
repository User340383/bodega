
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
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from contextlib import asynccontextmanager
import logging
import psycopg
from . import repository
from .database import migrate

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
    # Keep one field-error shape across routes and avoid echoing submitted values.
    details = [{key: error[key] for key in ("loc", "msg", "type")} for error in exc.errors()]
    is_extraction = request.method == "POST" and request.url.path == "/api/v1/extract"
    return JSONResponse(status_code=400 if is_extraction else 422, content={"detail": details})


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
    verifiedFields: list[str] = Field(
        default_factory=list,
        description="Fields the owner explicitly reviewed without changing; required for unchanged non-HIGH-confidence draft values.",
    )

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
    lines: list[ConfirmLine] = Field(
        min_length=1, max_length=100,
        description="Confirmed sales rows. The existing wire name is retained for compatibility.",
    )


class ConfirmResponse(BaseModel):
    submissionId: UUID
    recordIds: list[UUID]
    created: bool


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
    asOf: date | None = None


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


@app.post("/api/v1/confirm", status_code=status.HTTP_201_CREATED, response_model=ConfirmResponse)
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
def sales(startDate: date | None = Query(default=None, description="Inclusive first sale date; omitted means no lower date bound."),
          endDate: date | None = Query(default=None, description="Inclusive last sale date; omitted means no upper date bound."),
          limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0),
          _: None = Depends(require_owner_access)):
    if startDate is not None and endDate is not None and startDate > endDate:
        raise HTTPException(422, detail=[{
            "loc": ["query", "endDate"],
            "msg": "endDate must be on or after startDate",
            "type": "value_error",
        }])
    return {"records": repository.get_confirmed_records(limit, offset, start_date=startDate, end_date=endDate), "limit": limit, "offset": offset}


@app.get("/api/v1/analysis")
def analysis(
    asOf: date | None = None,
    _: None = Depends(require_owner_access),
):
    return {
        "asOf": (asOf or date.today()).isoformat(),
        "loggedDays": 0,
        "requiredDays": 7,
        "status": "INSUFFICIENT_HISTORY",
        "estimates": [],
    }


@app.post("/api/v1/guidance")
def guidance(
    request: GuidanceRequest,
    _: None = Depends(require_owner_access),
):
    return {
        "items": [],
        "source": "TEMPLATE",
        "groundingPassed": False,
        "failedRule": "MOCK_PLACEHOLDER",
        "stockNotice": None,
    }
