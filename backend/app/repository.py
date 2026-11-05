"""Gabriel's persistence boundary. Connection contexts commit or roll back all writes."""
import os
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4, uuid5

from fastapi import HTTPException
from psycopg.rows import dict_row

from .database import connect
from .clock import today
from .lifecycle import TRANSITIONS, SubmissionStatus, confidence_band, extraction_status


def owner_id():
    return UUID(os.environ["OWNER_ID"])


def _normalize_draft(submission_id, payload):
    """Preserve supplied IDs; give legacy/missing IDs stable submission-scoped values."""
    if payload is None:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get('lines', []), list):
        raise HTTPException(400, "Draft lines must be a list")
    used = set()
    for line in payload.get('lines', []):
        if not isinstance(line, dict):
            raise HTTPException(400, "Each draft row must be an object")
        line_id = line.get('lineId')
        if line_id is not None:
            if isinstance(line_id,bool) or not isinstance(line_id,int) or line_id < 1 or line_id in used:
                raise HTTPException(400, "Draft line IDs must be nonblank, unique strings")
            used.add(line_id)
        if line.get('bands') is not None and not isinstance(line['bands'], dict):
            raise HTTPException(400, "Draft confidence bands must be an object")
    lines = []
    for index, line in enumerate(payload.get('lines', [])):
        line_id = line.get('lineId')
        if line_id is None:
            line_id=index+1
            while line_id in used:
                line_id += 1
            used.add(line_id)
        normalized = {**line, 'lineId': line_id, 'bands': dict(line.get('bands') or {})}
        for field in ('itemName', 'quantity', 'unitPrice'):
            if field + 'Confidence' in line:
                try:
                    normalized['bands'][field] = confidence_band(line[field + 'Confidence'])
                except ValueError as exc:
                    raise HTTPException(400, 'Invalid field confidence') from exc
            if normalized['bands'].get(field) == 'LOW':
                normalized[field] = None
        lines.append(normalized)
    return {**payload, 'lines': lines}


