"""HTTP smoke test of the deployed contract; used only in the isolated smoke stack (compose.test.yml).

Runs against real PostgreSQL and real S3 (MinIO), with synthetic data only:
  A. AI outage, through the SPA's Vite proxy (API_URL; backend without an AI provider):
     validation envelope (400 extraction, 422 confirmation), markup rejection, text and photo
     capture falling back to manual entry, confirmation idempotency, stored input kept after
     confirmation (M3 7.4) and removed from S3 on discard, standalone manual entry, pending
     drafts, history range and pagination, request IDs, auth.
  B. AI path (AI_API_URL; the deterministic mock provider): capture -> review -> confirm ->
     history -> analysis (M3 5.5: 7-logged-day average, suggested stock) -> guidance.
  C. Delete-all: a wrong confirmation deletes nothing; DELETE reports the record count, and
     history and analysis are empty afterwards.
"""
import io
import json
import os
import time
import uuid
from datetime import date, timedelta
from decimal import Decimal

import httpx
from PIL import Image

AUTH = {'Authorization': 'Bearer smoke-test-code'}
RUN = uuid.uuid4().hex[:6]  # Item names unique per run, so a re-run on the same database still checks exactly.


def numbers(response):
    """M3 4.2: amounts are JSON numbers; parse them as Decimal so nothing is rounded."""
    return json.loads(response.text, parse_float=Decimal)


def wait_for(client, path, check):
    for attempt in range(60):
        try:
            response = client.get(path)
            if check(response):
                return
        except httpx.HTTPError:
            pass
        if attempt == 59:
            raise SystemExit(f'{path} never became ready')
        time.sleep(0.5)


def jpeg():
    out = io.BytesIO()
    Image.new('RGB', (60, 40), 'white').save(out, format='JPEG')
    return out.getvalue()


