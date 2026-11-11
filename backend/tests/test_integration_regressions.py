"""Regression tests for integration defects found while aligning the four members' Milestone 4 work
with WorkDivision and the final Milestone 3 (docs/INTEGRATION_DECISIONS.md). Each section names the
defect it reproduces or the Milestone 3 rule it protects. Synthetic data only."""
import io
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest
from PIL import Image

from app import privacy, repository, retention
from app.database import connect
from app.llm import MockLLMService, get_wording_provider
from app.main import app
from app.storage import InMemoryObjectStorage, get_object_storage
from app.vision import ExtractedLine, ExtractionResult, MockVisionModel, get_vision_model
from conftest import AS_OF

DAY = AS_OF.isoformat()


def use(model=None, store=None):
    model = model or MockVisionModel()
    store = store or InMemoryObjectStorage()
    app.dependency_overrides[get_vision_model] = lambda: model
    app.dependency_overrides[get_object_storage] = lambda: store
    return model, store


def jpeg():
    out = io.BytesIO()
    Image.new('RGB', (40, 40), 'white').save(out, format='JPEG')
    return out.getvalue()


def row(sid):
    with connect() as conn:
        return conn.execute("SELECT status, sale_date, image_key, masked_text FROM submissions WHERE id=%s",
                            (sid,)).fetchone()


def count(table):
    with connect() as conn:
        return conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]


def manual(client, sale_date, name='Bread', rows=1, quantity=1):
    lines = [{'itemName': f'{name} {n}' if rows > 1 else name, 'quantity': quantity, 'unitPrice': 1} for n in range(rows)]
    response = client.post('/api/v1/manual-entries', json={'submissionId': str(uuid4()),
                                                           'saleDate': sale_date.isoformat(), 'lines': lines})
    assert response.status_code == 201, response.text


# ---- Lifecycle follows M3 Diagram 4: no AI_UNAVAILABLE -> CLASSIFYING, no retry endpoint ------
def test_ai_unavailable_cannot_return_to_classifying(client):
    use(MockVisionModel(fail_modes=['fatal']))
    draft = client.post('/api/v1/extract', data={'saleDate': DAY, 'text': 'Bread 3 1.50'}).json()
    sid = draft['submissionId']
    assert draft['status'] == 'AI_UNAVAILABLE' and draft['manualEntry'] is True
    with pytest.raises(psycopg.errors.CheckViolation), connect() as conn:
        conn.execute("UPDATE submissions SET status='CLASSIFYING' WHERE id=%s", (sid,))
    assert client.post(f'/api/v1/submissions/{sid}/retry').status_code in (404, 405)  # Not in M3 4.2.
    assert row(sid)[0] == 'AI_UNAVAILABLE'
    # The M3 path from here: the owner types the rows and the same confirm saves them (SD-02).
    body = {'submissionId': sid, 'lines': [{'itemName': 'Bread', 'quantity': 3, 'unitPrice': 1.5}]}
    assert client.post('/api/v1/confirm', json=body).status_code == 201


# ---- Retention follows M3 7.4: input kept 30 days; only draftPayload is cleared on confirm ----
def test_confirming_a_text_draft_keeps_the_masked_text_until_the_30_day_purge(client):
    use(MockVisionModel())
    draft = client.post('/api/v1/extract', data={'saleDate': DAY, 'text': 'Bread 3 1.50'}).json()
    sid = draft['submissionId']
    line = draft['lines'][0]
    lines = [{'itemName': line['itemName'], 'quantity': line['quantity'], 'unitPrice': line['unitPrice'],
              'draftLineId': line['lineId'], 'verifiedFields': [f for f, band in line['bands'].items() if band != 'HIGH']}]
    assert client.post('/api/v1/confirm', json={'submissionId': sid, 'lines': lines}).status_code == 201
    assert row(sid)[3] == 'Bread 3 1.50'
    with connect() as conn:  # M3 7.3/7.4: draftPayload is cleared on confirmation.
        assert conn.execute('SELECT draft_payload FROM submissions WHERE id=%s', (sid,)).fetchone()[0] is None
    assert client.get(f'/api/v1/submissions/{sid}').json()['maskedText'] == 'Bread 3 1.50'  # SubmissionDetail (4.2)
    retention.run_sweep(InMemoryObjectStorage(), now=datetime.now(timezone.utc) + timedelta(days=29))
    assert row(sid)[3] == 'Bread 3 1.50'
    result = retention.run_sweep(InMemoryObjectStorage(), now=datetime.now(timezone.utc) + timedelta(days=31))
    assert result['inputsCleared'] == 1 and row(sid)[2:4] == (None, None)
    assert len(client.get('/api/v1/sales').json()['records']) == 1  # Confirmed rows remain.


