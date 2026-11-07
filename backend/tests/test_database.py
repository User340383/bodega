"""Integration tests run in an isolated schema on TEST_DATABASE_URL only."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql

from app.database import connect, migrate
from app import repository
from app.main import app


@pytest.fixture
def db(monkeypatch):
    url = os.getenv('TEST_DATABASE_URL')
    if not url:
        pytest.skip('TEST_DATABASE_URL must point to a disposable PostgreSQL database')
    schema = 'test_' + uuid4().hex
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    monkeypatch.setenv('DATABASE_URL', url)
    monkeypatch.setenv('BODEGA_DB_SCHEMA', schema)
    monkeypatch.setenv('OWNER_ID', '00000000-0000-4000-8000-000000000001')
    monkeypatch.setenv('APP_ACCESS_CODE', 'test-code')
    migrate()
    try:
        yield
    finally:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


@pytest.fixture
def client(db):
    with TestClient(app, raise_server_exceptions=False) as client:
        client.headers['Authorization'] = 'Bearer test-code'
        yield client


def make_submission(state='NEEDS_VERIFICATION', payload=None):
    sid = repository.create_submission(date.today(), draft_payload=payload)
    if state != 'UPLOADED':
        repository.update_status(sid, 'CLASSIFYING')
    if state not in ('UPLOADED', 'CLASSIFYING'):
        repository.update_status(sid, state)
    return sid


def body(sid, **overrides):
    line = {'itemName': ' Bread ', 'quantity': '2.5', 'unitPrice': '1.20'}
    line.update(overrides)
    return {'submissionId': str(sid), 'lines': [line]}


def counts(sid):
    with connect() as conn:
        return conn.execute('SELECT status,(SELECT count(*) FROM sales_records WHERE submission_id=s.id) FROM submissions s WHERE id=%s', (sid,)).fetchone()


def test_migration_is_repeatable_and_relationships(db):
    migrate()
    with connect() as conn:
        assert conn.execute('SELECT count(*) FROM schema_migrations').fetchone()[0] == 4
        assert conn.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema=current_schema() AND table_name IN ('owners','submissions','sales_records','baseline_estimates')").fetchone()[0] == 4
    with pytest.raises(psycopg.errors.ForeignKeyViolation), connect() as conn:
        conn.execute('INSERT INTO submissions(id,owner_id,sale_date) VALUES (%s,%s,%s)', (uuid4(), uuid4(), date.today()))


def test_confirm_and_repeat_and_history(client):
    sid = make_submission()
    assert client.get('/api/v1/sales').json()['records'] == []
    first = client.post('/api/v1/confirm', json=body(sid))
    assert first.status_code == 201, first.text
    repeated = client.post('/api/v1/confirm', json=body(sid, quantity='999'))
    assert repeated.status_code == 200
    assert not repeated.json()['created']
    assert repeated.json()['recordIds'] == first.json()['recordIds']
    assert counts(sid) == ('CONFIRMED', 1)
    records = client.get('/api/v1/sales').json()['records']
    assert len(records) == 1
    assert records[0]['itemName'] == 'Bread'
    assert records[0]['quantity'] == '2.5'
    assert records[0]['saleDate'] == date.today().isoformat()
    assert client.get('/api/v1/submissions/' + str(sid)).json()['lines'] == []


@pytest.mark.parametrize('changes', [{'itemName': '  '}, {'quantity': '0'}, {'quantity': '-1'}, {'quantity': 'NaN'}, {'quantity': 'Infinity'}, {'unitPrice': '-0.01'}, {'unitPrice': 'NaN'}, {'unitPrice': 'Infinity'}])
def test_invalid_values_leave_draft_unchanged(client, changes):
    sid = make_submission()
    assert client.post('/api/v1/confirm', json=body(sid, **changes)).status_code == 422
    assert counts(sid) == ('NEEDS_VERIFICATION', 0)


def test_empty_unknown_and_ineligible(client):
    sid = make_submission('CLASSIFYING')
    assert client.post('/api/v1/confirm', json=body(sid)).status_code == 409
    assert client.post('/api/v1/confirm', json=body(uuid4())).status_code == 404
    assert client.post('/api/v1/confirm', json={'submissionId': str(sid), 'lines': []}).status_code == 422
    assert client.post('/api/v1/confirm', json={'submissionId': 'bad', 'lines': []}).status_code == 422
    assert counts(sid) == ('CLASSIFYING', 0)


def test_database_failure_rolls_back_rows_and_status(client):
    sid = make_submission()
    with connect() as conn:
        conn.execute("CREATE FUNCTION fail_confirm() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.status='CONFIRMED' THEN RAISE EXCEPTION 'injected failure'; END IF; RETURN NEW; END $$")
        conn.execute('CREATE TRIGGER injected_failure BEFORE UPDATE ON submissions FOR EACH ROW EXECUTE FUNCTION fail_confirm()')
    response = client.post('/api/v1/confirm', json=body(sid))
    assert response.status_code == 500
    assert counts(sid) == ('NEEDS_VERIFICATION', 0)
    assert client.get('/api/v1/sales').json()['records'] == []
    with connect() as conn:
        conn.execute('DROP TRIGGER injected_failure ON submissions')
    assert client.post('/api/v1/confirm', json=body(sid)).status_code == 201


def test_partial_insert_rolls_back_on_later_verification_failure(client):
    sid = make_submission(payload={'lines': [{'lineId': 'a', 'itemName': 'Milk', 'quantity': 2, 'unitPrice': 1, 'bands': {'itemName': 'HIGH', 'quantity': 'HIGH', 'unitPrice': 'MEDIUM'}}]})
    request = body(sid, source='MANUAL')
    request['lines'].append({'draftLineId': 'a', 'itemName': 'Milk', 'quantity': 2, 'unitPrice': 1})
    assert client.post('/api/v1/confirm', json=request).status_code == 422
    assert counts(sid) == ('NEEDS_VERIFICATION', 0)
    request['lines'][1]['verifiedFields'] = ['unitPrice']
    assert client.post('/api/v1/confirm', json=request).status_code == 201
    assert counts(sid) == ('CONFIRMED', 2)


def test_concurrent_confirms_are_idempotent(client):
    sid = make_submission()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post('/api/v1/confirm', json=body(sid)), range(2)))
    assert sorted(r.status_code for r in responses) == [200, 201]
    assert responses[0].json()['recordIds'] == responses[1].json()['recordIds']
    assert counts(sid) == ('CONFIRMED', 1)


def test_manual_recovery_and_state_transitions(client):
    sid = make_submission('REJECTED')
    assert client.post('/api/v1/confirm', json=body(sid)).status_code == 409
    repository.update_status(sid, 'NEEDS_VERIFICATION')
    assert client.post('/api/v1/confirm', json=body(sid)).status_code == 201
    with pytest.raises(psycopg.errors.CheckViolation):
        repository.update_status(sid, 'CLASSIFYING')
    manual = make_submission('AI_UNAVAILABLE')
    assert client.post('/api/v1/confirm', json=body(manual)).status_code == 201
    assert all(r['source'] == 'MANUAL' for r in client.get('/api/v1/sales').json()['records'])


@pytest.mark.parametrize('quantity,price', [('0', '1'), ('-1', '1'), ('NaN', '1'), ('Infinity', '1'), ('1', '-1'), ('1', 'NaN')])
def test_sql_constraints(db, quantity, price):
    sid = make_submission()
    with pytest.raises(psycopg.errors.CheckViolation), connect() as conn:
        conn.execute("INSERT INTO sales_records VALUES (%s,%s,0,'Bread',%s,%s,%s,'MANUAL')", (uuid4(), sid, quantity, price, date.today()))
    assert counts(sid) == ('NEEDS_VERIFICATION', 0)


def test_parent_date_and_confirmation_integrity(db):
    sid = make_submission()
    with pytest.raises(psycopg.errors.ForeignKeyViolation), connect() as conn:
        conn.execute("INSERT INTO sales_records VALUES (%s,%s,0,'Bread',1,1,%s,'MANUAL')", (uuid4(), sid, date.today() - timedelta(days=1)))
    with pytest.raises(psycopg.errors.CheckViolation):
        repository.update_status(sid, 'CONFIRMED')
    with pytest.raises(psycopg.errors.CheckViolation), connect() as conn:
        conn.execute("INSERT INTO sales_records VALUES (%s,%s,0,'Bread',1,1,%s,'MANUAL')", (uuid4(), sid, date.today()))


def test_auth_and_owner_scope(client, monkeypatch):
    sid = make_submission()
    assert client.post('/api/v1/confirm', json=body(sid), headers={'Authorization': 'Bearer wrong'}).status_code == 401
    monkeypatch.setenv('OWNER_ID', str(uuid4()))
    assert client.post('/api/v1/confirm', json=body(sid)).status_code == 404
    assert client.get('/api/v1/submissions/' + str(sid)).status_code == 404
    assert client.get('/api/v1/sales').json()['records'] == []


def test_owner_cascade(client):
    sid = make_submission()
    assert client.post('/api/v1/confirm', json=body(sid)).status_code == 201
    with connect() as conn:
        conn.execute('DELETE FROM owners WHERE id=%s', (repository.owner_id(),))
    with connect() as conn:
        assert conn.execute('SELECT count(*) FROM submissions').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM sales_records').fetchone()[0] == 0


def test_manual_review_endpoint_and_placeholder_integration(client):
    sid = make_submission('REJECTED')
    url = '/api/v1/submissions/' + str(sid) + '/manual-review'
    assert client.post(url).status_code == 200
    assert client.post(url).status_code == 200
    assert counts(sid) == ('NEEDS_VERIFICATION', 0)
    assert client.post('/api/v1/confirm', json=body(sid)).status_code == 201
    assert client.post(url).status_code == 409
    assert client.post('/api/v1/submissions/' + str(uuid4()) + '/manual-review').status_code == 404
    result = client.post('/api/v1/extract', data={'saleDate': date.today().isoformat(), 'text': 'Bread 3'})
    assert result.status_code == 200
    assert result.json()['status'] == 'AI_UNAVAILABLE'
    assert result.json()['lines'] == []
    assert client.post('/api/v1/confirm', json=body(result.json()['submissionId'])).status_code == 201


def test_exact_decimal_history_and_immutable_records(client):
    sid = make_submission()
    response = client.post('/api/v1/confirm', json=body(sid, quantity='12345678901234.123456', unitPrice='0.000001'))
    assert response.status_code == 201
    record = client.get('/api/v1/sales').json()['records'][0]
    assert record['quantity'] == '12345678901234.123456'
    assert record['unitPrice'] == '0.000001'
    with pytest.raises(psycopg.errors.CheckViolation), connect() as conn:
        conn.execute('UPDATE sales_records SET quantity=1 WHERE submission_id=%s', (sid,))


def test_baseline_constraints_and_cascade(db):
    sid = make_submission()
    values = (uuid4(), repository.owner_id(), 'Bread', 7, 7, '2.5', 'UP', 18)
    with connect() as conn:
        conn.execute('INSERT INTO baseline_estimates(id,owner_id,item_name,window_days,logged_days,average_daily_quantity,trend,suggested_restock_qty) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)', values)
    for column, value in [('item_name', ' '), ('window_days', 0), ('logged_days', -1), ('average_daily_quantity', 'NaN'), ('trend', 'UNKNOWN'), ('suggested_restock_qty', -1)]:
        with pytest.raises(psycopg.errors.CheckViolation), connect() as conn:
            conn.execute(sql.SQL('UPDATE baseline_estimates SET {}=%s').format(sql.Identifier(column)), (value,))
    with connect() as conn:
        conn.execute('DELETE FROM owners WHERE id=%s', (repository.owner_id(),))
    with connect() as conn:
        assert conn.execute('SELECT count(*) FROM baseline_estimates').fetchone()[0] == 0


def test_seed_is_repeatable_and_drafts_excluded(client):
    from app.seed import seed
    seed()
    seed()
    assert len(client.get('/api/v1/sales').json()['records']) == 4
    draft = client.get('/api/v1/submissions/10000000-0000-4000-8000-000000000003')
    assert draft.json()['status'] == 'NEEDS_VERIFICATION'
    assert len(draft.json()['lines']) == 1


def test_database_rejects_whitespace_only_labels(client):
    sid = make_submission()
    assert client.post('/api/v1/confirm', json=body(sid)).status_code == 201
    for item in ('\t', '\n', ' \r\n '):
        with pytest.raises(psycopg.errors.CheckViolation), connect() as conn:
            conn.execute("INSERT INTO sales_records VALUES (%s,%s,1,%s,1,1,%s,'MANUAL')", (uuid4(), sid, item, date.today()))
        with pytest.raises(psycopg.errors.CheckViolation), connect() as conn:
            conn.execute("INSERT INTO baseline_estimates(id,owner_id,item_name,window_days,logged_days,average_daily_quantity,trend,suggested_restock_qty) VALUES (%s,%s,%s,7,7,1,'STABLE',7)", (uuid4(), repository.owner_id(), item))
    assert counts(sid) == ('CONFIRMED', 1)


def uncertain_line(**overrides):
    line = {'lineId': 'a', 'itemName': 'Bread', 'quantity': '2.5', 'unitPrice': '1.20',
            'bands': {'itemName': 'HIGH', 'quantity': 'HIGH', 'unitPrice': 'MEDIUM'}}
    line.update(overrides)
    return line


@pytest.mark.parametrize('changes', [{}, {'draftLineId': None}, {'draftLineId': ''},
                                     {'draftLineId': ' '}, {'source': 'AI_EXTRACTED'}])
def test_extracted_row_cannot_bypass_review_by_omitting_link(client, changes):
    sid = make_submission(payload={'lines': [uncertain_line()]})
    response = client.post('/api/v1/confirm', json=body(sid, **changes))
    assert response.status_code == 422, response.text
    assert counts(sid) == ('NEEDS_VERIFICATION', 0)
    assert client.get('/api/v1/submissions/' + str(sid)).json()['lines'][0]['unitPrice'] == '1.20'


@pytest.mark.parametrize('correction', [{'verifiedFields': ['unitPrice']}, {'unitPrice': '1.25'}])
def test_verified_or_corrected_extracted_rows_keep_provenance(client, correction):
    sid = make_submission(payload={'lines': [uncertain_line()]})
    request = body(sid, draftLineId='a', **correction)
    first = client.post('/api/v1/confirm', json=request)
    assert first.status_code == 201, first.text
    repeat = client.post('/api/v1/confirm', json=request)
    assert repeat.status_code == 200
    assert repeat.json()['recordIds'] == first.json()['recordIds']
    assert client.get('/api/v1/sales').json()['records'][0]['source'] == 'AI_EXTRACTED'


def test_explicit_manual_row_beside_extraction_and_inconsistent_sources(client):
    sid = make_submission(payload={'lines': [uncertain_line()]})
    for overrides in ({'draftLineId': 'a', 'source': 'MANUAL', 'verifiedFields': ['unitPrice']},
                      {'source': 'UNKNOWN'}, {'source': 'AI_EXTRACTED'}):
        response = client.post('/api/v1/confirm', json=body(sid, **overrides))
        assert response.status_code == 422
        assert counts(sid) == ('NEEDS_VERIFICATION', 0)
    request = body(sid, draftLineId='a', verifiedFields=['unitPrice'])
    request['lines'].append(body(sid, itemName='Milk', source='MANUAL')['lines'][0])
    assert client.post('/api/v1/confirm', json=request).status_code == 201
    records = client.get('/api/v1/sales').json()['records']
    assert [(r['itemName'], r['source']) for r in records] == [('Bread', 'AI_EXTRACTED'), ('Milk', 'MANUAL')]


def test_unknown_and_duplicate_confirmation_links_roll_back(client):
    sid = make_submission(payload={'lines': [uncertain_line()]})
    request = body(sid, draftLineId='a', verifiedFields=['unitPrice'])
    for invalid_id in ('missing', 'a'):
        request['lines'] = [request['lines'][0],
                            body(sid, draftLineId=invalid_id, verifiedFields=['unitPrice'])['lines'][0]]
        response = client.post('/api/v1/confirm', json=request)
        assert response.status_code == 422
        assert counts(sid) == ('NEEDS_VERIFICATION', 0)


def test_legacy_draft_without_ids_reopens_and_confirms_without_500(client):
    from psycopg.types.json import Jsonb
    sid = make_submission()
    # Bypass the new write boundary to model a draft persisted by an older caller.
    legacy = uncertain_line()
    legacy.pop('lineId')
    with connect() as conn:
        conn.execute('UPDATE submissions SET draft_payload=%s WHERE id=%s',
                     (Jsonb({'lines': [legacy, uncertain_line(lineId='preserved')]}), sid))
    url = '/api/v1/submissions/' + str(sid)
    first = client.get(url)
    assert first.status_code == 200, first.text
    lines = first.json()['lines']
    assert lines == client.get(url).json()['lines']
    assert lines[0]['lineId'] and lines[0]['lineId'] != 'preserved'
    assert lines[1]['lineId'] == 'preserved'
    assert client.post('/api/v1/confirm', json=body(sid)).status_code == 422
    request = body(sid, draftLineId=lines[0]['lineId'])
    assert client.post('/api/v1/confirm', json=request).status_code == 422
    assert counts(sid) == ('NEEDS_VERIFICATION', 0)
    request['lines'][0]['verifiedFields'] = ['unitPrice']
    assert client.post('/api/v1/confirm', json=request).status_code == 201
    assert client.get('/api/v1/sales').json()['records'][0]['source'] == 'AI_EXTRACTED'


def test_ids_are_persisted_on_create_and_update_and_missing_bands_require_review(client):
    sid = make_submission('UPLOADED', {'lines': [uncertain_line(lineId=None, bands=None)]})
    with connect() as conn:
        payload = conn.execute('SELECT draft_payload FROM submissions WHERE id=%s', (sid,)).fetchone()[0]
    line_id = payload['lines'][0]['lineId']
    assert payload['lines'][0]['bands'] == {}
    repository.update_status(sid, 'CLASSIFYING')
    repository.update_status(sid, 'NEEDS_VERIFICATION', draft_payload={'lines': [uncertain_line(lineId=None, bands=None)]})
    assert client.get('/api/v1/submissions/' + str(sid)).json()['lines'][0]['lineId'] == line_id
    request = body(sid, draftLineId=line_id)
    assert client.post('/api/v1/confirm', json=request).status_code == 422
    request['lines'][0]['verifiedFields'] = ['itemName', 'quantity', 'unitPrice']
    assert client.post('/api/v1/confirm', json=request).status_code == 201


@pytest.mark.parametrize('payload', [
    {'lines': [uncertain_line(), uncertain_line()]},
    {'lines': [uncertain_line(lineId='')]},
    {'lines': [uncertain_line(lineId=123)]},
    {'lines': [None]},
    {'lines': [uncertain_line(bands=[])]},
    {'lines': {}},
])
def test_malformed_draft_is_rejected_without_partial_write_or_unhandled_500(client, payload):
    from fastapi import HTTPException
    from psycopg.types.json import Jsonb
    sid = make_submission('CLASSIFYING')
    with pytest.raises(HTTPException) as exc:
        repository.update_status(sid, 'NEEDS_VERIFICATION', draft_payload=payload)
    assert exc.value.status_code == 422
    assert counts(sid) == ('CLASSIFYING', 0)
    with pytest.raises(HTTPException):
        repository.create_submission(date.today(), draft_payload=payload)
    with connect() as conn:
        assert conn.execute('SELECT count(*) FROM submissions').fetchone()[0] == 1
        conn.execute("UPDATE submissions SET status='NEEDS_VERIFICATION', draft_payload=%s WHERE id=%s", (Jsonb(payload), sid))
    assert client.get('/api/v1/submissions/' + str(sid)).status_code == 422
    assert client.post('/api/v1/confirm', json=body(sid, source='MANUAL')).status_code == 422
    assert counts(sid) == ('NEEDS_VERIFICATION', 0)


@pytest.mark.parametrize('quantity', ['0.0000001', '123456789012345678901.1234567', '1e-400'])
def test_finite_values_and_labels_are_not_restricted_by_unapproved_caps(client, quantity):
    from decimal import Decimal
    sid = make_submission('AI_UNAVAILABLE')
    label = 'Item ' + 'x' * 210
    response = client.post('/api/v1/confirm', json=body(sid, itemName=label, quantity=quantity, unitPrice='0.123456789'))
    assert response.status_code == 201, response.text
    row = client.get('/api/v1/sales').json()['records'][0]
    assert row['itemName'] == label
    assert Decimal(row['quantity']) == Decimal(quantity)
    assert row['unitPrice'] == '0.123456789'
    assert row['source'] == 'MANUAL'


@pytest.mark.parametrize('data,files,field', [
    ({'saleDate': 'not-a-date', 'text': 'Bread 3'}, None, 'saleDate'),
    ({'text': 'Bread 3'}, None, 'saleDate'),
    ({'saleDate': (date.today() + timedelta(days=1)).isoformat(), 'text': 'Bread 3'}, None, 'saleDate'),
    ({'saleDate': date.today().isoformat(), 'text': '   '}, None, 'text'),
    ({'saleDate': date.today().isoformat()}, None, None),
    ({'saleDate': date.today().isoformat()}, {'file': ('data.txt', b'x', 'text/plain')}, 'file'),
    ({'saleDate': date.today().isoformat(), 'text': 'Bread'}, {'file': ('data.png', b'x', 'image/png')}, None),
])
def test_extraction_validation_returns_400_with_field_details_and_no_submission(client, data, files, field):
    response = client.post('/api/v1/extract', data=data, files=files)
    assert response.status_code == 400, response.text
    errors = response.json()['detail']
    assert isinstance(errors, list) and errors
    assert errors[0]['loc'] == (['body', field] if field else ['body'])
    assert errors[0]['msg'] and errors[0]['type']
    assert all(set(error) == {'loc', 'msg', 'type'} for error in errors)
    with connect() as conn:
        assert conn.execute('SELECT count(*) FROM submissions').fetchone()[0] == 0


def test_validation_status_change_is_scoped_to_extraction_and_documented(client):
    assert client.post('/api/v1/confirm', json={'submissionId': 'bad', 'lines': []}).status_code == 422
    assert client.post('/api/v1/guidance', json={'asOf': 'bad'}).status_code == 422
    assert client.get('/api/v1/sales?limit=0').status_code == 422
    responses = client.get('/openapi.json').json()['paths']['/api/v1/extract']['post']['responses']
    assert '400' in responses and '422' not in responses


def test_migration_upgrade_preserves_existing_records_and_nonblank_checks(db, monkeypatch):
    from pathlib import Path
    schema = 'test_upgrade_' + uuid4().hex
    url = os.environ['TEST_DATABASE_URL']
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    monkeypatch.setenv('BODEGA_DB_SCHEMA', schema)
    try:
        with connect() as conn:
            conn.execute('CREATE TABLE schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())')
            for name in ('001_data_state.sql', '002_nonblank_labels.sql'):
                conn.execute((Path(__file__).resolve().parents[1] / 'migrations' / name).read_text())
                conn.execute('INSERT INTO schema_migrations(version) VALUES (%s)', (name,))
        from app.main import ConfirmRequest
        sid = make_submission()
        original_ids, _ = repository.save_sales_records(ConfirmRequest(**body(sid)))
        baseline_id = uuid4()
        with connect() as conn:
            conn.execute("INSERT INTO baseline_estimates VALUES (%s,%s,'Bread',7,7,2.5,'STABLE',18,now())",
                         (baseline_id, repository.owner_id()))
        migrate()
        migrate()
        with connect() as conn:
            assert conn.execute('SELECT id FROM sales_records WHERE submission_id=%s', (sid,)).fetchone()[0] == original_ids[0]
            assert conn.execute('SELECT count(*) FROM schema_migrations').fetchone()[0] == 4
            conn.execute('UPDATE baseline_estimates SET item_name=%s WHERE id=%s', ('x' * 201, baseline_id))
        assert counts(sid) == ('CONFIRMED', 1)
        other = make_submission()
        repository.save_sales_records(ConfirmRequest(**body(other, itemName='x' * 201)))
        for label in ('', ' ', '\t\n'):
            with pytest.raises(psycopg.errors.CheckViolation), connect() as conn:
                conn.execute('UPDATE baseline_estimates SET item_name=%s WHERE id=%s', (label, baseline_id))
        assert counts(other) == ('CONFIRMED', 1)
    finally:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
