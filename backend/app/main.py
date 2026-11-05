
import os
import asyncio
import json
import time
from datetime import datetime, timezone
from uuid import uuid4
from datetime import date, timedelta
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
from pydantic import BaseModel, Field, field_validator, ValidationError, ConfigDict
from contextlib import asynccontextmanager
import logging
import psycopg
from . import repository
from .database import connect, migrate
from .storage import ObjectStorage, StorageError
from .clock import today
from . import data_lifecycle
from . import image_cleanup
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.encoders import jsonable_encoder


class DecimalJSONResponse(Response):
    """Emit exact decimal JSON number tokens without a binary-float conversion."""
    media_type='application/json'

    def render(self, content):
        def encode(value):
            if isinstance(value, Decimal):
                if not value.is_finite():
                    raise ValueError('Nonfinite JSON number')
                return format(value,'f')
            if isinstance(value, dict):
                return '{'+','.join(json.dumps(str(k))+':'+encode(v) for k,v in value.items())+'}'
            if isinstance(value, (list,tuple)):
                return '['+','.join(encode(v) for v in value)+']'
            if isinstance(value, (UUID,date,datetime)):
                return json.dumps(str(value))
            return json.dumps(value,ensure_ascii=False,allow_nan=False)
        return encode(content).encode('utf-8')


def object_storage():
    return getattr(app.state, 'object_storage', None) or (ObjectStorage() if os.getenv('S3_BUCKET') else None)


async def cleanup_loop():
    while True:
        await asyncio.sleep(86400)
        try:
            await asyncio.to_thread(data_lifecycle.sweep, object_storage())
        except (StorageError, psycopg.Error):
            logging.getLogger(__name__).error('Object cleanup unavailable')

@asynccontextmanager
async def lifespan(app):
    migrate()
    try:
        await asyncio.to_thread(data_lifecycle.sweep, object_storage())
    except (StorageError, psycopg.Error):
        logging.getLogger(__name__).error('Startup object cleanup deferred')
    task = asyncio.create_task(cleanup_loop())
    try:
        yield
    finally:
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="Bodega API", version="0.1.0", lifespan=lifespan)


@app.middleware('http')
async def safe_request_log(request, call_next):
    started = time.monotonic()
    request_id = str(uuid4())
    response = await call_next(request)
    response.headers['X-Request-ID'] = request_id
    if response.status_code >= 400:
        logging.getLogger(__name__).warning(json.dumps({
            'timestamp': datetime.now(timezone.utc).isoformat(), 'requestId': request_id,
            'component': 'FastAPI', 'operation': getattr(request.scope.get('route'), 'name', 'unmatched'),
            'errorCategory': f'HTTP_{response.status_code}', 'durationMs': round((time.monotonic()-started)*1000, 2)}))
    return response


@app.exception_handler(StorageError)
async def storage_error(request, exc):
    logging.getLogger(__name__).error('Object storage operation failed')
    return error_response(503,'Image storage is unavailable. Retry; your saved sales are retained.')


@app.exception_handler(psycopg.Error)
async def database_error(request, exc):
    logging.getLogger(__name__).error("Database operation failed (%s)", type(exc).__name__)
    return error_response(500,'Your sales could not be saved or loaded. Please try again.')


def error_response(status_code, message, fields=None, detail=None):
    codes={400:'VALIDATION_ERROR',401:'UNAUTHORIZED',403:'FORBIDDEN',404:'NOT_FOUND',409:'INVALID_STATE',429:'RATE_LIMITED',500:'DATABASE_ERROR',503:'STORAGE_UNAVAILABLE'}
    return JSONResponse(status_code=status_code,content={'error':{'code':codes.get(status_code,'INTERNAL_ERROR'),'message':message,'fields':fields or {}}})

@app.exception_handler(StarletteHTTPException)
async def http_error(request, exc):
    code=400 if exc.status_code==422 else exc.status_code
    fields={}
    if isinstance(exc.detail,list):
        fields={'.'.join(str(v) for v in e.get('loc',[])[1:]):e.get('msg','Invalid value') for e in exc.detail}
    return error_response(code,exc.detail if isinstance(exc.detail,str) else 'Check the highlighted fields and try again.',fields,exc.detail)

@app.exception_handler(RequestValidationError)
async def request_validation_error(request, exc):
    details=[{key:e[key] for key in ('loc','msg','type')} for e in exc.errors()]
    return error_response(400,'Check the highlighted fields and try again.',
        {'.'.join(str(v) for v in e['loc'][1:]):e['msg'] for e in details},details)


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
    model_config = ConfigDict(extra='forbid')
    draftLineId: int | None = Field(default=None, strict=True, ge=1)
    itemName: str = Field(min_length=1)
    quantity: Decimal = Field(gt=0, le=Decimal("99999.99"), decimal_places=2, allow_inf_nan=False)
    unitPrice: Decimal = Field(ge=0, le=Decimal("9999999.99"), decimal_places=2, allow_inf_nan=False)
    verifiedFields: list[str] = Field(default_factory=list)

    @field_validator('draftLineId', mode='before')
    @classmethod
    def valid_draft_id(cls, value):
        if isinstance(value, bool) or (isinstance(value, int) and value < 1) or (isinstance(value, str) and not value.strip()):
            raise ValueError('Use a valid draft line identifier')
        return value

    @field_validator("itemName")
    @classmethod
    def nonblank_item(cls, value):
        value = ' '.join(value.split())
        if not value or len(value)>100:
            raise ValueError("Item name must contain 1 to 100 characters")
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


