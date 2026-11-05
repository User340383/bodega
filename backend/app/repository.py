"""Gabriel's persistence boundary. Connection contexts commit or roll back all writes."""
import os
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4, uuid5

from fastapi import HTTPException
from psycopg.rows import dict_row

from .database import connect


def owner_id():
    return UUID(os.environ["OWNER_ID"])


def _normalize_draft(submission_id, payload):
    """Preserve supplied IDs; give legacy/missing IDs stable submission-scoped values."""
    if payload is None:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get('lines', []), list):
        raise HTTPException(422, "Draft lines must be a list")
    used = set()
    for line in payload.get('lines', []):
        if not isinstance(line, dict):
            raise HTTPException(422, "Each draft row must be an object")
        line_id = line.get('lineId')
        if line_id is not None:
            if not isinstance(line_id, str) or not line_id.strip() or line_id in used:
                raise HTTPException(422, "Draft line IDs must be nonblank, unique strings")
            used.add(line_id)
        if line.get('bands') is not None and not isinstance(line['bands'], dict):
            raise HTTPException(422, "Draft confidence bands must be an object")
    lines = []
    for index, line in enumerate(payload.get('lines', [])):
        line_id = line.get('lineId')
        if line_id is None:
            attempt = 0
            while True:
                line_id = str(uuid5(UUID(str(submission_id)), f'draft-line:{index}:{attempt}'))
                if line_id not in used:
                    break
                attempt += 1
            used.add(line_id)
        lines.append({**line, 'lineId': line_id, 'bands': line.get('bands') or {}})
    return {**payload, 'lines': lines}


def create_submission(sale_date, *, image_key=None, draft_payload=None):
    from psycopg.types.json import Jsonb
    submission_id = uuid4()
    draft_payload = _normalize_draft(submission_id, draft_payload)
    with connect() as conn:
        conn.execute("INSERT INTO owners(id) VALUES (%s) ON CONFLICT DO NOTHING", (owner_id(),))
        conn.execute("INSERT INTO submissions(id,owner_id,sale_date,image_key,draft_payload) VALUES (%s,%s,%s,%s,%s)",
                     (submission_id, owner_id(), sale_date, image_key, Jsonb(draft_payload) if draft_payload is not None else None))
    return submission_id


def update_status(submission_id, status, *, draft_payload=None):
    from psycopg.types.json import Jsonb
    draft_payload = _normalize_draft(submission_id, draft_payload)
    with connect() as conn:
        result = conn.execute("UPDATE submissions SET status=%s, draft_payload=COALESCE(%s,draft_payload) WHERE id=%s AND owner_id=%s",
                              (status, Jsonb(draft_payload) if draft_payload is not None else None, submission_id, owner_id()))
        if not result.rowcount:
            raise HTTPException(404, "Submission not found")