def create_submission(sale_date, *, image_key=None, draft_payload=None, input_type=None, masked_text=None):
    from psycopg.types.json import Jsonb
    submission_id = uuid4()
    draft_payload = _normalize_draft(submission_id, draft_payload)
    with connect() as conn:
        conn.execute("INSERT INTO owners(id) VALUES (%s) ON CONFLICT DO NOTHING", (owner_id(),))
        conn.execute("INSERT INTO submissions(id,owner_id,sale_date,image_key,draft_payload,input_type,masked_text) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                     (submission_id, owner_id(), sale_date, image_key, Jsonb(draft_payload) if draft_payload is not None else None, input_type or ('IMAGE' if image_key else 'TEXT'), masked_text))
    return submission_id


def create_unavailable_submission(sale_date, masked_text=None):
    """Persist the placeholder fallback atomically; never strand a partial request."""
    submission_id = uuid4()
    with connect() as conn:
        conn.execute('INSERT INTO owners(id) VALUES (%s) ON CONFLICT DO NOTHING', (owner_id(),))
        conn.execute('INSERT INTO submissions(id,owner_id,sale_date,masked_text) VALUES (%s,%s,%s,%s)',
                     (submission_id, owner_id(), sale_date, masked_text))
        conn.execute("UPDATE submissions SET status='CLASSIFYING' WHERE id=%s", (submission_id,))
        conn.execute("UPDATE submissions SET status='AI_UNAVAILABLE' WHERE id=%s", (submission_id,))
    return submission_id


def update_status(submission_id, status, *, draft_payload=None, expected_status=None):
    from psycopg.types.json import Jsonb
    draft_payload = _normalize_draft(submission_id, draft_payload)
    try:
        status = SubmissionStatus(status).value
    except ValueError as exc:
        raise HTTPException(400, 'Unknown submission status') from exc
    with connect() as conn:
        row = conn.execute('SELECT status FROM submissions WHERE id=%s AND owner_id=%s FOR UPDATE',
                           (submission_id, owner_id())).fetchone()
        if row is None:
            raise HTTPException(404, 'Submission not found')
        current = row[0]
        if expected_status is not None and current != expected_status:
            raise HTTPException(409, 'Submission changed; reload before updating')
        if status != current and status not in TRANSITIONS[current]:
            raise HTTPException(409, f'Invalid submission transition: {current} -> {status}')
        if status == 'CONFIRMED' and (current != status or draft_payload is not None):
            raise HTTPException(409, 'Use transactional confirmation to save sales')
        result = conn.execute("UPDATE submissions SET status=%s, draft_payload=COALESCE(%s,draft_payload) WHERE id=%s AND owner_id=%s",
                              (status, Jsonb(draft_payload) if draft_payload is not None else None, submission_id, owner_id()))
        if not result.rowcount:
            raise HTTPException(404, "Submission not found")


def get_submission(submission_id):
    with connect() as conn:
        conn.row_factory = dict_row
        row = conn.execute("SELECT id, sale_date, status, draft_payload, input_type, masked_text, created_at FROM submissions WHERE id=%s AND owner_id=%s", (submission_id, owner_id())).fetchone()
        if row is None:
            raise HTTPException(404, "Submission not found")
        draft = _normalize_draft(row["id"], row["draft_payload"])
        return {"submissionId": row["id"], "saleDate": row["sale_date"], "status": row["status"],
                "inputType": row["input_type"], "maskedText": row["masked_text"], "imageUrl": None, "message": (draft or {}).get("message"),
                "lines": (draft or {}).get("lines", []), "manualEntry": row["status"] in ('AI_UNAVAILABLE', 'REJECTED')}


def save_extraction_result(submission_id, overall_confidence, lines):
    """Persist a schema-valid, contact-masked provider result supplied by ingestion.

    InferenceService owns provider validation, masking, timeout and retry. Numeric
    field confidence overrides bands; no provider request is made here.
    """
    if not isinstance(lines, list):
        raise HTTPException(400, 'Draft lines must be a list')
    if any(not isinstance(row, dict) for row in lines):
        raise HTTPException(400, 'Each draft row must be an object')
    payload = _normalize_draft(submission_id, {'version': 1, 'message': None, 'lines': [{**row, 'lineId': index+1} for index,row in enumerate(lines)]})
    try:
        status = extraction_status(overall_confidence, lines)
    except ValueError as exc:
        raise HTTPException(400, 'Invalid overall confidence') from exc
    update_status(submission_id, status, expected_status='CLASSIFYING',
                  draft_payload={'lines': []} if status == 'REJECTED' else payload)


def _same_value(left, right, field):
    if field == 'itemName':
        return str(left).strip() == str(right).strip()
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except Exception:
        return False


def save_sales_records(request):
    with connect() as conn:
        conn.row_factory = dict_row
        submission = conn.execute("SELECT * FROM submissions WHERE id=%s AND owner_id=%s FOR UPDATE", (request.submissionId, owner_id())).fetchone()
        if submission is None:
            raise HTTPException(404, "Submission not found")
        existing = conn.execute("SELECT id FROM sales_records WHERE submission_id=%s ORDER BY line_no", (request.submissionId,)).fetchall()
        if submission['status'] == 'CONFIRMED':
            return [r['id'] for r in existing], False
        if submission['status'] not in ('NEEDS_VERIFICATION', 'AI_UNAVAILABLE', 'REJECTED'):
            raise HTTPException(409, "Submission is not ready for confirmation; rejected submissions must enter manual review first")
        if submission['sale_date'] > today():
            raise HTTPException(400, "Sale date cannot be in the future")
        payload = _normalize_draft(submission['id'], submission['draft_payload'])
        drafts = {line['lineId']: line for line in (payload or {}).get('lines', [])}
        ids, seen = [], set()
        for index, line in enumerate(request.lines):
            draft = None
            if line.draftLineId is not None:
                if line.draftLineId in seen or line.draftLineId not in drafts:
                    raise HTTPException(400, f"Row {index + 1}: unknown or duplicate draftLineId")
                seen.add(line.draftLineId)
                draft = drafts[line.draftLineId]
            if draft is not None:
                for field in ('itemName', 'quantity', 'unitPrice'):
                    band = draft.get('bands', {}).get(field, 'UNSCORED')
                    if band != 'HIGH' and _same_value(draft.get(field), getattr(line, field), field) and field not in line.verifiedFields:
                        raise HTTPException(400, f"Row {index + 1}: verify or edit {field}")
            record_id = uuid4()
            conn.execute("INSERT INTO sales_records(id,submission_id,line_no,item_name,quantity,unit_price,source,item_key) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                         (record_id, request.submissionId, index+1, line.itemName, line.quantity, line.unitPrice, 'AI_EXTRACTED' if draft is not None else 'MANUAL', ' '.join(line.itemName.split()).casefold()))
            ids.append(record_id)
        conn.execute("UPDATE submissions SET status='CONFIRMED', draft_payload=NULL WHERE id=%s", (request.submissionId,))
    return ids, True


def get_confirmed_records(limit=100, offset=0, *, start_date=None, end_date=None):
    with connect() as conn:
        conn.row_factory = dict_row
        return conn.execute("""SELECT r.id, r.line_no AS "lineNo", r.submission_id AS "submissionId", r.item_name AS "itemName",
            r.quantity, r.unit_price AS "unitPrice", s.sale_date AS "saleDate", r.source
            FROM sales_records r JOIN submissions s ON s.id=r.submission_id
            WHERE s.owner_id=%s AND s.status='CONFIRMED'
              AND (%s::date IS NULL OR s.sale_date >= %s)
              AND (%s::date IS NULL OR s.sale_date <= %s)
            ORDER BY s.sale_date DESC, s.created_at DESC, s.id, r.line_no LIMIT %s OFFSET %s""",
            (owner_id(), start_date, start_date, end_date, end_date, limit, offset)).fetchall()


def get_confirmed_records_between(start, end):
    """Complete inclusive history for analysis, without the UI's 100-row page cap."""
    if start > end:
        raise HTTPException(400, 'Start date must not follow end date')
    with connect() as conn:
        conn.row_factory = dict_row
        return conn.execute('''SELECT r.id, r.line_no AS "lineNo", r.submission_id AS "submissionId",
            r.item_name AS "itemName", r.quantity, r.unit_price AS "unitPrice",
            s.sale_date AS "saleDate", r.source
            FROM sales_records r JOIN submissions s ON s.id=r.submission_id
            WHERE s.owner_id=%s AND s.status='CONFIRMED' AND s.sale_date BETWEEN %s AND %s
            ORDER BY s.sale_date, s.created_at, s.id, r.line_no''',
            (owner_id(), start, end)).fetchall()


def save_baselines(estimates, *, as_of_date):
    """Require the actual analysis date; never manufacture provenance."""
    return replace_baselines(as_of_date, estimates)


def get_latest_baselines():
    """Latest stored snapshot per normalized item for the configured owner."""
    with connect() as conn:
        conn.row_factory = dict_row
        return conn.execute('''SELECT DISTINCT ON (item_key)
            id, owner_id AS "ownerId", item_name AS "itemName", window_days AS "windowDays",
            logged_days AS "loggedDays", average_daily_quantity AS "averageDailyQuantity",
            trend, suggested_restock_qty AS "suggestedRestockQty", computed_at AS "computedAt"
            FROM baseline_estimates WHERE owner_id=%s
            ORDER BY item_key, computed_at DESC, id DESC''', (owner_id(),)).fetchall()


def get_image_key(submission_id):
    with connect() as conn:
        row = conn.execute('SELECT image_key FROM submissions WHERE id=%s AND owner_id=%s', (submission_id, owner_id())).fetchone()
        if row is None:
            raise HTTPException(404, 'Submission not found')
        return row[0]


def replace_baselines(as_of_date, estimates):
    from .data_lifecycle import replace_baselines as replace
    return replace(as_of_date, estimates)


def delete_baselines():
    with connect() as conn:
        conn.execute('DELETE FROM baseline_estimates WHERE owner_id=%s', (owner_id(),))
