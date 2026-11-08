"""HTTP integration through Vite's proxy; used only in the isolated smoke stack.

Exercises the deployed contract end to end against a real backend and PostgreSQL: validation
envelope, extraction fallback, confirmation idempotency, standalone manual entry, pending
drafts, deletion, the ledger, request IDs and authentication.
"""
import os
import time
import uuid
from datetime import date

import httpx

with httpx.Client(base_url=os.environ['API_URL'], headers={'Authorization': 'Bearer smoke-test-code'}) as client:
    for attempt in range(60):
        try:
            response = client.get('/')
            response.raise_for_status()
            assert '<div id="root">' in response.text
            break
        except (httpx.HTTPError, AssertionError):
            if attempt == 59:
                raise
            time.sleep(0.5)
    today = date.today().isoformat()

    invalid = client.post('/api/v1/extract', data={'saleDate': 'not-a-date', 'text': 'Bread 3'})
    assert invalid.status_code == 400, invalid.text
    assert invalid.json()['error']['code'] == 'VALIDATION_ERROR'
    assert 'saleDate' in invalid.json()['error']['fields']
    assert invalid.headers['x-request-id']
    injected = client.post('/api/v1/extract', data={'saleDate': today, 'text': '<script>alert(1)</script>'})
    assert injected.status_code == 400, injected.text
    assert client.post('/api/v1/confirm', json={'submissionId': 'bad', 'lines': []}).status_code == 400

    draft = client.post('/api/v1/extract', data={'saleDate': today, 'text': 'Bread 3'}).raise_for_status().json()
    assert draft['status'] == 'AI_UNAVAILABLE' and draft['manualEntry'] is True
    sid = draft['submissionId']
    assert sid in [row['submissionId'] for row in client.get('/api/v1/submissions').raise_for_status().json()]
    request = {'submissionId': sid, 'lines': [{'itemName': 'Smoke bread', 'quantity': '2.5', 'unitPrice': '1.20'}]}
    first = client.post('/api/v1/confirm', json=request)
    assert first.status_code == 201, first.text
    repeat = client.post('/api/v1/confirm', json=request)
    assert repeat.status_code == 200, repeat.text
    assert first.json()['recordIds'] == repeat.json()['recordIds']
    assert sid not in [row['submissionId'] for row in client.get('/api/v1/submissions').json()]

    manual_id = str(uuid.uuid4())
    manual = {'submissionId': manual_id, 'saleDate': today,
              'lines': [{'itemName': 'Smoke rice (lb)', 'quantity': '1.25', 'unitPrice': '0.80'}]}
    created = client.post('/api/v1/manual-entries', json=manual)
    assert created.status_code == 201, created.text
    assert client.post('/api/v1/manual-entries', json=manual).json()['recordIds'] == created.json()['recordIds']

    saved = client.get('/api/v1/sales').raise_for_status().json()['records']
    rows = [row for row in saved if row['submissionId'] == sid]
    assert len(rows) == 1 and rows[0]['quantity'] == '2.5' and rows[0]['unitPrice'] == '1.20'
    assert rows[0]['lineTotal'] == '3.000'
    detail = client.get('/api/v1/submissions/' + sid).json()
    assert detail['status'] == 'CONFIRMED' and len(detail['records']) == 1

    deleted = client.delete('/api/v1/submissions/' + manual_id)
    assert deleted.status_code == 204 and deleted.headers['x-records-deleted'] == '1'
    assert client.get('/api/v1/submissions/' + manual_id).status_code == 404
    assert client.get('/api/v1/sales', headers={'Authorization': 'Bearer invalid'}).status_code == 401
print('PASS: frontend HTML, proxy, validation envelope (400), markup rejection, extraction fallback, '
      'confirmation idempotency, manual entry, pending drafts, deletion, exact-decimal ledger, '
      'request IDs and authentication')
