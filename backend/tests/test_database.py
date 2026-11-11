"""Integration tests run in an isolated schema on TEST_DATABASE_URL only."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from uuid import uuid4

import psycopg
import pytest
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
    from conftest import ExactClient
    with ExactClient(app, raise_server_exceptions=False) as client:
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
        assert conn.execute('SELECT version_num FROM alembic_version').fetchone()[0] == '008'  # Alembic head
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
    # Diagram 4: REJECTED is an open state and accepts the same confirm as the others.
    sid = make_submission('REJECTED')
    with pytest.raises(psycopg.errors.CheckViolation):
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
        conn.execute("INSERT INTO sales_records(id,submission_id,line_no,item_name,quantity,unit_price,source) VALUES (%s,%s,1,'Bread',%s,%s,'MANUAL')", (uuid4(), sid, quantity, price))
    assert counts(sid) == ('NEEDS_VERIFICATION', 0)


def test_parent_date_and_confirmation_integrity(db):
    sid = make_submission()
    # M3 7.1: the record has no date column of its own, so it can never disagree with its parent.
    with connect() as conn:
        columns = {row[0] for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='sales_records'")}
    assert 'sale_date' not in columns and {'line_no', 'item_key'} <= columns
    with pytest.raises(psycopg.errors.ForeignKeyViolation), connect() as conn:
        conn.execute("INSERT INTO sales_records(id,submission_id,line_no,item_name,quantity,unit_price,source) VALUES (%s,%s,1,'Bread',1,1,'MANUAL')", (uuid4(), uuid4()))
    with pytest.raises(psycopg.errors.CheckViolation):
        repository.update_status(sid, 'CONFIRMED')
    with pytest.raises(psycopg.errors.CheckViolation), connect() as conn:
        conn.execute("INSERT INTO sales_records(id,submission_id,line_no,item_name,quantity,unit_price,source) VALUES (%s,%s,1,'Bread',1,1,'MANUAL')", (uuid4(), sid))
    for line_no in (0, 101):
        with pytest.raises(psycopg.errors.CheckViolation), connect() as conn:
            conn.execute("INSERT INTO sales_records(id,submission_id,line_no,item_name,quantity,unit_price,source) VALUES (%s,%s,%s,'Bread',1,1,'MANUAL')", (uuid4(), sid, line_no))


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


def test_rejected_input_is_typed_and_confirmed_without_a_review_transition(client):
    sid = make_submission('REJECTED')
    # The pre-final "manual-review" endpoint is gone: REJECTED confirms directly (Diagram 4).
    assert client.post('/api/v1/submissions/' + str(sid) + '/manual-review').status_code in (404, 405)
    assert client.post('/api/v1/confirm', json=body(sid)).status_code == 201
    assert counts(sid) == ('CONFIRMED', 1)
    result = client.post('/api/v1/extract', data={'saleDate': date.today().isoformat(), 'text': 'Bread 3'})
    assert result.status_code == 200
    assert result.json()['status'] == 'AI_UNAVAILABLE'
    assert result.json()['lines'] == []
    assert client.post('/api/v1/confirm', json=body(result.json()['submissionId'])).status_code == 201


def test_exact_decimal_history_and_immutable_records(client):
    sid = make_submission()
    response = client.post('/api/v1/confirm', json=body(sid, quantity='99999.99', unitPrice='9999999.99'))
    assert response.status_code == 201
    record = client.get('/api/v1/sales').json()['records'][0]
    assert record['quantity'] == '99999.99'
    assert record['unitPrice'] == '9999999.99'
    assert record['lineTotal'] == '999999899000.0001'  # Exact NUMERIC product, never a float.
    assert record['recordId'] == response.json()['recordIds'][0] and record['lineNo'] == 1
    with pytest.raises(psycopg.errors.CheckViolation), connect() as conn:
        conn.execute('UPDATE sales_records SET quantity=1 WHERE submission_id=%s', (sid,))


def test_baseline_constraints_and_cascade(db):
    make_submission()  # Creates the owner the snapshot belongs to.
    values = (uuid4(), repository.owner_id(), 'Bread', 'bread', date.today(), 7, 7, '2.5', 'UP', 18)
    with connect() as conn:
        conn.execute('INSERT INTO baseline_estimates(id,owner_id,item_name,item_key,as_of_date,window_days,logged_days,average_daily_quantity,trend,suggested_restock_qty) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)', values)
    for column, value in [('item_name', ' '), ('window_days', 0), ('logged_days', -1), ('average_daily_quantity', 'NaN'),
                          ('trend', 'UNKNOWN'), ('suggested_restock_qty', -1), ('as_of_date', None), ('cover_days', 8),
                          ('item_name', 'x' * 101)]:
        with pytest.raises(psycopg.errors.CheckViolation), connect() as conn:
            conn.execute(sql.SQL('UPDATE baseline_estimates SET {}=%s').format(sql.Identifier(column)), (value,))
    with pytest.raises(psycopg.errors.UniqueViolation), connect() as conn:
        conn.execute('INSERT INTO baseline_estimates(id,owner_id,item_name,item_key,as_of_date,window_days,logged_days,average_daily_quantity,trend,suggested_restock_qty) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                     (uuid4(), *values[1:]))
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
            conn.execute("INSERT INTO sales_records(id,submission_id,line_no,item_name,quantity,unit_price,source) VALUES (%s,%s,2,%s,1,1,'MANUAL')", (uuid4(), sid, item))
        with pytest.raises(psycopg.errors.CheckViolation), connect() as conn:
            conn.execute("INSERT INTO baseline_estimates(id,owner_id,item_name,item_key,as_of_date,window_days,logged_days,average_daily_quantity,trend,suggested_restock_qty) VALUES (%s,%s,%s,'k',%s,7,7,1,'STABLE',7)", (uuid4(), repository.owner_id(), item, date.today()))
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
    {'lines': [uncertain_line(lineId=0)]},
    {'lines': [uncertain_line(lineId=True)]},
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
    assert exc.value.status_code == 400
    assert counts(sid) == ('CLASSIFYING', 0)
    with pytest.raises(HTTPException):
        repository.create_submission(date.today(), draft_payload=payload)
    with connect() as conn:
        assert conn.execute('SELECT count(*) FROM submissions').fetchone()[0] == 1
        conn.execute("UPDATE submissions SET status='NEEDS_VERIFICATION', draft_payload=%s WHERE id=%s", (Jsonb(payload), sid))
    assert client.get('/api/v1/submissions/' + str(sid)).status_code == 400
    assert client.post('/api/v1/confirm', json=body(sid, source='MANUAL')).status_code == 400
    assert counts(sid) == ('NEEDS_VERIFICATION', 0)


@pytest.mark.parametrize('quantity,price,label', [
    ('0.01', '0', 'x'), ('99999.99', '9999999.99', 'x' * 100), ('2.50', '1.20', 'Rice (lb)'), ('1E+2', '1.5', 'Eggs')])
def test_final_limits_accept_boundaries(client, quantity, price, label):
    """FR-202 as clarified in M3 section 3 and the 7.2 limits: 2 decimals, 99,999.99, 1-100 chars."""
    from decimal import Decimal
    sid = make_submission('AI_UNAVAILABLE')
    response = client.post('/api/v1/confirm', json=body(sid, itemName=label, quantity=quantity, unitPrice=price))
    assert response.status_code == 201, response.text
    row = client.get('/api/v1/sales').json()['records'][0]
    assert row['itemName'] == label and Decimal(row['quantity']) == Decimal(quantity)
    assert row['source'] == 'MANUAL'


@pytest.mark.parametrize('changes', [
    {'quantity': '0.001'}, {'quantity': '100000'}, {'quantity': '1e-400'}, {'quantity': '123456789012345678901.12'},
    {'unitPrice': '1.005'}, {'unitPrice': '10000000'}, {'itemName': 'x' * 101}])
def test_final_limits_reject_values_outside_the_contract(client, changes):
    sid = make_submission('AI_UNAVAILABLE')
    response = client.post('/api/v1/confirm', json=body(sid, **changes))
    assert response.status_code == 422
    error = response.json()['error']
    assert error['code'] == 'VALIDATION_ERROR' and error['fields']
    assert counts(sid) == ('AI_UNAVAILABLE', 0)


def test_database_enforces_final_limits_for_direct_writes(db):
    sid = make_submission()
    for quantity, label in (('0.001', 'Bread'), ('100000', 'Bread'), ('1', 'x' * 101)):
        with pytest.raises(psycopg.errors.CheckViolation), connect() as conn:
            conn.execute("INSERT INTO sales_records(id,submission_id,line_no,item_name,quantity,unit_price,source) VALUES (%s,%s,1,%s,%s,1,'MANUAL')", (uuid4(), sid, label, quantity))


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


def test_validation_errors_use_the_envelope_400_except_confirmation_422(client):
    """D-30: extraction and every other request answer 400; confirming sales rows answers 422.
    Both use the M3 4.2 envelope {error: {code, message, fields}}."""
    expected = [
        (client.post('/api/v1/confirm', json={'submissionId': 'bad', 'lines': []}), 422),
        (client.post('/api/v1/manual-entries', json={'submissionId': 'bad', 'lines': []}), 422),
        (client.post('/api/v1/extract', data={'saleDate': 'bad', 'text': 'Bread 3'}), 400),
        (client.post('/api/v1/guidance', json={'asOf': 'bad'}), 400),
        (client.get('/api/v1/analysis?asOf=2999-01-01'), 400),
        (client.get('/api/v1/sales?start=bad'), 400),
        (client.get('/api/v1/sales?end=2026-02-30'), 400),
        (client.get('/api/v1/sales?start=2026-05-02&end=2026-05-01'), 400),
        (client.get('/api/v1/sales?start=2025-01-01&end=2026-05-01'), 400),
        (client.get('/api/v1/sales?limit=0'), 400),
        (client.get('/api/v1/sales?limit=501'), 400),
        (client.get('/api/v1/sales?offset=-1'), 400),
    ]
    for response, status in expected:
        assert response.status_code == status, response.text
        error = response.json()['error']
        assert error['code'] == 'VALIDATION_ERROR' and error['message'] and isinstance(error['fields'], dict)
        assert error['fields'], response.text
    paths = client.get('/openapi.json').json()['paths']
    assert '422' in paths['/api/v1/confirm']['post']['responses']
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
        # Legacy data written in the 001 shape (sale_date on each row, line numbers from 0), the
        # way the code before 005/006 wrote it, including a 3-row submission and a baseline.
        owner, sid, day = repository.owner_id(), uuid4(), date.today() - timedelta(days=2)
        original_ids = [uuid4(), uuid4(), uuid4()]
        baseline_id = uuid4()
        with connect() as conn:
            conn.execute('INSERT INTO owners(id) VALUES (%s)', (owner,))
            conn.execute('INSERT INTO submissions(id, owner_id, sale_date) VALUES (%s,%s,%s)', (sid, owner, day))
            conn.execute("UPDATE submissions SET status='CLASSIFYING' WHERE id=%s", (sid,))
            conn.execute("UPDATE submissions SET status='NEEDS_VERIFICATION' WHERE id=%s", (sid,))
            for line, (record_id, item, quantity) in enumerate(zip(original_ids, ('Bread', 'Milk', 'Rice (lb)'), ('3', '2', '2.25'), strict=True)):
                conn.execute("INSERT INTO sales_records VALUES (%s,%s,%s,%s,%s,1.50,%s,'MANUAL')", (record_id, sid, line, item, quantity, day))
            conn.execute("UPDATE submissions SET status='CONFIRMED' WHERE id=%s", (sid,))
            conn.execute("INSERT INTO baseline_estimates VALUES (%s,%s,'Bread',7,7,2.5,'STABLE',18,now())", (baseline_id, owner))
        migrate()
        migrate()
        with connect() as conn:
            # The pre-Alembic ledger is kept as it was; Alembic now records the head revision.
            assert conn.execute('SELECT count(*) FROM schema_migrations').fetchone()[0] == 2
            assert conn.execute('SELECT version_num FROM alembic_version').fetchone()[0] == '008'
            # 005 backfills inputType and lifecycle timestamps without touching the sales rows.
            assert conn.execute('SELECT input_type, confirmed_at IS NOT NULL FROM submissions WHERE id=%s',
                                (sid,)).fetchone() == ('TEXT', True)
            # 006: same records and values, line numbers 1..n in the original order, item keys filled.
            rows = conn.execute('SELECT id, line_no, item_name, quantity, item_key FROM sales_records '
                                'WHERE submission_id=%s ORDER BY line_no', (sid,)).fetchall()
            assert [(r[0], r[1], r[2], str(r[3])) for r in rows] == [
                (original_ids[0], 1, 'Bread', '3'), (original_ids[1], 2, 'Milk', '2'), (original_ids[2], 3, 'Rice (lb)', '2.25')]
            assert [r[4] for r in rows] == ['bread', 'milk', 'rice (lb)']
            # The legacy snapshot is kept; it has no analysis date, so it is never "latest".
            assert conn.execute('SELECT as_of_date FROM baseline_estimates WHERE id=%s', (baseline_id,)).fetchone()[0] is None
        assert repository.get_latest_baselines() == []
        # The dropped per-row date is recovered from the parent in every read.
        assert {(r['recordId'], r['saleDate'], r['lineNo']) for r in repository.get_confirmed_records(day, day)[0]} == {
            (original_ids[0], day, 1), (original_ids[1], day, 2), (original_ids[2], day, 3)}
        assert counts(sid) == ('CONFIRMED', 3)
        from app.main import ConfirmRequest
        other = make_submission()
        repository.save_sales_records(ConfirmRequest(**body(other, itemName='x' * 100)))
        assert counts(other) == ('CONFIRMED', 1)
        for label in ('x' * 101, ' ', '\t\n'):
            with pytest.raises(psycopg.errors.CheckViolation), connect() as conn:
                conn.execute("INSERT INTO sales_records(id,submission_id,line_no,item_name,quantity,unit_price,source) "
                             "VALUES (%s,%s,2,%s,1,1,'MANUAL')", (uuid4(), other, label))
    finally:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
