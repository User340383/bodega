"""RecordRepository (M3 5.1, 7): the only module that runs SQL for submissions, sales and snapshots.

Connection contexts commit or roll back every write as one transaction. Callers outside this
module never see SQL. Functions keep the signatures teammates already use:
create_submission / update_status / get_submission / get_capture_input (José's ingestion),
get_confirmed_sales_between (Diushen's analysis) and save_sales_records (confirmation).
"""
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4, uuid5

from fastapi import HTTPException
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .database import connect

OPEN_STATES = ('NEEDS_VERIFICATION', 'AI_UNAVAILABLE', 'REJECTED')
PROCESSING_STATES = ('UPLOADED', 'CLASSIFYING')
STALE_SECONDS = 120
RETENTION_DAYS = 30
STALE_MESSAGE = "We could not read this right now. You can type the sales below."


def owner_id():
    return UUID(os.environ["OWNER_ID"])


def _today():
    from .clock import SystemClock
    return SystemClock().today()


def _line_key(line_id):
    """Draft line IDs are positive integers (M3 7.3); nonblank strings from older drafts still match."""
    return str(line_id)


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
            valid_int = isinstance(line_id, int) and not isinstance(line_id, bool) and line_id >= 1
            valid_str = isinstance(line_id, str) and line_id.strip()
            if not (valid_int or valid_str) or _line_key(line_id) in used:
                raise HTTPException(400, "Draft line IDs must be unique positive integers or nonblank strings")
            used.add(_line_key(line_id))
        if line.get('bands') is not None and not isinstance(line['bands'], dict):
            raise HTTPException(400, "Draft confidence bands must be an object")
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


def _ensure_owner(conn):
    # Id only, so this also works on a schema older than 005; migrate() seeds display_name (M3 7.1).
    conn.execute("INSERT INTO owners(id) VALUES (%s) ON CONFLICT DO NOTHING", (owner_id(),))


def create_submission(sale_date, *, image_key=None, draft_payload=None, input_type=None, masked_text=None):
    submission_id = uuid4()
    draft_payload = _normalize_draft(submission_id, draft_payload)
    with connect() as conn:
        _ensure_owner(conn)
        columns = {"id": submission_id, "owner_id": owner_id(), "sale_date": sale_date, "image_key": image_key,
                   "draft_payload": Jsonb(draft_payload) if draft_payload is not None else None}
        # Capture columns (migration 004) are written only when supplied, so callers that predate
        # them keep working against an older schema (see the migration-upgrade test).
        if input_type is not None:
            columns["input_type"] = input_type
        if masked_text is not None:
            columns["masked_text"] = masked_text
        conn.execute(f"INSERT INTO submissions({','.join(columns)}) VALUES ({','.join(['%s'] * len(columns))})",
                     tuple(columns.values()))
    return submission_id


def get_capture_input(submission_id):
    """José's ingestion boundary: how a submission arrived and its stored (sanitized/masked) input."""
    with connect() as conn:
        conn.row_factory = dict_row
        row = conn.execute("SELECT input_type, image_key, masked_text FROM submissions WHERE id=%s AND owner_id=%s",
                           (submission_id, owner_id())).fetchone()
        if row is None:
            raise HTTPException(404, "Submission not found")
        return row


class StaleTransition(Exception):
    """The submission left the expected state (for example the stale sweep marked it AI_UNAVAILABLE)."""


def update_status(submission_id, status, *, draft_payload=None, expected_status=None):
    """Set a status (and optionally the masked draft). With expected_status, the row is locked and
    the write happens only if the submission is still in that state, so a late extraction result
    can never overwrite an outcome the stale sweep or another request already recorded."""
    draft_payload = _normalize_draft(submission_id, draft_payload)
    with connect() as conn:
        current = conn.execute("SELECT status FROM submissions WHERE id=%s AND owner_id=%s FOR UPDATE",
                               (submission_id, owner_id())).fetchone()
        if current is None:
            raise HTTPException(404, "Submission not found")
        if expected_status is not None and current[0] != expected_status:
            raise StaleTransition(current[0])
        conn.execute("UPDATE submissions SET status=%s, draft_payload=COALESCE(%s,draft_payload) WHERE id=%s",
                     (status, Jsonb(draft_payload) if draft_payload is not None else None, submission_id))


def _records_for(conn, submission_ids):
    return conn.execute("""SELECT r.id, r.line_number, r.submission_id AS "submissionId", r.item_name AS "itemName",
        r.quantity, r.unit_price AS "unitPrice", r.sale_date AS "saleDate", r.source
        FROM sales_records r WHERE r.submission_id = ANY(%s) ORDER BY r.submission_id, r.line_number""",
        (list(submission_ids),)).fetchall()