def test_confirming_a_photo_draft_keeps_the_photo_until_the_30_day_purge(client):
    _, store = use(MockVisionModel(fail_modes=['fatal']))
    sid = client.post('/api/v1/extract', data={'saleDate': DAY},
                      files={'file': ('log.jpg', jpeg(), 'image/jpeg')}).json()['submissionId']
    body = {'submissionId': sid, 'lines': [{'itemName': 'Bread', 'quantity': 3, 'unitPrice': 1.5, 'source': 'MANUAL'}]}
    assert client.post('/api/v1/confirm', json=body).status_code == 201
    assert len(store.objects) == 1 and row(sid)[2] is not None and count('image_deletion_jobs') == 0
    assert client.get(f'/api/v1/submissions/{sid}').json()['imageUrl']
    result = retention.run_sweep(store, now=datetime.now(timezone.utc) + timedelta(days=31))
    assert (result['inputsCleared'], result['imagesDeleted']) == (1, 1)
    assert store.objects == {} and row(sid)[0] == 'CONFIRMED' and row(sid)[2] is None


def test_discarding_a_draft_removes_its_photo(client):
    _, store = use(MockVisionModel(fail_modes=['fatal']))
    sid = client.post('/api/v1/extract', data={'saleDate': DAY},
                      files={'file': ('log.jpg', jpeg(), 'image/jpeg')}).json()['submissionId']
    assert client.delete(f'/api/v1/submissions/{sid}').status_code == 204
    assert store.objects == {} and row(sid) is None


def test_retention_windows_are_the_m3_values():
    assert repository.RETENTION_DAYS == 30 and repository.STALE_SECONDS == 120
    assert retention.SWEEP_INTERVAL_SECONDS <= 24 * 60 * 60  # M3 5.4: at startup and at least daily.


# ---- Delete-all (optional addition, D-22) with partial failures ------------------------------
def test_delete_all_with_storage_down_deletes_records_and_retries_photos(client):
    _, store = use(MockVisionModel(fail_modes=['fatal']))
    client.post('/api/v1/extract', data={'saleDate': DAY}, files={'file': ('log.jpg', jpeg(), 'image/jpeg')})
    client.post('/api/v1/manual-entries', json={'submissionId': str(uuid4()), 'saleDate': DAY, 'lines': [
        {'itemName': 'Bread', 'quantity': 1, 'unitPrice': 1}, {'itemName': 'Milk', 'quantity': 1, 'unitPrice': 1}]})
    store.fail_deletes = True
    result = client.post('/api/v1/data/delete-all', json={'confirmation': 'DELETE'}).json()
    assert (result['recordsDeleted'], result['imagesPendingDeletion']) == (2, 1)
    assert result['message'].startswith('2 records deleted') and 'photos will be removed' in result['message']
    assert count('submissions') == 0 and len(store.objects) == 1
    store.fail_deletes = False
    retention.run_sweep(store)
    assert store.objects == {} and count('image_deletion_jobs') == 0


def test_delete_all_database_failure_deletes_nothing_and_leaves_storage_alone(client, monkeypatch):
    _, store = use(MockVisionModel(fail_modes=['fatal']))
    client.post('/api/v1/extract', data={'saleDate': DAY}, files={'file': ('log.jpg', jpeg(), 'image/jpeg')})

    def broken(conn, key):
        raise psycopg.OperationalError('connection lost')
    monkeypatch.setattr(repository, '_enqueue_image_deletion', broken)
    response = client.post('/api/v1/data/delete-all', json={'confirmation': 'DELETE'})
    assert response.status_code == 500 and response.json()['error']['code'] == 'DATABASE_ERROR'
    assert count('submissions') == 1 and len(store.objects) == 1 and count('image_deletion_jobs') == 0


