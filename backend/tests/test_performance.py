"""NFR-703: recalculate the baseline analysis within 3.0 s at the 95th percentile for a business
with 5,000 confirmed sales records (M3 11 load profile: 28 days x 100 items), measured against
PostgreSQL through the HTTP API. This measures the backend only; NFR-704 (dashboard render on the
reference 4G profile) needs a browser on that network profile and is not covered here.
"""
import statistics
import time
from datetime import timedelta
from uuid import uuid4

from app import repository
from app.database import connect
from conftest import AS_OF

ITEMS = 100
DAYS = 28
RECORDS = 5000


def load_history():
    rows, submissions = [], []
    per_day = -(-RECORDS // DAYS)  # ceil
    made = 0
    for offset in range(DAYS):
        day = AS_OF - timedelta(days=offset)
        count = min(per_day, RECORDS - made)
        for start in range(0, count, 100):  # At most 100 rows per submission.
            sid = uuid4()
            submissions.append((sid, repository.owner_id(), day))
            for line in range(min(100, count - start)):
                item = (start + line) % ITEMS
                rows.append((uuid4(), sid, line, f'Item {item:03d}', 1 + (offset + item) % 9, '1.25', day))
        made += count
    with connect() as conn:
        conn.execute('INSERT INTO owners(id) VALUES (%s) ON CONFLICT DO NOTHING', (repository.owner_id(),))
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO submissions(id, owner_id, sale_date, input_type, status) "
                            "VALUES (%s, %s, %s, 'MANUAL', 'CONFIRMED')", submissions)
            cur.executemany("INSERT INTO sales_records VALUES (%s,%s,%s,%s,%s,%s,%s,'MANUAL')", rows)
    return len(rows)


def test_analysis_of_5000_records_is_under_3_seconds_at_p95(client):
    assert load_history() == RECORDS
    timings = []
    for _ in range(20):
        started = time.monotonic()
        response = client.get('/api/v1/analysis')
        timings.append(time.monotonic() - started)
        assert response.status_code == 200
    result = response.json()
    assert (result['status'], result['loggedDays'], len(result['estimates'])) == ('READY', DAYS, ITEMS)
    p95 = statistics.quantiles(timings, n=20)[-1]
    print(f'\nNFR-703 analysis over {RECORDS} records: median {statistics.median(timings):.3f} s, p95 {p95:.3f} s')
    assert p95 < 3.0


def test_ledger_and_guidance_stay_responsive_at_5000_records(client):
    load_history()
    started = time.monotonic()
    ledger = client.get('/api/v1/sales')
    assert ledger.status_code == 200 and len(ledger.json()['records']) == RECORDS
    started_guidance = time.monotonic()
    guidance = client.post('/api/v1/guidance', json={})  # No provider configured: template wording.
    assert guidance.status_code == 200 and len(guidance.json()['items']) == ITEMS
    print(f'\nledger {started_guidance - started:.3f} s, guidance {time.monotonic() - started_guidance:.3f} s')
    assert time.monotonic() - started < 6.0
