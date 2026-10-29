
import os
from datetime import date
from decimal import Decimal
from uuid import uuid4

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field

app = FastAPI(title="Bodega API", version="0.1.0")


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
    draftLineId: str | None = None
    itemName: str = Field(min_length=1)
    quantity: Decimal = Field(gt=0)
    unitPrice: Decimal = Field(ge=0)


class ConfirmRequest(BaseModel):
    submissionId: str
    lines: list[ConfirmLine] = Field(min_length=1, max_length=100)


class GuidanceRequest(BaseModel):
    asOf: date | None = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/v1/extract")
async def extract(
    saleDate: date = Form(...),
    file: UploadFile | None = File(default=None),
    text: str | None = Form(default=None),
    _: None = Depends(require_owner_access),
):
    if (file is None) == (text is None):
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Provide exactly one image file or text input.",
                    "fields": {},
                }
            },
        )

    if file is not None:
        if file.content_type not in ("image/jpeg", "image/png"):
            raise HTTPException(
                status_code=400,
                detail="Only JPEG and PNG images are accepted.",
            )

        contents = await file.read(10 * 1024 * 1024 + 1)
        if len(contents) > 10 * 1024 * 1024:
            raise HTTPException(
                status_code=400,
                detail="Image must not exceed 10 MiB.",
            )

    elif not text or len(text) > 5000:
        raise HTTPException(
            status_code=400,
            detail="Text must contain 1–5,000 characters.",
        )

    return {
        "submissionId": str(uuid4()),
        "status": "NEEDS_VERIFICATION",
        "inputType": "IMAGE" if file is not None else "TEXT",
        "saleDate": saleDate.isoformat(),
        "message": "Placeholder response: extraction is not implemented.",
        "manualEntry": False,
        "imageUrl": None,
        "maskedText": "Mock text input" if text is not None else None,
        "lines": [
            {
                "lineId": str(uuid4()),
                "itemName": "Sample item",
                "quantity": 1,
                "unitPrice": 1.0,
                "bands": {
                    "itemName": "UNSCORED",
                    "quantity": "UNSCORED",
                    "unitPrice": "UNSCORED",
                },
            }
        ],
    }


@app.post("/api/v1/confirm", status_code=status.HTTP_201_CREATED)
def confirm(
    request: ConfirmRequest,
    _: None = Depends(require_owner_access),
):
    return {
        "submissionId": request.submissionId,
        "recordIds": [str(uuid4()) for _ in request.lines],
        "created": True,
    }


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