@pytest.mark.parametrize('typed', ['delete', 'DELETE ', '', 'yes'])
def test_delete_all_requires_typed_delete(client, typed):
    manual(client, AS_OF)
    response = client.post('/api/v1/data/delete-all', json={'confirmation': typed})
    assert response.status_code == 400 and 'confirmation' in response.json()['error']['fields']
    assert count('sales_records') == 1


# ---- Orphaned photos (M3 5.4: the object is deleted when createSubmission fails) -------------
def test_orphaned_photos_are_found_and_deleted_by_the_sweep(db):
    store = InMemoryObjectStorage()
    old = store.put_object(b'old', 'image/jpeg')
    store.stored_at[old] -= timedelta(hours=2)
    fresh = store.put_object(b'new', 'image/jpeg')  # Possibly mid-upload: left alone.
    result = retention.run_sweep(store)
    assert result['orphansQueued'] == 1 and set(store.objects) == {fresh}


def test_photo_stored_but_submission_insert_failed_is_cleaned_up(client, monkeypatch):
    _, store = use(MockVisionModel())
    store.fail_deletes = True  # The compensating delete fails too.

    def broken(*args, **kwargs):
        raise psycopg.OperationalError('down')
    with monkeypatch.context() as patch:
        patch.setattr(repository, 'create_submission', broken)
        assert client.post('/api/v1/extract', data={'saleDate': DAY},
                           files={'file': ('log.jpg', jpeg(), 'image/jpeg')}).status_code == 500
    (key,) = store.objects
    store.fail_deletes = False
    store.stored_at[key] -= timedelta(hours=2)
    retention.run_sweep(store)
    assert store.objects == {}


# ---- Masking (M3 9.5): contacts are removed, sales values are not ----------------------------
def test_extraction_sends_masked_text_and_keeps_sales_values(client):
    model, _ = use(MockVisionModel())
    text = 'Call Ana 809 555 1234 or ana@example.do\nBread 3 1.50\nRice 100 2500\nBeef 1 1500000\n2026-04-30 Milk 2 1.25'
    draft = client.post('/api/v1/extract', data={'saleDate': DAY, 'text': text}).json()
    sent = model.calls[0][1]
    assert sent == 'Call Ana [removed] or [removed]\nBread 3 1.50\nRice 100 2500\nBeef 1 1500000\n2026-04-30 Milk 2 1.25'
    assert draft['maskedText'] == sent
    assert {line['itemName']: line['quantity'] for line in draft['lines']}['Rice'] == '100'


def test_masking_examples_from_m3_9_5_and_the_reviewed_refinements():
    # M3 9.5 examples, plus D-20 punctuation and D-28 all-space and "1-" forms.
    for phone in ('+1 809 555 1234', '(809) 555-1234', '809-555-1234', '809.555.1234', '555-1234', '8095551234',
                  '809 555 1234', '1-809-555-1234', '(809) 555 1234'):
        assert privacy.mask_text(f'Call {phone} now') == 'Call [removed] now', phone
    # M3 9.5: quantities, prices and ISO dates stay intact. A unit price may have up to seven
    # digits (M3 5.4: at most 9,999,999.99), so runs of 7 to 9 digits are never masked.
    for sale in ('Coke 2 1.50 3.00', 'Sold 120 bread', 'Rice 100 2500', '2026-09-01 Bread 3', 'Total 1,250.75',
                 '1234567.89', 'Bread 3 150 2000', 'Eggs 30 5 150', 'Beef 1 1500000', 'Stock 5551234', 'Lot 9999999'):
        assert privacy.mask_text(sale) == sale


