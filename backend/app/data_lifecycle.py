from datetime import datetime, timedelta, timezone
from uuid import uuid4
from decimal import Decimal, ROUND_HALF_UP
from fastapi import HTTPException
from psycopg.rows import dict_row
from . import repository
from .database import connect
from .schemas import BaselineEstimate
from . import image_cleanup


def list_open():
    mark_stale(datetime.now(timezone.utc)-timedelta(seconds=120))
    with connect() as conn:
        conn.row_factory=dict_row
        rows=conn.execute('''SELECT id AS "submissionId",status,input_type AS "inputType",
            sale_date AS "saleDate",created_at AS "createdAt",
            COALESCE(jsonb_array_length(draft_payload->'lines'),0) AS "lineCount"
            FROM submissions WHERE owner_id=%s AND status IN ('NEEDS_VERIFICATION','REJECTED','AI_UNAVAILABLE')
            ORDER BY created_at DESC,id''',(repository.owner_id(),)).fetchall()
    return rows


def mark_stale(cutoff):
    with connect() as conn:
        return conn.execute("UPDATE submissions SET status='AI_UNAVAILABLE' WHERE status IN ('UPLOADED','CLASSIFYING') AND updated_at < %s",(cutoff,)).rowcount


def delete_submission(sid, *, bucket_name=None):
    with connect() as conn:
        row=conn.execute('SELECT status,image_key FROM submissions WHERE owner_id=%s AND id=%s FOR UPDATE',(repository.owner_id(),sid)).fetchone()
        if row is None: raise HTTPException(404,'Submission not found')
        if row[0] in ('UPLOADED','CLASSIFYING'): raise HTTPException(409,'Submission is still processing. Try again later.')
        if row[1]:
            image_cleanup.enqueue(conn, bucket_name, row[1])
        conn.execute('DELETE FROM baseline_estimates WHERE owner_id=%s',(repository.owner_id(),))
        conn.execute('DELETE FROM submissions WHERE id=%s',(sid,))
    return row[1]


def manual_entry(request):
    # Serialize creation by client-generated UUID, including when no row yet exists.
    with connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(str(request.submissionId),))
        row=conn.execute('SELECT owner_id,status FROM submissions WHERE id=%s FOR UPDATE',(request.submissionId,)).fetchone()
        if row:
            if row[0]!=repository.owner_id(): raise HTTPException(404,'Submission not found')
            if row[1]!='CONFIRMED': raise HTTPException(409,'Submission already exists in another state')
            return [r[0] for r in conn.execute('SELECT id FROM sales_records WHERE submission_id=%s ORDER BY line_no',(request.submissionId,))],False
        conn.execute('INSERT INTO owners(id) VALUES (%s) ON CONFLICT DO NOTHING',(repository.owner_id(),))
        conn.execute("INSERT INTO submissions(id,owner_id,sale_date,input_type,status) VALUES (%s,%s,%s,'MANUAL','CONFIRMED')",(request.submissionId,repository.owner_id(),request.saleDate))
        ids=[]
        for index,line in enumerate(request.lines):
            if line.draftLineId is not None: raise HTTPException(400,'Manual rows cannot link an extracted draft')
            rid=uuid4(); ids.append(rid)
            conn.execute('''INSERT INTO sales_records(id,submission_id,line_no,item_name,quantity,unit_price,source,item_key)
                VALUES (%s,%s,%s,%s,%s,%s,'MANUAL',%s)''',(rid,request.submissionId,index+1,line.itemName,line.quantity,line.unitPrice,line.itemName.casefold()))
    return ids,True


def replace_baselines(as_of,estimates):
    rows=[BaselineEstimate.model_validate(e) for e in estimates]
    keys=[' '.join(r.itemName.split()).casefold() for r in rows]
    if len(keys)!=len(set(keys)): raise HTTPException(400,'Duplicate snapshot item')
    with connect() as conn:
        conn.execute('INSERT INTO owners(id) VALUES (%s) ON CONFLICT DO NOTHING',(repository.owner_id(),))
        conn.execute('SELECT id FROM owners WHERE id=%s FOR UPDATE',(repository.owner_id(),))
        conn.execute('DELETE FROM baseline_estimates WHERE owner_id=%s AND as_of_date=%s',(repository.owner_id(),as_of))
        for r,key in zip(rows,keys):
            conn.execute('''INSERT INTO baseline_estimates(id,owner_id,item_name,item_key,as_of_date,window_days,logged_days,
                average_daily_quantity,prior_average_daily_quantity,trend,cover_days,suggested_restock_qty)
                VALUES (%s,%s,%s,%s,%s,7,%s,%s,%s,%s,7,%s)''',
                (r.id,repository.owner_id(),r.itemName,key,as_of,r.loggedDays,
                 r.averageDailyQuantity.quantize(Decimal('0.0001'),rounding=ROUND_HALF_UP),
                 r.priorAverageDailyQuantity.quantize(Decimal('0.0001'),rounding=ROUND_HALF_UP) if r.priorAverageDailyQuantity is not None else None,
                 r.trend,r.suggestedRestockQty))


def sweep(storage, now=None):
    now=now or datetime.now(timezone.utc)
    mark_stale(now-timedelta(seconds=120))
    cutoff=now-timedelta(days=30)
    # Enqueue and clear references atomically; storage is touched only after commit.
    with connect() as conn:
        rows=conn.execute('SELECT id,status,image_key,owner_id FROM submissions WHERE created_at < %s FOR UPDATE',(cutoff,)).fetchall()
        for sid,state,key,owner in rows:
            if key:
                if storage is None: continue
                image_cleanup.enqueue(conn, storage.bucket, key)
            if state=='CONFIRMED':
                conn.execute('UPDATE submissions SET image_key=NULL,masked_text=NULL WHERE id=%s',(sid,))
            else:
                conn.execute('DELETE FROM submissions WHERE id=%s',(sid,))
                conn.execute('DELETE FROM baseline_estimates WHERE owner_id=%s',(owner,))
        conn.execute('DELETE FROM baseline_estimates WHERE as_of_date < %s',(cutoff.date(),))
    image_cleanup.enqueue_expired(storage, now)
    image_cleanup.process_pending(storage)