with httpx.Client(base_url=os.environ['API_URL'], headers=AUTH, timeout=90) as web, \
        httpx.Client(base_url=os.environ['AI_API_URL'], headers=AUTH, timeout=90) as ai, \
        httpx.Client(timeout=15) as plain:
    wait_for(web, '/', lambda r: r.status_code == 200 and '<div id="root">' in r.text)
    wait_for(ai, '/health', lambda r: r.status_code == 200)
    today = date.today()
    d1, d2 = (today - timedelta(days=2)).isoformat(), (today - timedelta(days=1)).isoformat()

    # ---- A. AI outage through the SPA proxy ----------------------------------------------
    invalid = web.post('/api/v1/extract', data={'saleDate': 'not-a-date', 'text': 'Bread 3'})
    assert invalid.status_code == 400, invalid.text
    assert invalid.json()['error']['code'] == 'VALIDATION_ERROR' and 'saleDate' in invalid.json()['error']['fields']
    assert invalid.headers['x-request-id']
    assert web.post('/api/v1/extract', data={'saleDate': d2, 'text': '<script>alert(1)</script>'}).status_code == 400
    bad_confirm = web.post('/api/v1/confirm', json={'submissionId': 'bad', 'lines': []})
    assert bad_confirm.status_code == 422 and bad_confirm.json()['error']['code'] == 'VALIDATION_ERROR'

    text = web.post('/api/v1/extract', data={'saleDate': d2, 'text': 'Bread 3\ncall 809-555-1234 or a@b.do'}).json()
    assert text['status'] == 'AI_UNAVAILABLE' and text['manualEntry'] is True
    assert text['maskedText'] == 'Bread 3\ncall [removed] or [removed]'
    sid = text['submissionId']
    assert sid in [row['submissionId'] for row in web.get('/api/v1/submissions').json()]
    request = {'submissionId': sid, 'lines': [{'itemName': f'Smoke bread {RUN}', 'quantity': '2.5', 'unitPrice': '1.20',
                                               'source': 'MANUAL'}]}
    first = web.post('/api/v1/confirm', json=request)
    assert first.status_code == 201, first.text
    repeat = web.post('/api/v1/confirm', json=request)
    assert repeat.status_code == 200 and first.json()['recordIds'] == repeat.json()['recordIds']
    detail = web.get('/api/v1/submissions/' + sid).json()
    assert detail['status'] == 'CONFIRMED' and len(detail['records']) == 1
    assert sid not in [row['submissionId'] for row in web.get('/api/v1/submissions').json()]

    photo = web.post('/api/v1/extract', data={'saleDate': d2}, files={'file': ('log.jpg', jpeg(), 'image/jpeg')})
    assert photo.status_code == 200, photo.text
    photo = photo.json()
    assert photo['status'] == 'AI_UNAVAILABLE' and photo['imageUrl']
    assert plain.get(photo['imageUrl']).status_code == 200  # Stored in S3, served by a signed URL.
    assert web.post('/api/v1/confirm', json={'submissionId': photo['submissionId'], 'lines': [
        {'itemName': f'Smoke eggs {RUN}', 'quantity': 12, 'unitPrice': 0.25, 'source': 'MANUAL'}]}).status_code == 201
    assert plain.get(photo['imageUrl']).status_code == 200  # M3 7.4: kept for 30 days after upload.

    discard = web.post('/api/v1/extract', data={'saleDate': d2}, files={'file': ('log.jpg', jpeg(), 'image/jpeg')}).json()
    assert web.delete('/api/v1/submissions/' + discard['submissionId']).status_code == 204
    assert plain.get(discard['imageUrl']).status_code == 404 and \
        web.get('/api/v1/submissions/' + discard['submissionId']).status_code == 404

    manual_id = str(uuid.uuid4())
    manual = {'submissionId': manual_id, 'saleDate': d2,
              'lines': [{'itemName': f'Smoke rice {RUN}', 'quantity': '1.25', 'unitPrice': '0.80'}]}
    created = web.post('/api/v1/manual-entries', json=manual)  # Manual entry while the AI is down.
    assert created.status_code == 201, created.text
    assert web.post('/api/v1/manual-entries', json=manual).json()['recordIds'] == created.json()['recordIds']
    bad_manual = web.post('/api/v1/manual-entries', json={**manual, 'submissionId': str(uuid.uuid4()),
                                                          'lines': [{'itemName': 'x', 'quantity': '1.234', 'unitPrice': 1}]})
    assert bad_manual.status_code == 422 and 'lines.0.quantity' in bad_manual.json()['error']['fields']

    saved = numbers(web.get('/api/v1/sales'))['records']
    rows = [row for row in saved if row['submissionId'] == sid]
    assert len(rows) == 1 and rows[0]['quantity'] == Decimal('2.5') and rows[0]['lineTotal'] == Decimal('3.000')
    page = web.get('/api/v1/sales', params={'limit': 1, 'offset': 1}).json()
    assert (len(page['records']), page['offset'], page['total'] >= 3, page['hasMore']) == (1, 1, True, True)
    assert web.get('/api/v1/sales', params={'start': d2, 'end': d1}).status_code == 400
    assert web.delete('/api/v1/submissions/' + manual_id).headers['x-records-deleted'] == '1'
    assert web.get('/api/v1/sales', headers={'Authorization': 'Bearer invalid'}).status_code == 401

    # ---- B. AI path: capture -> review -> confirm -> history -> analysis -> guidance ------------
    bread = f'Bread {RUN}'
    for day, quantity in ((d1, 3), (d2, 5)):
        draft = ai.post('/api/v1/extract', data={'saleDate': day, 'text': f'{bread} {quantity} 1.50'}).json()
        assert draft['status'] == 'NEEDS_VERIFICATION', draft
        review = ai.get('/api/v1/submissions/' + draft['submissionId']).json()  # Reopened from PostgreSQL.
        assert review['lines'] == draft['lines'] and review['saleDate'] == day
        line = review['lines'][0]
        uncertain = [field for field, band in line['bands'].items() if band != 'HIGH']
        confirm = ai.post('/api/v1/confirm', json={'submissionId': draft['submissionId'], 'lines': [{
            'draftLineId': line['lineId'], 'itemName': line['itemName'], 'quantity': line['quantity'],
            'unitPrice': line['unitPrice'], 'verifiedFields': uncertain}]})
        assert confirm.status_code == 201, confirm.text
    history = ai.get('/api/v1/sales', params={'start': d1, 'end': d2}).json()['records']
    assert sorted(r['saleDate'] for r in history if r['itemName'] == bread) == [d1, d2]
    early = ai.get('/api/v1/analysis').json()  # Fewer than 7 logged days: no estimates, guidance 409.
    assert early['status'] == 'INSUFFICIENT_HISTORY' and early['estimates'][0]['suggestedQty'] is None, early
    assert ai.post('/api/v1/guidance', json={}).json()['error']['code'] == 'INSUFFICIENT_HISTORY'
    for back in range(3, 9):  # Six more logged days (manual entry): 8 in all.
        entry = {'submissionId': str(uuid.uuid4()), 'saleDate': (today - timedelta(days=back)).isoformat(),
                 'lines': [{'itemName': bread, 'quantity': 4, 'unitPrice': 1.5}]}
        assert ai.post('/api/v1/manual-entries', json=entry).status_code == 201
    analysis = numbers(ai.get('/api/v1/analysis'))
    estimate = next(e for e in analysis['estimates'] if e['itemName'] == bread)
    # Latest 7 logged days: 5 + 3 + 4 x 5 = 28, so 4.0 per logged day and ceil(4.0 x 7) = 28.
    assert (analysis['loggedDays'], estimate['currentAverageDisplay'], estimate['suggestedQty']) == (8, '4.0', 28), estimate
    assert estimate['trend'] == 'INSUFFICIENT_DATA'  # A trend needs 14 logged days.
    guidance = ai.post('/api/v1/guidance', json={}).json()
    assert guidance['source'] == 'AI_WORDED' and guidance['groundingPassed'] is True, guidance
    assert bread in [item['itemName'] for item in guidance['items']] and guidance['stockNotice']

    # ---- C. Delete all stored data ------------------------------------------------------
    total = web.get('/api/v1/sales', params={'start': (today - timedelta(days=365)).isoformat()}).json()['total']
    refused = web.post('/api/v1/data/delete-all', json={'confirmation': 'delete'})
    assert refused.status_code == 400
    assert web.get('/api/v1/sales', params={'start': (today - timedelta(days=365)).isoformat()}).json()['total'] == total
    deleted = web.post('/api/v1/data/delete-all', json={'confirmation': 'DELETE'}).json()
    assert deleted['recordsDeleted'] == total and deleted['message'].startswith(f'{total} records deleted'), deleted
    assert web.get('/api/v1/sales').json()['records'] == [] and web.get('/api/v1/submissions').json() == []
    assert web.get('/api/v1/analysis').json()['status'] == 'NO_DATA'

print('PASS: SPA proxy, envelope (400/422), markup rejection, AI-outage fallback, masked text, confirmation '
      'idempotency, S3 photo kept after confirm and removed on discard, manual entry during the outage, pending '
      'drafts, history range and pagination, auth; AI path capture -> review -> confirm -> history -> analysis '
      '(7-logged-day average 4.0, suggested stock 28) -> guidance; delete-all with typed DELETE')