def get_submission(submission_id):
    with connect() as conn:
        conn.row_factory = dict_row
        row = conn.execute("""SELECT id, sale_date, status, draft_payload, input_type, created_at
            FROM submissions WHERE id=%s AND owner_id=%s""", (submission_id, owner_id())).fetchone()
        if row is None:
            raise HTTPException(404, "Submission not found")
        draft = _normalize_draft(row["id"], row["draft_payload"])
        result = {"submissionId": row["id"], "saleDate": row["sale_date"], "status": row["status"],
                  "createdAt": row["created_at"], "lines": (draft or {}).get("lines", []),
                  "manualEntry": row["status"] in ('AI_UNAVAILABLE', 'REJECTED'),
                  "message": (draft or {}).get("message")}
        if row["status"] == 'CONFIRMED':
            # SubmissionDetail adds the saved rows once confirmed (M3 4.2).
            result["records"] = [_public_record(r) for r in _records_for(conn, [row["id"]])]
        return result


def _same_value(left, right, field):
    if field == 'itemName':
        return str(left).strip() == str(right).strip()
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except Exception:
        return False


def save_sales_records(request, *, today=None):
    """Confirm one open submission: validate against its stored draft, insert every row and set
    CONFIRMED in one locked transaction. A repeat on a CONFIRMED submission returns its IDs."""
    with connect() as conn:
        conn.row_factory = dict_row
        submission = conn.execute("SELECT * FROM submissions WHERE id=%s AND owner_id=%s FOR UPDATE",
                                  (request.submissionId, owner_id())).fetchone()
        if submission is None:
            raise HTTPException(404, "Submission not found")
        existing = conn.execute("SELECT id FROM sales_records WHERE submission_id=%s ORDER BY line_number",
                                (request.submissionId,)).fetchall()
        if submission['status'] == 'CONFIRMED':
            return [r['id'] for r in existing], False
        if submission['status'] not in OPEN_STATES:
            raise HTTPException(409, "This sales log is still being read. Try again in a moment.")
        if submission['sale_date'] > (today or _today()):
            raise HTTPException(400, "Sale date cannot be in the future")
        payload = _normalize_draft(submission['id'], submission['draft_payload'])
        drafts = {_line_key(line['lineId']): line for line in (payload or {}).get('lines', [])}
        ids, seen = [], set()
        for index, line in enumerate(request.lines):
            draft = None
            if line.draftLineId is not None:
                key = _line_key(line.draftLineId)
                if key in seen or key not in drafts:
                    raise HTTPException(400, f"Row {index + 1}: unknown or duplicate draftLineId")
                if line.source == 'MANUAL':
                    raise HTTPException(400, f"Row {index + 1}: a linked draft row cannot have MANUAL source")
                seen.add(key)
                draft = drafts[key]
            elif line.source == 'AI_EXTRACTED' or (drafts and line.source != 'MANUAL'):
                raise HTTPException(400, f"Row {index + 1}: link the draft row or explicitly declare a new MANUAL row")
            if draft is not None:
                for field in ('itemName', 'quantity', 'unitPrice'):
                    band = draft.get('bands', {}).get(field, 'UNSCORED')
                    if band != 'HIGH' and _same_value(draft.get(field), getattr(line, field), field) and field not in line.verifiedFields:
                        raise HTTPException(400, f"Row {index + 1}: verify or edit {field}")
            record_id = uuid4()
            conn.execute("INSERT INTO sales_records(id,submission_id,line_number,item_name,quantity,unit_price,sale_date,source) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                         (record_id, request.submissionId, index, line.itemName, line.quantity, line.unitPrice, submission['sale_date'], 'AI_EXTRACTED' if draft is not None else 'MANUAL'))
            ids.append(record_id)
        conn.execute("UPDATE submissions SET status='CONFIRMED', draft_payload=NULL WHERE id=%s", (request.submissionId,))
    return ids, True


def create_manual_submission(submission_id, sale_date, lines):
    """Standalone manual entry (FR-104, SD-04): insert a MANUAL submission directly as CONFIRMED
    with its rows in one transaction. The client generates submission_id once per form, so a
    resubmission after a network error returns the existing record IDs instead of duplicating."""
    with connect() as conn:
        # Serialize concurrent first submissions of the same form, even before the row exists.
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (str(submission_id),))
        row = conn.execute("SELECT owner_id, status FROM submissions WHERE id=%s FOR UPDATE", (submission_id,)).fetchone()
        if row is not None:
            if row[0] != owner_id():
                raise HTTPException(409, "This submission ID is already in use")
            if row[1] != 'CONFIRMED':
                raise HTTPException(409, "This submission already exists in another state")
            ids = conn.execute("SELECT id FROM sales_records WHERE submission_id=%s ORDER BY line_number",
                               (submission_id,)).fetchall()
            return [r[0] for r in ids], False
        _ensure_owner(conn)
        conn.execute("""INSERT INTO submissions(id, owner_id, sale_date, input_type, status)
            VALUES (%s, %s, %s, 'MANUAL', 'CONFIRMED')""", (submission_id, owner_id(), sale_date))
        ids = []
        for index, line in enumerate(lines):
            record_id = uuid4()
            conn.execute("""INSERT INTO sales_records(id,submission_id,line_number,item_name,quantity,unit_price,sale_date,source)
                VALUES (%s,%s,%s,%s,%s,%s,%s,'MANUAL')""",
                (record_id, submission_id, index, line.itemName, line.quantity, line.unitPrice, sale_date))
            ids.append(record_id)
    return ids, True


