"""Final Milestone 3 data layer and REST contract (sections 4.2, 5.6, 7.1-7.4) against PostgreSQL.

Covers the parts merged from Gabriel's unmerged branch work: the Diagram 4 lifecycle, standalone
manual entry, pending drafts, deletion, the date-range ledger, retention and image cleanup.
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid1, uuid4

import psycopg
import pytest

from app import repository, retention
from app.database import connect
from app.ingestion import InferenceService
from app.logging_setup import JsonFormatter
from app.main import app
from app.storage import InMemoryObjectStorage, StorageError, get_object_storage
from app.vision import ExtractedLine, ExtractionResult, MockVisionModel
from conftest import AS_OF

STATES = ('UPLOADED', 'CLASSIFYING', 'NEEDS_VERIFICATION', 'AI_UNAVAILABLE', 'REJECTED', 'CONFIRMED')
ALLOWED = {('UPLOADED', 'CLASSIFYING'), ('UPLOADED', 'AI_UNAVAILABLE'),
           ('CLASSIFYING', 'NEEDS_VERIFICATION'), ('CLASSIFYING', 'REJECTED'), ('CLASSIFYING', 'AI_UNAVAILABLE'),
           ('NEEDS_VERIFICATION', 'CONFIRMED'), ('AI_UNAVAILABLE', 'CONFIRMED'), ('REJECTED', 'CONFIRMED')}
PATHS = {'UPLOADED': [], 'CLASSIFYING': ['CLASSIFYING'], 'NEEDS_VERIFICATION': ['CLASSIFYING', 'NEEDS_VERIFICATION'],
         'AI_UNAVAILABLE': ['CLASSIFYING', 'AI_UNAVAILABLE'], 'REJECTED': ['CLASSIFYING', 'REJECTED'],
         'CONFIRMED': ['CLASSIFYING', 'NEEDS_VERIFICATION']}


def submission_in(state, sale_date=AS_OF, **kwargs):
    sid = repository.create_submission(sale_date, **kwargs)
    for step in PATHS[state]:
        repository.update_status(sid, step)
    if state == 'CONFIRMED':
        _move(sid, 'CONFIRMED', sale_date)
    return sid


def _move(sid, target, sale_date=AS_OF):
    with connect() as conn:
        if target == 'CONFIRMED':
            conn.execute("INSERT INTO sales_records(id,submission_id,line_no,item_name,quantity,unit_price,source) "
                         "VALUES (%s,%s,1,'Bread',1,1,'MANUAL')", (uuid4(), sid))
        conn.execute("UPDATE submissions SET status=%s, draft_payload=NULL WHERE id=%s", (target, sid))


def status_of(sid):
    with connect() as conn:
        row = conn.execute('SELECT status FROM submissions WHERE id=%s', (sid,)).fetchone()
    return row[0] if row else None


def line(**overrides):
    return {'itemName': 'Bread', 'quantity': '2', 'unitPrice': '1.50', **overrides}


@pytest.fixture
def storage():
    store = InMemoryObjectStorage()
    app.dependency_overrides[get_object_storage] = lambda: store
    yield store


# ---- Diagram 4 lifecycle --------------------------------------------------------------------
@pytest.mark.parametrize('source', STATES)
@pytest.mark.parametrize('target', STATES)
def test_every_state_pair_follows_diagram_4(db, source, target):
    sid = submission_in(source)
    allowed = source == target or (source, target) in ALLOWED
    if allowed:
        if source != target:
            _move(sid, target)
        assert status_of(sid) == target
    else:
        with pytest.raises(psycopg.errors.CheckViolation):
            _move(sid, target)
        assert status_of(sid) == source


def test_only_manual_entry_may_be_inserted_confirmed(db):
    with pytest.raises(psycopg.errors.CheckViolation), connect() as conn:
        conn.execute("INSERT INTO owners(id) VALUES (%s) ON CONFLICT DO NOTHING", (repository.owner_id(),))
        conn.execute("INSERT INTO submissions(id,owner_id,sale_date,input_type,status) VALUES (%s,%s,%s,'TEXT','CONFIRMED')",
                     (uuid4(), repository.owner_id(), AS_OF))


def test_confirmation_records_timestamps_and_clears_the_draft(client):
    sid = submission_in('NEEDS_VERIFICATION')
    assert client.post('/api/v1/confirm', json={'submissionId': str(sid), 'lines': [line(source='MANUAL')]}).status_code == 201
    with connect() as conn:
        confirmed, updated, created, draft = conn.execute(
            'SELECT confirmed_at, updated_at, created_at, draft_payload FROM submissions WHERE id=%s', (sid,)).fetchone()
    assert confirmed is not None and updated >= created and draft is None


@pytest.mark.parametrize('state', ['NEEDS_VERIFICATION', 'AI_UNAVAILABLE', 'REJECTED'])
def test_all_open_states_accept_the_same_confirm(client, state):
    sid = submission_in(state)
    response = client.post('/api/v1/confirm', json={'submissionId': str(sid), 'lines': [line(source='MANUAL')]})
    assert response.status_code == 201, response.text
    assert status_of(sid) == 'CONFIRMED'


@pytest.mark.parametrize('state', ['UPLOADED', 'CLASSIFYING'])
def test_processing_states_cannot_be_confirmed(client, state):
    sid = submission_in(state)
    response = client.post('/api/v1/confirm', json={'submissionId': str(sid), 'lines': [line()]})
    assert response.status_code == 409 and response.json()['error']['code'] == 'INVALID_STATE'


# ---- Standalone manual entry (FR-104, SD-04) -------------------------------------------------
def test_manual_entry_creates_a_confirmed_manual_submission_once(client):
    body = {'submissionId': str(uuid4()), 'saleDate': AS_OF.isoformat(),
            'lines': [line(itemName='Rice (lb)', quantity='2.25', unitPrice='0.80'), line(itemName='Milk', quantity='1')]}
    first = client.post('/api/v1/manual-entries', json=body)
    assert first.status_code == 201, first.text
    again = client.post('/api/v1/manual-entries', json=body)  # A retry after a network error.
    assert again.status_code == 200 and again.json()['recordIds'] == first.json()['recordIds']
    assert again.json()['created'] is False
    detail = client.get('/api/v1/submissions/' + body['submissionId']).json()
    assert (detail['status'], detail['inputType']) == ('CONFIRMED', 'MANUAL')
    assert [(r['itemName'], r['source'], r['lineNo']) for r in detail['records']] == [
        ('Rice (lb)', 'MANUAL', 1), ('Milk', 'MANUAL', 2)]
    assert len(client.get('/api/v1/sales').json()['records']) == 2


@pytest.mark.parametrize('changes,field', [
    ({'submissionId': str(uuid1())}, 'submissionId'),
    ({'saleDate': (AS_OF + timedelta(days=1)).isoformat()}, 'saleDate'),
    ({'saleDate': (AS_OF - timedelta(days=366)).isoformat()}, 'saleDate'),
    ({'lines': []}, 'lines'),
    ({'lines': [line(quantity='0')]}, 'lines.0.quantity'),
    ({'lines': [line(quantity='1.234')]}, 'lines.0.quantity'),
    ({'lines': [line(itemName=' ')]}, 'lines.0.itemName'),
])
def test_manual_entry_validation(client, changes, field):
    body = {'submissionId': str(uuid4()), 'saleDate': AS_OF.isoformat(), 'lines': [line()], **changes}
    response = client.post('/api/v1/manual-entries', json=body)
    assert response.status_code == 400, response.text
    assert field in response.json()['error']['fields']
    with connect() as conn:
        assert conn.execute('SELECT count(*) FROM submissions').fetchone()[0] == 0


def test_manual_entry_id_of_an_open_submission_is_a_conflict(client):
    sid = submission_in('NEEDS_VERIFICATION')
    body = {'submissionId': str(sid), 'saleDate': AS_OF.isoformat(), 'lines': [line()]}
    response = client.post('/api/v1/manual-entries', json=body)
    assert response.status_code == 409
    assert status_of(sid) == 'NEEDS_VERIFICATION'


# ---- Pending drafts, deletion and the ledger (FR-216, FR-304, FR-303) ------------------------
def test_pending_drafts_lists_open_submissions_newest_first(client):
    rejected = submission_in('REJECTED')
    unavailable = submission_in('AI_UNAVAILABLE')
    review = submission_in('NEEDS_VERIFICATION', draft_payload={'lines': [line(lineId=1)]})
    submission_in('CONFIRMED')
    submission_in('CLASSIFYING')
    rows = client.get('/api/v1/submissions').json()
    assert [row['submissionId'] for row in rows] == [str(review), str(unavailable), str(rejected)]
    assert rows[0]['lineCount'] == 1 and rows[0]['inputType'] == 'TEXT'
    assert set(rows[0]) == {'submissionId', 'status', 'inputType', 'saleDate', 'createdAt', 'lineCount'}


def test_stale_processing_submissions_become_ai_unavailable(db):
    uploaded = submission_in('UPLOADED')
    classifying = submission_in('CLASSIFYING')
    fresh_now = datetime.now(timezone.utc)
    assert repository.mark_stale(fresh_now) == 0  # Not yet 120 s old.
    assert repository.mark_stale(fresh_now + timedelta(seconds=121)) == 2
    assert status_of(uploaded) == status_of(classifying) == 'AI_UNAVAILABLE'
    assert repository.get_submission(classifying)['message'] == repository.STALE_MESSAGE


def test_late_extraction_result_never_overwrites_the_stale_outcome(db):
    sid = submission_in('UPLOADED', input_type='TEXT', masked_text='Bread 3 1.50')

    class SweptDuringCall(MockVisionModel):
        def extract_from_text(self, text, prompt, timeout):
            # The retention sweep runs while the provider call is still in flight.
            repository.mark_stale(datetime.now(timezone.utc) + timedelta(seconds=121))
            return ExtractionResult(overallConfidence=0.9, lines=[ExtractedLine(
                itemName='Bread', quantity=3, unitPrice=1.5, itemNameConfidence=0.9,
                quantityConfidence=0.9, unitPriceConfidence=0.9)])

    InferenceService(SweptDuringCall(), sleep=lambda _: None).extract_from_text(sid, 'Bread 3 1.50')
    detail = repository.get_submission(sid)
    assert detail['status'] == 'AI_UNAVAILABLE' and detail['lines'] == []


def test_discarding_an_open_draft_deletes_it_and_its_image(client, storage):
    key = storage.put_object(b'jpeg', 'image/jpeg')
    sid = submission_in('REJECTED', image_key=key, input_type='IMAGE')
    response = client.delete('/api/v1/submissions/' + str(sid))
    assert response.status_code == 204 and response.headers['x-records-deleted'] == '0'
    assert status_of(sid) is None
    assert key not in storage.objects and repository.pending_image_deletions() == []
    assert client.delete('/api/v1/submissions/' + str(sid)).status_code == 404


def test_deleting_confirmed_sales_cascades_and_clears_every_snapshot(client, storage):
    from conftest import logged_dates
    for day in logged_dates(7):
        assert client.post('/api/v1/manual-entries', json={'submissionId': str(uuid4()), 'saleDate': day.isoformat(),
                                                          'lines': [line(), line(itemName='Milk')]}).status_code == 201
    assert client.get('/api/v1/analysis').json()['status'] == 'ESTIMATE_ONLY'
    assert len(repository.get_latest_baselines()) == 2
    target = client.get('/api/v1/sales').json()['records'][0]['submissionId']
    response = client.delete('/api/v1/submissions/' + target)
    assert response.status_code == 204 and response.headers['x-records-deleted'] == '2'
    assert repository.get_latest_baselines() == []  # Recomputed by the next analysis.
    assert target not in {r['submissionId'] for r in client.get('/api/v1/sales').json()['records']}
    assert client.get('/api/v1/analysis').json()['loggedDays'] == 6


@pytest.mark.parametrize('state', ['UPLOADED', 'CLASSIFYING'])
def test_processing_submissions_cannot_be_deleted(client, state):
    sid = submission_in(state)
    response = client.delete('/api/v1/submissions/' + str(sid))
    assert response.status_code == 409 and status_of(sid) == state


def test_deletion_is_owner_scoped(client, monkeypatch):
    sid = submission_in('REJECTED')
    owner = str(repository.owner_id())
    monkeypatch.setenv('OWNER_ID', str(uuid4()))
    assert client.delete('/api/v1/submissions/' + str(sid)).status_code == 404
    monkeypatch.setenv('OWNER_ID', owner)
    assert status_of(sid) == 'REJECTED'


def test_failed_image_delete_stays_queued_until_a_sweep_succeeds(client, storage):
    key = storage.put_object(b'jpeg', 'image/jpeg')
    sid = submission_in('AI_UNAVAILABLE', image_key=key, input_type='IMAGE')
    original_delete = storage.delete_object
    storage.delete_object = lambda _key: (_ for _ in ()).throw(StorageError('outage'))
    assert client.delete('/api/v1/submissions/' + str(sid)).status_code == 204  # The database delete stands.
    assert repository.pending_image_deletions() == [key] and key in storage.objects
    storage.delete_object = original_delete
    assert retention.run_sweep(storage)['imagesDeleted'] == 1
    assert repository.pending_image_deletions() == [] and key not in storage.objects


def test_ledger_date_range_default_and_bounds(client):
    for day in (AS_OF, AS_OF - timedelta(days=27), AS_OF - timedelta(days=28)):
        client.post('/api/v1/manual-entries', json={'submissionId': str(uuid4()), 'saleDate': day.isoformat(),
                                                   'lines': [line(itemName=f'Item {day.day}')]})
    default = client.get('/api/v1/sales').json()
    assert (default['start'], default['end']) == ((AS_OF - timedelta(days=27)).isoformat(), AS_OF.isoformat())
    assert [r['saleDate'] for r in default['records']] == [AS_OF.isoformat(), (AS_OF - timedelta(days=27)).isoformat()]
    wide = client.get('/api/v1/sales', params={'start': (AS_OF - timedelta(days=365)).isoformat(), 'end': AS_OF.isoformat()})
    assert len(wide.json()['records']) == 3
    record = default['records'][0]
    assert set(record) == {'recordId', 'submissionId', 'saleDate', 'lineNo', 'itemName', 'quantity', 'unitPrice',
                           'lineTotal', 'source'}
    assert record['lineTotal'] == '3.00'


# ---- Retention (FR-215, NFR-715, M3 7.4) ----------------------------------------------------
def test_thirty_day_purge(db):
    store = InMemoryObjectStorage()
    open_key = store.put_object(b'a', 'image/jpeg')
    confirmed_key = store.put_object(b'b', 'image/jpeg')
    old_open = submission_in('REJECTED', image_key=open_key, input_type='IMAGE')
    old_confirmed = submission_in('CONFIRMED', image_key=confirmed_key, input_type='IMAGE')
    with connect() as conn:
        conn.execute("UPDATE submissions SET masked_text='x' WHERE id=%s", (old_confirmed,))
    repository.replace_baselines(AS_OF - timedelta(days=40), [{
        'itemName': 'Bread', 'itemKey': 'bread', 'windowDays': 7, 'loggedDays': 7, 'averageDailyQuantity': 1,
        'priorAverageDailyQuantity': None, 'trend': 'STABLE', 'suggestedRestockQty': 7}])

    later = datetime.now(timezone.utc) + timedelta(days=31)
    result = retention.run_sweep(store, now=later)
    assert (result['openDeleted'], result['inputsCleared'], result['imagesDeleted']) == (1, 1, 2)
    assert result['snapshotsDeleted'] == 1
    assert status_of(old_open) is None
    assert status_of(old_confirmed) == 'CONFIRMED'
    with connect() as conn:
        assert conn.execute('SELECT image_key, masked_text FROM submissions WHERE id=%s',
                            (old_confirmed,)).fetchone() == (None, None)
        assert conn.execute('SELECT count(*) FROM sales_records WHERE submission_id=%s', (old_confirmed,)).fetchone()[0] == 1
    assert store.objects == {}
    # Nothing younger than 30 days is touched.
    young = submission_in('REJECTED')
    assert retention.run_sweep(store)['openDeleted'] == 0 and status_of(young) == 'REJECTED'


def test_sweep_survives_storage_outage(db):
    class Down(InMemoryObjectStorage):
        def delete_object(self, key):
            raise StorageError('down')
    store = Down()
    key = store.put_object(b'a', 'image/jpeg')
    submission_in('REJECTED', image_key=key, input_type='IMAGE')
    result = retention.run_sweep(store, now=datetime.now(timezone.utc) + timedelta(days=31))
    assert (result['openDeleted'], result['imageDeletesFailed']) == (1, 1)
    assert repository.pending_image_deletions() == [key]


# ---- Error envelope and structured logs (M3 4.2, NFR-723) ------------------------------------
def test_error_envelope_for_each_status(client):
    missing = client.get('/api/v1/submissions/' + str(uuid4()))
    assert missing.status_code == 404 and missing.json()['error']['code'] == 'NOT_FOUND'
    unauthorized = client.get('/api/v1/sales', headers={'Authorization': 'Bearer nope'})
    assert unauthorized.status_code == 401
    assert unauthorized.json()['error'] == {'code': 'UNAUTHORIZED', 'fields': {},
                                            'message': 'Your access code was not accepted. Please sign in again.'}
    for response in (missing, unauthorized):
        message = response.json()['error']['message']
        assert len(message.split()) <= 30 and 'Traceback' not in message  # NFR-731


def test_request_log_line_has_the_nfr_723_fields_and_no_inputs(client, caplog):
    caplog.set_level(logging.INFO)
    secret_text = 'Call 809-555-1234 Bread 3'
    client.post('/api/v1/extract', data={'saleDate': 'bad', 'text': secret_text})
    client.get('/api/v1/sales', params={'start': '2026-04-01'})
    lines = [json.loads(JsonFormatter().format(record)) for record in caplog.records if record.name == 'bodega.request']
    assert lines, 'no request log lines'
    failed = next(entry for entry in lines if entry['status'] == 400)
    assert {'timestamp', 'requestId', 'component', 'operation', 'errorCategory', 'durationMs'} <= set(failed)
    assert failed['operation'] == 'POST /api/v1/extract' and failed['errorCategory'] == 'HTTP_400'
    assert failed['timestamp'].endswith('+00:00')
    # Backend loggers only: the test client's own httpx logger prints request URLs client-side.
    everything = '\n'.join(JsonFormatter().format(record) for record in caplog.records
                           if not record.name.startswith('httpx'))
    for forbidden in ('809-555-1234', 'Bread 3', 'test-code', '2026-04-01'):
        assert forbidden not in everything


def test_ai_storage_and_database_failures_log_every_nfr_723_field(client, caplog):
    """SRS 7.3 NFR-723 method: trigger AI and storage failures; check log fields and no sensitive data."""
    import io
    from PIL import Image
    from app.storage import InMemoryObjectStorage, get_object_storage
    from app.vision import MockVisionModel, get_vision_model
    caplog.set_level(logging.INFO)
    secret = 'Call 809-555-1234 Bread 3'
    model = MockVisionModel(fail_modes=['fatal'])
    app.dependency_overrides[get_vision_model] = lambda: model
    app.dependency_overrides[get_object_storage] = lambda: InMemoryObjectStorage(fail_puts=True)
    assert client.post('/api/v1/extract', data={'saleDate': AS_OF.isoformat(), 'text': secret}).json()['status'] == 'AI_UNAVAILABLE'
    out = io.BytesIO()
    Image.new('RGB', (20, 20), 'white').save(out, format='JPEG')
    assert client.post('/api/v1/extract', data={'saleDate': AS_OF.isoformat()},
                       files={'file': ('log.jpg', out.getvalue(), 'image/jpeg')}).status_code == 503
    with connect() as conn:
        conn.execute("CREATE FUNCTION boom() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'x'; END $$")
        conn.execute('CREATE TRIGGER boom BEFORE INSERT ON submissions FOR EACH ROW EXECUTE FUNCTION boom()')
    assert client.post('/api/v1/extract', data={'saleDate': AS_OF.isoformat(), 'text': secret}).status_code == 500
    lines = [json.loads(JsonFormatter().format(r)) for r in caplog.records
             if r.levelno >= logging.WARNING and not r.name.startswith('httpx')]
    categories = {line['errorCategory'] for line in lines}
    assert {'AI_FATAL', 'STORAGE_UNAVAILABLE', 'RaiseException', 'HTTP_500', 'HTTP_503'} <= categories, categories
    for line in lines:
        assert {'timestamp', 'requestId', 'component', 'operation', 'errorCategory', 'durationMs'} <= set(line), line
        assert line['timestamp'].endswith('+00:00') and isinstance(line['durationMs'], (int, float))
    everything = '\n'.join(json.dumps(line) for line in lines)
    for forbidden in ('809-555-1234', 'Bread 3', 'test-code', '[removed]'):
        assert forbidden not in everything