# ---- Extraction rounding (defect: 1.234 became 1.23 before review) ---------------------------
def test_unrounded_quantity_reaches_review_and_confirmation_enforces_the_rule(client):
    use(MockVisionModel(canned=ExtractionResult(overallConfidence=0.9, lines=[ExtractedLine(
        itemName='Rice (lb)', quantity=Decimal('1.234'), unitPrice=Decimal('0.80'), itemNameConfidence=0.99,
        quantityConfidence=0.99, unitPriceConfidence=0.99)])))
    draft = client.post('/api/v1/extract', data={'saleDate': DAY, 'text': 'Rice (lb) 1.234 0.80'}).json()
    line = draft['lines'][0]
    assert (line['quantity'], line['bands']['quantity']) == ('1.234', 'MEDIUM')
    body = {'submissionId': draft['submissionId'], 'lines': [{'draftLineId': line['lineId'], 'itemName': 'Rice (lb)',
                                                              'quantity': '1.234', 'unitPrice': '0.80',
                                                              'verifiedFields': ['quantity']}]}
    rejected = client.post('/api/v1/confirm', json=body)
    assert rejected.status_code == 422 and 'lines.0.quantity' in rejected.json()['error']['fields']
    body['lines'][0]['quantity'] = '1.23'
    assert client.post('/api/v1/confirm', json=body).status_code == 201


def test_draft_line_ids_are_stable_across_reopening(client):
    use(MockVisionModel())
    sid = client.post('/api/v1/extract', data={'saleDate': DAY, 'text': 'Bread 3 1.50\nMilk 2 1.20'}).json()['submissionId']
    first = [line['lineId'] for line in client.get(f'/api/v1/submissions/{sid}').json()['lines']]
    assert first == [1, 2] == [line['lineId'] for line in client.get(f'/api/v1/submissions/{sid}').json()['lines']]


# ---- Error contract (M3 4.2 envelope; status codes per D-30) ---------------------------------
def test_confirmation_errors_are_422_with_field_paths_and_extraction_errors_are_400(client):
    use(MockVisionModel(canned=ExtractionResult(overallConfidence=0.9, lines=[ExtractedLine(
        itemName='Bread', quantity=Decimal('3'), unitPrice=Decimal('1.50'), itemNameConfidence=0.7,
        quantityConfidence=0.99, unitPriceConfidence=0.99)])))
    draft = client.post('/api/v1/extract', data={'saleDate': DAY, 'text': 'Bread 3 1.50'}).json()
    line = draft['lines'][0]
    unverified = client.post('/api/v1/confirm', json={'submissionId': draft['submissionId'], 'lines': [
        {'draftLineId': line['lineId'], 'itemName': 'Bread', 'quantity': '3', 'unitPrice': '1.50'}]})
    # FR-208: the MEDIUM item name must be ticked or edited.
    assert unverified.status_code == 422
    assert unverified.json()['error']['fields'] == {'lines.0.itemName': 'Row 1: tick or correct this value.'}
    extract = client.post('/api/v1/extract', data={'saleDate': '2026-02-30', 'text': 'Bread 3'})
    assert extract.status_code == 400 and set(extract.json()['error']['fields']) == {'saleDate'}


def test_analysis_and_guidance_date_errors_are_400(client):
    # Defect: an asOf in the future answered 422 once the blanket 422 -> 400 mapping was removed.
    for response in (client.get('/api/v1/analysis?asOf=2999-01-01'),
                     client.post('/api/v1/guidance', json={'asOf': '2999-01-01'})):
        assert response.status_code == 400 and response.json()['error']['code'] == 'VALIDATION_ERROR'
        assert 'asOf' in response.json()['error']['fields']


def test_unexpected_errors_still_use_the_envelope(client, monkeypatch):
    def boom(*_):
        raise RuntimeError('SECRET internal detail')
    monkeypatch.setattr(repository, 'list_open_submissions', boom)
    response = client.get('/api/v1/submissions')
    assert response.status_code == 500 and 'SECRET' not in response.text
    assert response.json()['error']['code'] == 'INTERNAL_ERROR' and response.json()['error']['message']


def test_unknown_route_method_uses_the_envelope(client):
    response = client.put('/api/v1/sales')
    assert response.status_code == 405 and response.json()['error']['code'] == 'METHOD_NOT_ALLOWED'