class ConfirmResponse(BaseModel):
    submissionId: UUID
    recordIds: list[UUID]
    created: bool


class ManualRequest(BaseModel):
    saleDate: date

    @field_validator('saleDate')
    @classmethod
    def not_future(cls, value):
        if value > today() or value < today()-timedelta(days=365):
            raise ValueError('Sale date cannot be in the future')
        return value


class DeleteRequest(BaseModel):
    confirmation: Literal['DELETE']


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
    try:
        with connect() as conn:
            conn.execute("SELECT 1")
    except psycopg.Error:
        return JSONResponse(status_code=503, content={"status": "unavailable"})
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
def confirm(request: dict, response: Response, _: None = Depends(require_owner_access)):
    try:
        sid=UUID(str(request.get('submissionId')))
    except ValueError:
        raise HTTPException(400,'Provide a valid submissionId')
    saved=repository.get_submission(sid)
    if saved['status']=='CONFIRMED':
        parsed=ConfirmRequest.model_construct(submissionId=sid,lines=[])
    else:
        try:
            parsed=ConfirmRequest.model_validate(request)
        except ValidationError as exc:
            raise HTTPException(400,detail=[{k:e[k] for k in ('loc','msg','type')} for e in exc.errors()])
    ids, created = repository.save_sales_records(parsed)
    response.status_code = 201 if created else 200
    return {"submissionId": sid, "recordIds": ids, "created": created}


@app.get("/api/v1/submissions/{submission_id}")
def submission(submission_id: UUID, _: None = Depends(require_owner_access)):
    result = repository.get_submission(submission_id)
    key = repository.get_image_key(submission_id)
    storage = object_storage() if key else None
    records=repository.get_confirmed_records_between(result['saleDate'],result['saleDate']) if result['status']=='CONFIRMED' else []
    detail={**result, 'imageUrl': storage.signed_url(key) if storage else None}
    if result['status']=='CONFIRMED':
        detail['records']=[public_record(r) for r in records if r['submissionId']==submission_id]
    return DecimalJSONResponse(public_draft(detail))


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


class ManualEntryRequest(ManualRequest):
    submissionId: UUID
    lines: list[ConfirmLine] = Field(min_length=1,max_length=100)

@app.post('/api/v1/manual-entries',response_model=ConfirmResponse)
def final_manual(request: ManualEntryRequest,response: Response,_: None=Depends(require_owner_access)):
    if request.submissionId.version != 4: raise HTTPException(400,'Use a UUID v4 submission identifier')
    ids,created=data_lifecycle.manual_entry(request)
    response.status_code=201 if created else 200
    return {'submissionId':request.submissionId,'recordIds':ids,'created':created}

@app.get('/api/v1/submissions')
def pending_drafts(_: None=Depends(require_owner_access)):
    return data_lifecycle.list_open()

@app.delete('/api/v1/submissions/{submission_id}',status_code=204)
def delete_one(submission_id: UUID,_: None=Depends(require_owner_access)):
    storage=object_storage()
    key=data_lifecycle.delete_submission(submission_id, bucket_name=storage.bucket if storage else None)
    if key:
        try:
            image_cleanup.process_pending(storage, image_key=key)
        except (StorageError, psycopg.Error):
            logging.getLogger(__name__).error('Deleted submission image cleanup deferred')
    return Response(status_code=204)

@app.get('/api/v1/sales')
def final_sales(start: date | None=None,end: date | None=None,_: None=Depends(require_owner_access)):
    end=end or today(); start=start or end-timedelta(days=27)
    if start>end or (end-start).days>365: raise HTTPException(400,'Choose an ordered date range of at most 366 days')
    rows=repository.get_confirmed_records_between(start,end)
    rows.sort(key=lambda r:(-r['saleDate'].toordinal(),r['lineNo'],str(r['submissionId'])))
    result=[public_record(r) for r in rows]
    # Decimal values are JSON numbers in the final contract. Stored values remain exact.
    return DecimalJSONResponse(result)


def public_record(row):
    return {'recordId':row['id'],'submissionId':row['submissionId'],'saleDate':row['saleDate'],
            'itemName':row['itemName'],'quantity':row['quantity'],'unitPrice':row['unitPrice'],
            'lineTotal':row['quantity']*row['unitPrice'],'source':row['source']}


def public_draft(value):
    result={**value,'lines':[dict(row) for row in value.get('lines',[])]}
    for row in result['lines']:
        for field in ('quantity','unitPrice'):
            if row.get(field) is not None:
                try:
                    number=Decimal(str(row[field]))
                    row[field]=number if number.is_finite() else None
                except Exception:
                    row[field]=None
    return result