def get_submission(submission_id):
    with connect() as conn:
        conn.row_factory = dict_row
        row = conn.execute("SELECT id, sale_date, status, draft_payload FROM submissions WHERE id=%s AND owner_id=%s", (submission_id, owner_id())).fetchone()
        if row is None:
            raise HTTPException(404, "Submission not found")
        draft = _normalize_draft(row["id"], row["draft_payload"])
        return {"submissionId": row["id"], "saleDate": row["sale_date"], "status": row["status"],
                "lines": (draft or {}).get("lines", []), "manualEntry": row["status"] in ('AI_UNAVAILABLE', 'REJECTED')}


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
        existing = conn.execute("SELECT id FROM sales_records WHERE submission_id=%s ORDER BY line_number", (request.submissionId,)).fetchall()
        if submission['status'] == 'CONFIRMED':
            return [r['id'] for r in existing], False
        if submission['status'] not in ('NEEDS_VERIFICATION', 'AI_UNAVAILABLE'):
            raise HTTPException(409, "Submission is not ready for confirmation; rejected submissions must enter manual review first")
        if submission['sale_date'] > date.today():
            raise HTTPException(422, "Sale date cannot be in the future")
        payload = _normalize_draft(submission['id'], submission['draft_payload'])
        drafts = {line['lineId']: line for line in (payload or {}).get('lines', [])}
        ids, seen = [], set()
        for index, line in enumerate(request.lines):
            draft = None
            if line.draftLineId is not None:
                if line.draftLineId in seen or line.draftLineId not in drafts:
                    raise HTTPException(422, f"Row {index + 1}: unknown or duplicate draftLineId")
                if line.source == 'MANUAL':
                    raise HTTPException(422, f"Row {index + 1}: a linked draft row cannot have MANUAL source")
                seen.add(line.draftLineId)
                draft = drafts[line.draftLineId]
            elif line.source == 'AI_EXTRACTED' or (drafts and line.source != 'MANUAL'):
                raise HTTPException(422, f"Row {index + 1}: link the draft row or explicitly declare a new MANUAL row")
            if draft is not None:
                for field in ('itemName', 'quantity', 'unitPrice'):
                    band = draft.get('bands', {}).get(field, 'UNSCORED')
                    if band != 'HIGH' and _same_value(draft.get(field), getattr(line, field), field) and field not in line.verifiedFields:
                        raise HTTPException(422, f"Row {index + 1}: verify or edit {field}")
            record_id = uuid4()
            conn.execute("INSERT INTO sales_records(id,submission_id,line_number,item_name,quantity,unit_price,sale_date,source) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                         (record_id, request.submissionId, index, line.itemName, line.quantity, line.unitPrice, submission['sale_date'], 'AI_EXTRACTED' if draft is not None else 'MANUAL'))
            ids.append(record_id)
        conn.execute("UPDATE submissions SET status='CONFIRMED', draft_payload=NULL WHERE id=%s", (request.submissionId,))
    return ids, True


def get_confirmed_records(limit=100, offset=0):
    with connect() as conn:
        conn.row_factory = dict_row
        return conn.execute("""SELECT r.id, r.submission_id AS "submissionId", r.item_name AS "itemName",
            r.quantity, r.unit_price AS "unitPrice", r.sale_date AS "saleDate", r.source
            FROM sales_records r JOIN submissions s ON s.id=r.submission_id
            WHERE s.owner_id=%s AND s.status='CONFIRMED'
            ORDER BY r.sale_date DESC, s.created_at DESC, s.id, r.line_number LIMIT %s OFFSET %s""",
            (owner_id(), limit, offset)).fetchall()


def start_manual_review(submission_id):
    with connect() as conn:
        row = conn.execute('SELECT status FROM submissions WHERE id=%s AND owner_id=%s FOR UPDATE',
                           (submission_id, owner_id())).fetchone()
        if row is None:
            raise HTTPException(404, 'Submission not found')
        if row[0] == 'NEEDS_VERIFICATION':
            return
        if row[0] not in ('REJECTED', 'AI_UNAVAILABLE'):
            raise HTTPException(409, 'Submission is not eligible for manual review')
        conn.execute("UPDATE submissions SET status='NEEDS_VERIFICATION', draft_payload=NULL WHERE id=%s", (submission_id,))


def get_confirmed_sales_between(start_date, end_date):
    """Every confirmed row in an inclusive date range, oldest first, for analysis.

    Deliberately unpaginated: get_confirmed_records serves the UI ledger one page at a time,
    and analysing only its first page would silently drop history.
    """
    with connect() as conn:
        conn.row_factory = dict_row
        return conn.execute("""SELECT r.item_name AS "itemName", r.quantity, r.sale_date AS "saleDate"
            FROM sales_records r JOIN submissions s ON s.id=r.submission_id
            WHERE s.owner_id=%s AND s.status='CONFIRMED' AND r.sale_date BETWEEN %s AND %s
            ORDER BY r.sale_date, s.created_at, s.id, r.line_number""",
            (owner_id(), start_date, end_date)).fetchall()