# ---- GET /sales range and pagination (defect: limit/offset ignored) --------------------------
def test_sales_range_default_window_and_validation(client):
    for offset in (0, 27, 28, 100):
        manual(client, AS_OF - timedelta(days=offset), name=f'D{offset}')
    default = client.get('/api/v1/sales').json()
    assert (default['start'], default['end']) == ((AS_OF - timedelta(days=27)).isoformat(), DAY)
    assert {r['itemName'] for r in default['records']} == {'D0', 'D27'}
    ranged = client.get('/api/v1/sales', params={'start': (AS_OF - timedelta(days=100)).isoformat(),
                                                  'end': (AS_OF - timedelta(days=28)).isoformat()}).json()
    assert {r['itemName'] for r in ranged['records']} == {'D28', 'D100'}
    only_end = client.get('/api/v1/sales', params={'end': (AS_OF - timedelta(days=28)).isoformat()}).json()
    assert only_end['start'] == (AS_OF - timedelta(days=55)).isoformat()
    assert client.get('/api/v1/sales', params={'start': '2026-04-31'}).status_code == 400


def test_sales_pages_are_stable_and_complete(client):
    for offset in range(3):
        manual(client, AS_OF - timedelta(days=offset), rows=3)
    seen = []
    for offset in (0, 4, 8):
        page = client.get('/api/v1/sales', params={'limit': 4, 'offset': offset}).json()
        assert (page['limit'], page['offset'], page['total']) == (4, offset, 9)
        assert page['hasMore'] is (offset + 4 < 9)
        seen += [r['recordId'] for r in page['records']]
    assert len(seen) == len(set(seen)) == 9
    dates = [r['saleDate'] for r in client.get('/api/v1/sales', params={'limit': 9}).json()['records']]
    assert dates == sorted(dates, reverse=True)


# ---- Analysis and guidance follow M3 5.5 (WorkDivision: 7-day moving averages and trends) ----
def test_analysis_uses_the_latest_seven_logged_days_with_trend_and_suggested_stock(client):
    """Regression for the reverted all-history formula: 14 logged days, 2 per day then 4 per day.
    M3 5.5: average = mean of the last 7 logged days (4.0, not the all-history 3.0); trend UP
    (more than 10% above the prior 7); suggested stock = ceil(4.0 x 7) = 28."""
    for back in range(14):
        manual(client, AS_OF - timedelta(days=back), quantity=4 if back < 7 else 2)
    result = client.get('/api/v1/analysis').json()
    bread = result['estimates'][0]
    assert (result['status'], result['loggedDays'], result['requiredDays']) == ('READY', 14, 7)
    assert (bread['currentAverageDisplay'], bread['trend'], bread['suggestedQty']) == ('4.0', 'UP', '28')
    with connect() as conn:  # Snapshot in the M3 7.1 shape (SD-03 replaceBaselines).
        assert conn.execute("""SELECT window_days, logged_days, average_daily_quantity, prior_average_daily_quantity,
            trend, cover_days, suggested_restock_qty FROM baseline_estimates""").fetchone() == (
            7, 14, Decimal('4'), Decimal('2'), 'UP', 7, 28)


def test_fewer_than_seven_logged_days_gives_no_estimate_and_guidance_409(client):
    provider = MockLLMService()
    app.dependency_overrides[get_wording_provider] = lambda: provider
    for back in range(6):
        manual(client, AS_OF - timedelta(days=back), quantity=3)
    result = client.get('/api/v1/analysis').json()
    assert (result['status'], result['loggedDays']) == ('INSUFFICIENT_HISTORY', 6)
    assert all(e['suggestedQty'] is None and e['currentAverage'] is None for e in result['estimates'])
    refused = client.post('/api/v1/guidance', json={})
    assert refused.status_code == 409 and refused.json()['error']['code'] == 'INSUFFICIENT_HISTORY'
    assert provider.calls == [] and count('baseline_estimates') == 0  # No model call; nothing saved.


def test_guidance_uses_the_requested_as_of_for_evidence(client):
    provider = MockLLMService()
    app.dependency_overrides[get_wording_provider] = lambda: provider
    for back in range(1, 8):
        manual(client, AS_OF - timedelta(days=back), quantity=2)
    manual(client, AS_OF, quantity=30)
    earlier = (AS_OF - timedelta(days=1)).isoformat()
    result = client.post('/api/v1/guidance', json={'asOf': earlier}).json()
    assert result['asOf'] == earlier and result['items'][0]['suggestedQty'] == '14'
    assert (result['source'], result['groundingPassed']) == ('AI_WORDED', True)
    evidence = provider.calls[0].items[0]
    assert (evidence.suggestedQty, evidence.averageOneDecimal) == ('14', '2.0')  # The later 30 is not sent.