def _public_record(row):
    """SALES_RECORDS row in the M3 4.2 GET /sales shape. lineTotal is computed, never stored."""
    return {"recordId": row["id"], "submissionId": row["submissionId"], "saleDate": row["saleDate"],
            "lineNo": row["line_number"] + 1, "itemName": row["itemName"], "quantity": row["quantity"],
            "unitPrice": row["unitPrice"], "lineTotal": row["quantity"] * row["unitPrice"], "source": row["source"]}


def get_confirmed_records(start_date, end_date):
    """Confirmed history for the owner in an inclusive date range (GET /sales): newest date first,
    then by submission and line number. Drafts never appear."""
    with connect() as conn:
        conn.row_factory = dict_row
        rows = conn.execute("""SELECT r.id, r.line_number, r.submission_id AS "submissionId", r.item_name AS "itemName",
            r.quantity, r.unit_price AS "unitPrice", r.sale_date AS "saleDate", r.source
            FROM sales_records r JOIN submissions s ON s.id=r.submission_id
            WHERE s.owner_id=%s AND s.status='CONFIRMED' AND r.sale_date BETWEEN %s AND %s
            ORDER BY r.sale_date DESC, s.created_at DESC, s.id, r.line_number""",
            (owner_id(), start_date, end_date)).fetchall()
    return [_public_record(row) for row in rows]


def get_confirmed_sales_between(start_date, end_date):
    """Every confirmed row in an inclusive date range, oldest first, for analysis.

    Deliberately unpaginated so analysis never drops history.
    """
    with connect() as conn:
        conn.row_factory = dict_row
        return conn.execute("""SELECT r.item_name AS "itemName", r.quantity, r.sale_date AS "saleDate"
            FROM sales_records r JOIN submissions s ON s.id=r.submission_id
            WHERE s.owner_id=%s AND s.status='CONFIRMED' AND r.sale_date BETWEEN %s AND %s
            ORDER BY r.sale_date, s.created_at, s.id, r.line_number""",
            (owner_id(), start_date, end_date)).fetchall()


def mark_stale(now=None):
    """RetentionService.markStale: UPLOADED/CLASSIFYING older than 120 s become AI_UNAVAILABLE
    (an interrupted request or a server restart), so the owner can finish them manually."""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(seconds=STALE_SECONDS)
    with connect() as conn:
        return conn.execute("""UPDATE submissions SET status='AI_UNAVAILABLE', draft_payload=%s
            WHERE status IN ('UPLOADED','CLASSIFYING') AND updated_at < %s""",
            (Jsonb({"version": 1, "message": STALE_MESSAGE, "lines": []}), cutoff)).rowcount


def list_open_submissions():
    """Pending drafts (FR-216): open submissions, newest first."""
    mark_stale()
    with connect() as conn:
        conn.row_factory = dict_row
        return conn.execute("""SELECT id AS "submissionId", status, input_type AS "inputType", sale_date AS "saleDate",
            created_at AS "createdAt", COALESCE(jsonb_array_length(draft_payload->'lines'), 0) AS "lineCount"
            FROM submissions WHERE owner_id=%s AND status = ANY(%s)
            ORDER BY created_at DESC, id""", (owner_id(), list(OPEN_STATES))).fetchall()


def _enqueue_image_deletion(conn, image_key):
    if image_key:
        conn.execute("INSERT INTO image_deletion_jobs(image_key) VALUES (%s) ON CONFLICT DO NOTHING", (image_key,))


