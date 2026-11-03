"""HTTP integration through Vite's proxy; used only in the isolated smoke stack."""
import os
import time
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
    invalid = client.post('/api/v1/extract', data={'saleDate': 'not-a-date', 'text': 'Bread 3'})
    assert invalid.status_code == 400, invalid.text
    assert invalid.json()['detail'][0]['loc'] == ['body', 'saleDate']
    invalid_confirm = client.post('/api/v1/confirm', json={'submissionId': 'bad', 'lines': []})
    assert invalid_confirm.status_code == 422
    assert all(set(error) == {'loc', 'msg', 'type'} for error in invalid_confirm.json()['detail'])
    draft = client.post('/api/v1/extract', data={'saleDate': date.today().isoformat(), 'text': 'Bread 3'}).raise_for_status().json()
    assert draft['status'] == 'AI_UNAVAILABLE'
    sid = draft['submissionId']
    request = {'submissionId': sid, 'lines': [{'itemName': 'Smoke bread', 'quantity': '2.5', 'unitPrice': '1.20'}]}
    first = client.post('/api/v1/confirm', json=request)
    assert first.status_code == 201, first.text
    repeat = client.post('/api/v1/confirm', json=request)
    assert repeat.status_code == 200, repeat.text
    assert first.json()['recordIds'] == repeat.json()['recordIds']
    saved = client.get('/api/v1/sales').raise_for_status().json()['records']
    rows = [row for row in saved if row['submissionId'] == sid]
    assert len(rows) == 1 and rows[0]['quantity'] == '2.5' and rows[0]['unitPrice'] == '1.20'
    filtered = client.get('/api/v1/sales', params={'startDate': date.today().isoformat(), 'endDate': date.today().isoformat()}).raise_for_status().json()['records']
    assert any(row['submissionId'] == sid for row in filtered)
    assert client.get('/api/v1/submissions/' + sid).json()['status'] == 'CONFIRMED'
    assert client.get('/api/v1/sales', headers={'Authorization': 'Bearer invalid'}).status_code == 401
print('PASS: frontend HTML, proxy, persistent draft, confirmation, idempotency, exact-decimal ledger, inclusive date filters, authentication, extraction 400 and standardized confirmation 422')