@pytest.mark.parametrize('quantity', ['0', '-1', '100000', '1.234'])
def test_quantities_outside_the_final_limits_are_rejected_before_analysis(client, quantity):
    response = client.post('/api/v1/manual-entries', json={'submissionId': str(uuid4()), 'saleDate': DAY, 'lines': [
        {'itemName': 'Bread', 'quantity': quantity, 'unitPrice': 1}]})
    assert response.status_code == 422 and 'lines.0.quantity' in response.json()['error']['fields']
    assert client.get('/api/v1/analysis').json()['status'] == 'NO_DATA'


def test_extreme_stored_values_do_not_fail_analysis(client):
    sid = repository.create_submission(AS_OF)
    repository.update_status(sid, 'CLASSIFYING')
    repository.update_status(sid, 'AI_UNAVAILABLE')
    with connect() as conn:  # Written before the M3 limits (NOT VALID constraints keep old rows).
        conn.execute("ALTER TABLE sales_records DROP CONSTRAINT sales_final_limits")
        for line_no, quantity in ((1, '123456789012345678901234567890.123456'), (2, '0.000001')):
            conn.execute("""INSERT INTO sales_records(id,submission_id,line_no,item_name,quantity,unit_price,source)
                VALUES (%s,%s,%s,'Bulk',%s,1,'MANUAL')""", (uuid4(), sid, line_no, quantity))
        conn.execute("UPDATE submissions SET status='CONFIRMED', draft_payload=NULL WHERE id=%s", (sid,))
    manual(client, AS_OF - timedelta(days=1), name='Bulk')
    response = client.get('/api/v1/analysis')
    assert response.status_code == 200 and response.json()['status'] == 'INSUFFICIENT_HISTORY'
    series = response.json()['estimates'][0]['series']
    assert [point['quantity'] for point in series] == ['1', '123456789012345678901234567890.123457']


def test_default_as_of_uses_the_application_clock_in_app_timezone(monkeypatch):
    from app.clock import SystemClock
    monkeypatch.setenv('APP_TIMEZONE', 'Pacific/Kiritimati')  # UTC+14 and UTC-11: always different dates.
    ahead = SystemClock().today()
    monkeypatch.setenv('APP_TIMEZONE', 'Pacific/Pago_Pago')
    assert SystemClock().today() < ahead


def test_invalid_timezone_configuration_is_a_controlled_error(db, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv('APP_TIMEZONE', 'Not/AZone')
    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get('/api/v1/analysis', headers={'Authorization': 'Bearer test-code'})
    assert response.status_code == 503


# ---- WorkDivision step 2: SQL seed script with synthetic bread and milk sales ----------------
def test_sql_seed_script_is_repeatable_and_feeds_the_analysis(db):
    from pathlib import Path

    from app.analysis import BaselineAnalyzer
    script = (Path(__file__).resolve().parents[1] / 'seeds' / 'synthetic_sales.sql').read_text(encoding='utf-8')
    for _ in range(2):  # The second run must add nothing.
        with connect() as conn:
            conn.execute(script)
    with connect() as conn:
        today = conn.execute('SELECT CURRENT_DATE').fetchone()[0]
        assert conn.execute("SELECT count(*), count(DISTINCT s.sale_date), max(s.sale_date) FROM sales_records r "
                            "JOIN submissions s ON s.id = r.submission_id WHERE s.status = 'CONFIRMED'"
                            ).fetchone() == (28, 14, today - timedelta(days=1))
    result = BaselineAnalyzer().analyze(today)
    figures = {e.itemName: (e.currentAverageDisplay, e.trend, e.suggestedQty) for e in result.estimates}
    assert (result.status, result.loggedDays) == ('READY', 14)
    assert figures == {'Bread': ('4.0', 'UP', Decimal('28')), 'Milk': ('2.5', 'STABLE', Decimal('18'))}