def delete_submission(submission_id):
    """DELETE /submissions/{id} (FR-304, 7.4): the row, its sales rows (cascade), its image (queued
    for deletion after commit) and every baseline snapshot for the owner, which the next analysis
    recomputes. Returns the number of sales records deleted."""
    with connect() as conn:
        row = conn.execute("SELECT status, image_key FROM submissions WHERE id=%s AND owner_id=%s FOR UPDATE",
                           (submission_id, owner_id())).fetchone()
        if row is None:
            raise HTTPException(404, "Submission not found")
        if row[0] in PROCESSING_STATES:
            raise HTTPException(409, "This sales log is still being read. Try again in a moment.")
        deleted = conn.execute("SELECT count(*) FROM sales_records WHERE submission_id=%s", (submission_id,)).fetchone()[0]
        _enqueue_image_deletion(conn, row[1])
        conn.execute("DELETE FROM baseline_estimates WHERE owner_id=%s", (owner_id(),))
        conn.execute("DELETE FROM submissions WHERE id=%s", (submission_id,))
    return deleted


def purge_expired(now=None):
    """RetentionService.purgeExpired (FR-215, 7.4), for every owner:
    - open submissions older than 30 days are deleted with their image;
    - confirmed submissions keep their rows, but their image and masked text are removed;
    - baseline snapshots older than 30 days (by asOfDate) are deleted.
    Images are only queued here; they are deleted from storage after this transaction commits."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=RETENTION_DAYS)
    with connect() as conn:
        expired = conn.execute("""SELECT id, status, image_key, owner_id FROM submissions
            WHERE created_at < %s AND (status <> 'CONFIRMED' OR image_key IS NOT NULL OR masked_text IS NOT NULL)
            FOR UPDATE""", (cutoff,)).fetchall()
        purged = {"openDeleted": 0, "inputsCleared": 0}
        for submission_id, state, image_key, owner in expired:
            if state in PROCESSING_STATES:
                continue  # mark_stale moves these to AI_UNAVAILABLE first; purge on the next run.
            _enqueue_image_deletion(conn, image_key)
            if state == 'CONFIRMED':
                conn.execute("UPDATE submissions SET image_key=NULL, masked_text=NULL WHERE id=%s", (submission_id,))
                purged["inputsCleared"] += 1
            else:
                conn.execute("DELETE FROM submissions WHERE id=%s", (submission_id,))
                purged["openDeleted"] += 1
        purged["snapshotsDeleted"] = conn.execute("DELETE FROM baseline_estimates WHERE as_of_date < %s",
                                                  (cutoff.date(),)).rowcount
    return purged


def pending_image_deletions(limit=100):
    with connect() as conn:
        return [row[0] for row in conn.execute(
            "SELECT image_key FROM image_deletion_jobs ORDER BY created_at LIMIT %s", (limit,)).fetchall()]


def finish_image_deletion(image_key, succeeded):
    with connect() as conn:
        if succeeded:
            conn.execute("DELETE FROM image_deletion_jobs WHERE image_key=%s", (image_key,))
        else:
            conn.execute("""UPDATE image_deletion_jobs SET attempt_count=attempt_count+1, last_attempt_at=now()
                WHERE image_key=%s""", (image_key,))


def replace_baselines(as_of_date: date, snapshots):
    """saveBaselines (SD-03): one snapshot per owner, asOfDate and item, replaced on every analysis
    run for that date. `snapshots` are dicts with itemName, itemKey, windowDays, loggedDays,
    averageDailyQuantity, priorAverageDailyQuantity, trend and suggestedRestockQty."""
    with connect() as conn:
        _ensure_owner(conn)
        conn.execute("SELECT id FROM owners WHERE id=%s FOR UPDATE", (owner_id(),))
        conn.execute("DELETE FROM baseline_estimates WHERE owner_id=%s AND as_of_date=%s", (owner_id(), as_of_date))
        for item in snapshots:
            conn.execute("""INSERT INTO baseline_estimates(id, owner_id, item_name, item_key, as_of_date, window_days,
                    logged_days, average_daily_quantity, prior_average_daily_quantity, trend, suggested_restock_qty)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (uuid4(), owner_id(), item["itemName"], item["itemKey"], as_of_date, item["windowDays"],
                 item["loggedDays"], item["averageDailyQuantity"], item["priorAverageDailyQuantity"],
                 item["trend"], item["suggestedRestockQty"]))


def get_latest_baselines():
    """getLatestBaselines: the newest snapshot per item for the owner."""
    with connect() as conn:
        conn.row_factory = dict_row
        return conn.execute("""SELECT DISTINCT ON (item_key) item_name AS "itemName", item_key AS "itemKey",
                as_of_date AS "asOfDate", window_days AS "windowDays", logged_days AS "loggedDays",
                average_daily_quantity AS "averageDailyQuantity",
                prior_average_daily_quantity AS "priorAverageDailyQuantity", trend,
                suggested_restock_qty AS "suggestedRestockQty", computed_at AS "computedAt"
            FROM baseline_estimates WHERE owner_id=%s AND as_of_date IS NOT NULL
            ORDER BY item_key, as_of_date DESC, computed_at DESC""", (owner_id(),)).fetchall()
