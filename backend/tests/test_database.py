"""Integration tests run in an isolated schema on TEST_DATABASE_URL only."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
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
        assert conn.execute('SELECT count(*) FROM schema_migrations').fetchone()[0] == 7
        assert conn.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema=current_schema() AND table_name IN ('owners','submissions','sales_records','baseline_estimates')").fetchone()[0] == 4
    with pytest.raises(psycopg.errors.ForeignKeyViolation), connect() as conn:
        conn.execute('INSERT INTO submissions(id,owner_id,sale_date) VALUES (%s,%s,%s)', (uuid4(), uuid4(), date.today()))


def test_confirm_and_repeat_and_history(client):
    sid = make_submission()
    assert client.get('/api/v1/sales').json() == []
    first = client.post('/api/v1/confirm', json=body(sid))
    assert first.status_code == 201, first.text
    repeated = client.post('/api/v1/confirm', json=body(sid, quantity='999'))
    assert repeated.status_code == 200
    assert not repeated.json()['created']
    assert repeated.json()['recordIds'] == first.json()['recordIds']
    assert counts(sid) == ('CONFIRMED', 1)
    records = client.get('/api/v1/sales').json()
    assert len(records) == 1
    assert records[0]['itemName'] == 'Bread'
    assert records[0]['quantity'] == 2.5
    assert records[0]['saleDate'] == date.today().isoformat()
    assert client.get('/api/v1/submissions/' + str(sid)).json()['lines'] == []


@pytest.mark.parametrize('changes', [{'itemName': '  '}, {'quantity': '0'}, {'quantity': '-1'}, {'quantity': 'NaN'}, {'quantity': 'Infinity'}, {'unitPrice': '-0.01'}, {'unitPrice': 'NaN'}, {'unitPrice': 'Infinity'}])
def test_invalid_values_leave_draft_unchanged(client, changes):
    sid = make_submission()
    assert client.post('/api/v1/confirm', json=body(sid, **changes)).status_code == 400
    assert counts(sid) == ('NEEDS_VERIFICATION', 0)


def test_empty_unknown_and_ineligible(client):
    sid = make_submission('CLASSIFYING')
    assert client.post('/api/v1/confirm', json=body(sid)).status_code == 409
    assert client.post('/api/v1/confirm', json=body(uuid4())).status_code == 404
    assert client.post('/api/v1/confirm', json={'submissionId': str(sid), 'lines': []}).status_code == 400
    assert client.post('/api/v1/confirm', json={'submissionId': 'bad', 'lines': []}).status_code == 400
    assert counts(sid) == ('CLASSIFYING', 0)


def test_database_failure_rolls_back_rows_and_status(client):
    sid = make_submission()
    with connect() as conn:
        conn.execute("CREATE FUNCTION fail_confirm() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.status='CONFIRMED' THEN RAISE EXCEPTION 'injected failure'; END IF; RETURN NEW; END $$")
        conn.execute('CREATE TRIGGER injected_failure BEFORE UPDATE ON submissions FOR EACH ROW EXECUTE FUNCTION fail_confirm()')
    response = client.post('/api/v1/confirm', json=body(sid))
    assert response.status_code == 500
    assert counts(sid) == ('NEEDS_VERIFICATION', 0)
    assert client.get('/api/v1/sales').json() == []
    with connect() as conn:
        conn.execute('DROP TRIGGER injected_failure ON submissions')
    assert client.post('/api/v1/confirm', json=body(sid)).status_code == 201


def test_partial_insert_rolls_back_on_later_verification_failure(client):
    sid = make_submission(payload={'lines': [{'lineId': 1, 'itemName': 'Milk', 'quantity': 2, 'unitPrice': 1, 'bands': {'itemName': 'HIGH', 'quantity': 'HIGH', 'unitPrice': 'MEDIUM'}}]})
    request = body(sid)
    request['lines'].append({'draftLineId': 1, 'itemName': 'Milk', 'quantity': 2, 'unitPrice': 1})
    assert client.post('/api/v1/confirm', json=request).status_code == 400
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
    assert client.post('/api/v1/confirm', json=body(sid)).status_code == 201
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        repository.update_status(sid, 'CLASSIFYING')
    assert exc.value.status_code == 409
    manual = make_submission('AI_UNAVAILABLE')
    assert client.post('/api/v1/confirm', json=body(manual)).status_code == 201
    assert all(r['source'] == 'MANUAL' for r in client.get('/api/v1/sales').json())


@pytest.mark.parametrize('quantity,price', [('0', '1'), ('-1', '1'), ('NaN', '1'), ('Infinity', '1'), ('1', '-1'), ('1', 'NaN')])
def test_sql_constraints(db, quantity, price):
    sid = make_submission()
    with pytest.raises((psycopg.errors.CheckViolation,psycopg.errors.NumericValueOutOfRange)), connect() as conn:
        conn.execute("INSERT INTO sales_records(id,submission_id,line_no,item_name,quantity,unit_price,source) VALUES (%s,%s,1,'Bread',%s,%s,'MANUAL')", (uuid4(), sid, quantity, price))
    assert counts(sid) == ('NEEDS_VERIFICATION', 0)


def test_parent_date_and_confirmation_integrity(db):
    sid = make_submission()
    with pytest.raises(psycopg.errors.ForeignKeyViolation), connect() as conn:
        conn.execute("INSERT INTO sales_records(id,submission_id,line_no,item_name,quantity,unit_price,source) VALUES (%s,%s,1,'Bread',1,1,'MANUAL')", (uuid4(), uuid4()))
    with pytest.raises(psycopg.errors.CheckViolation), connect() as conn:
        conn.execute("UPDATE submissions SET status='CONFIRMED' WHERE id=%s", (sid,))
    with pytest.raises(psycopg.errors.CheckViolation), connect() as conn:
        conn.execute("INSERT INTO sales_records(id,submission_id,line_no,item_name,quantity,unit_price,source) VALUES (%s,%s,1,'Bread',1,1,'MANUAL')", (uuid4(), sid))


def test_auth_and_owner_scope(client, monkeypatch):
    sid = make_submission()
    assert client.post('/api/v1/confirm', json=body(sid), headers={'Authorization': 'Bearer wrong'}).status_code == 401
    monkeypatch.setenv('OWNER_ID', str(uuid4()))
    assert client.post('/api/v1/confirm', json=body(sid)).status_code == 404
    assert client.get('/api/v1/submissions/' + str(sid)).status_code == 404
    assert client.get('/api/v1/sales').json() == []


def test_owner_cascade(client):
    sid = make_submission()
    assert client.post('/api/v1/confirm', json=body(sid)).status_code == 201
    with connect() as conn:
        conn.execute('DELETE FROM owners WHERE id=%s', (repository.owner_id(),))
    with connect() as conn:
        assert conn.execute('SELECT count(*) FROM submissions').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM sales_records').fetchone()[0] == 0


def test_manual_fallback_and_rejected_confirmation(client):
    sid=make_submission('REJECTED')
    assert client.post('/api/v1/confirm',json=body(sid)).status_code==201
    result=client.post('/api/v1/extract',data={'saleDate':date.today().isoformat(),'text':'Bread 3'})
    assert result.status_code==200 and result.json()['status']=='AI_UNAVAILABLE'
    assert client.post('/api/v1/confirm',json=body(result.json()['submissionId'])).status_code==201


def test_exact_decimal_history_and_immutable_records(client):
    sid = make_submission()
    response = client.post('/api/v1/confirm', json=body(sid, quantity='12345.12', unitPrice='0.01'))
    assert response.status_code == 201
    record = client.get('/api/v1/sales').json()[0]
    assert record['quantity'] == 12345.12
    assert record['unitPrice'] == 0.01
    with pytest.raises(psycopg.errors.CheckViolation), connect() as conn:
        conn.execute('UPDATE sales_records SET quantity=1 WHERE submission_id=%s', (sid,))


def test_baseline_constraints_and_cascade(db):
    sid = make_submission()
    values = (uuid4(), repository.owner_id(), 'Bread', 7, 7, '2.5', 'UP', 18)
    with connect() as conn:
        conn.execute('INSERT INTO baseline_estimates(id,owner_id,item_name,window_days,logged_days,average_daily_quantity,trend,suggested_restock_qty,as_of_date) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_DATE)', values)
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
    assert len(client.get('/api/v1/sales').json()) == 4
    draft = client.get('/api/v1/submissions/10000000-0000-4000-8000-000000000003')
    assert draft.json()['status'] == 'NEEDS_VERIFICATION'
    assert len(draft.json()['lines']) == 1


def test_database_rejects_whitespace_only_labels(client):
    sid = make_submission()
    assert client.post('/api/v1/confirm', json=body(sid)).status_code == 201
    for item in ('\t', '\n', ' \r\n '):
        with pytest.raises(psycopg.errors.CheckViolation), connect() as conn:
            conn.execute("INSERT INTO sales_records(id,submission_id,line_no,item_name,quantity,unit_price,source) VALUES (%s,%s,1,%s,1,1,'MANUAL')", (uuid4(), sid, item))
        with pytest.raises(psycopg.errors.CheckViolation), connect() as conn:
            conn.execute("INSERT INTO baseline_estimates(id,owner_id,item_name,window_days,logged_days,average_daily_quantity,trend,suggested_restock_qty,as_of_date) VALUES (%s,%s,%s,7,7,1,'STABLE',7,CURRENT_DATE)", (uuid4(), repository.owner_id(), item))
    assert counts(sid) == ('CONFIRMED', 1)


def uncertain_line(**overrides):
    line = {'lineId': 1, 'itemName': 'Bread', 'quantity': '2.5', 'unitPrice': '1.20',
            'bands': {'itemName': 'HIGH', 'quantity': 'HIGH', 'unitPrice': 'MEDIUM'}}
    line.update(overrides)
    return line


@pytest.mark.parametrize('changes', [{'draftLineId':'a'}, {'draftLineId': 'missing'}, {'draftLineId': ''},
                                     {'draftLineId': ' '}, {'source': 'AI_EXTRACTED'}])
def test_extracted_row_cannot_bypass_review_by_omitting_link(client, changes):
    sid = make_submission(payload={'lines': [uncertain_line()]})
    response = client.post('/api/v1/confirm', json=body(sid, **changes))
    assert response.status_code == 400, response.text
    assert counts(sid) == ('NEEDS_VERIFICATION', 0)
    assert client.get('/api/v1/submissions/' + str(sid)).json()['lines'][0]['unitPrice'] == 1.2


@pytest.mark.parametrize('correction', [{'verifiedFields': ['unitPrice']}, {'unitPrice': '1.25'}])
def test_verified_or_corrected_extracted_rows_keep_provenance(client, correction):
    sid = make_submission(payload={'lines': [uncertain_line()]})
    request = body(sid, draftLineId=1, **correction)
    first = client.post('/api/v1/confirm', json=request)
    assert first.status_code == 201, first.text
    repeat = client.post('/api/v1/confirm', json=request)
    assert repeat.status_code == 200
    assert repeat.json()['recordIds'] == first.json()['recordIds']
    assert client.get('/api/v1/sales').json()[0]['source'] == 'AI_EXTRACTED'


def test_explicit_manual_row_beside_extraction_and_inconsistent_sources(client):
    sid = make_submission(payload={'lines': [uncertain_line()]})
    for overrides in ({'draftLineId': 1, 'source': 'MANUAL', 'verifiedFields': ['unitPrice']},
                      {'source': 'UNKNOWN'}, {'source': 'AI_EXTRACTED'}):
        response = client.post('/api/v1/confirm', json=body(sid, **overrides))
        assert response.status_code == 400
        assert counts(sid) == ('NEEDS_VERIFICATION', 0)
    request = body(sid, draftLineId=1, verifiedFields=['unitPrice'])
    request['lines'].append(body(sid, itemName='Milk')['lines'][0])
    assert client.post('/api/v1/confirm', json=request).status_code == 201
    records = client.get('/api/v1/sales').json()
    assert [(r['itemName'], r['source']) for r in records] == [('Bread', 'AI_EXTRACTED'), ('Milk', 'MANUAL')]


def test_unknown_and_duplicate_confirmation_links_roll_back(client):
    sid = make_submission(payload={'lines': [uncertain_line()]})
    request = body(sid, draftLineId=1, verifiedFields=['unitPrice'])
    for invalid_id in ('missing', 1):
        request['lines'] = [request['lines'][0],
                            body(sid, draftLineId=invalid_id, verifiedFields=['unitPrice'])['lines'][0]]
        response = client.post('/api/v1/confirm', json=request)
        assert response.status_code == 400
        assert counts(sid) == ('NEEDS_VERIFICATION', 0)


def test_legacy_draft_without_ids_reopens_and_confirms_without_500(client):
    from psycopg.types.json import Jsonb
    sid = make_submission()
    # Bypass the new write boundary to model a draft persisted by an older caller.
    legacy = uncertain_line()
    legacy.pop('lineId')
    with connect() as conn:
        conn.execute('UPDATE submissions SET draft_payload=%s WHERE id=%s',
                     (Jsonb({'lines': [legacy, uncertain_line(lineId=2)]}), sid))
    url = '/api/v1/submissions/' + str(sid)
    first = client.get(url)
    assert first.status_code == 200, first.text
    lines = first.json()['lines']
    assert lines == client.get(url).json()['lines']
    assert lines[0]['lineId'] and lines[0]['lineId'] != 2
    assert lines[1]['lineId'] == 2
    request = body(sid, draftLineId=lines[0]['lineId'])
    assert client.post('/api/v1/confirm', json=request).status_code == 400
    assert counts(sid) == ('NEEDS_VERIFICATION', 0)
    request['lines'][0]['verifiedFields'] = ['unitPrice']
    assert client.post('/api/v1/confirm', json=request).status_code == 201
    assert client.get('/api/v1/sales').json()[0]['source'] == 'AI_EXTRACTED'


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
    assert client.post('/api/v1/confirm', json=request).status_code == 400
    request['lines'][0]['verifiedFields'] = ['itemName', 'quantity', 'unitPrice']
    assert client.post('/api/v1/confirm', json=request).status_code == 201


@pytest.mark.parametrize('payload', [
    {'lines': [uncertain_line(), uncertain_line()]},
    {'lines': [uncertain_line(lineId='')]},
    {'lines': [uncertain_line(lineId=[])]},
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


@pytest.mark.parametrize('quantity', ['0.0000001', '123456789012345678901.1234567', '1e-400'])
def test_finite_values_and_labels_are_not_restricted_by_unapproved_caps(client, quantity):
    from decimal import Decimal
    sid = make_submission('AI_UNAVAILABLE')
    label = 'Item ' + 'x' * 210
    response = client.post('/api/v1/confirm', json=body(sid, itemName=label, quantity=quantity, unitPrice='0.123456789'))
    assert response.status_code == 400, response.text
    assert counts(sid) == ('AI_UNAVAILABLE', 0)


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
    error = response.json()['error']
    assert error['code'] == 'VALIDATION_ERROR'
    assert error['fields']
    assert (field or '') in error['fields']
    with connect() as conn:
        assert conn.execute('SELECT count(*) FROM submissions').fetchone()[0] == 0


def test_validation_status_change_is_scoped_to_extraction_and_documented(client):
    assert client.post('/api/v1/confirm', json={'submissionId': 'bad', 'lines': []}).status_code == 400
    assert client.post('/api/v1/guidance', json={'asOf': 'bad'}).status_code == 400
    assert client.get('/api/v1/sales?start=invalid').status_code == 400
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
        sid = uuid4(); original_ids=[uuid4()]
        with connect() as conn:
            conn.execute('INSERT INTO owners(id) VALUES (%s)', (repository.owner_id(),))
            conn.execute('INSERT INTO submissions(id,owner_id,sale_date) VALUES (%s,%s,%s)', (sid,repository.owner_id(),date.today()))
            conn.execute("UPDATE submissions SET status='CLASSIFYING' WHERE id=%s",(sid,))
            conn.execute("UPDATE submissions SET status='NEEDS_VERIFICATION' WHERE id=%s",(sid,))
            conn.execute("INSERT INTO sales_records(id,submission_id,line_number,item_name,quantity,unit_price,sale_date,source) VALUES (%s,%s,0,'Bread',2.5,1.2,%s,'MANUAL')",(original_ids[0],sid,date.today()))
            conn.execute("UPDATE submissions SET status='CONFIRMED' WHERE id=%s",(sid,))
        migrate()
        migrate()
        with connect() as conn:
            assert conn.execute('SELECT id FROM sales_records WHERE submission_id=%s', (sid,)).fetchone()[0] == original_ids[0]
            assert conn.execute('SELECT count(*) FROM schema_migrations').fetchone()[0] == 7
        assert counts(sid) == ('CONFIRMED', 1)
        other = make_submission()
        repository.save_sales_records(ConfirmRequest(**body(other, itemName='x' * 100)))
        assert counts(other) == ('CONFIRMED', 1)
    finally:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


# Completion audit regressions: real PostgreSQL remains the persistence boundary.
@pytest.mark.parametrize('before', ['UPLOADED','CLASSIFYING','NEEDS_VERIFICATION','AI_UNAVAILABLE','REJECTED','CONFIRMED'])
@pytest.mark.parametrize('after', ['UPLOADED','CLASSIFYING','NEEDS_VERIFICATION','AI_UNAVAILABLE','REJECTED','CONFIRMED'])
def test_all_database_state_pairs(client, before, after):
    from app.lifecycle import TRANSITIONS
    sid = make_submission('NEEDS_VERIFICATION' if before == 'CONFIRMED' else before)
    if before == 'CONFIRMED':
        assert client.post('/api/v1/confirm', json=body(sid)).status_code == 201
    allowed = before == after or after in TRANSITIONS[before]
    def change():
        with connect() as conn:
            if after == 'CONFIRMED' and before != after and allowed:
                conn.execute("INSERT INTO sales_records(id,submission_id,line_no,item_name,quantity,unit_price,source) VALUES (%s,%s,1,'Bread',1,1,'MANUAL')", (uuid4(), sid))
            conn.execute('UPDATE submissions SET status=%s WHERE id=%s', (after, sid))
    if allowed:
        change()
        assert counts(sid)[0] == after
    else:
        with pytest.raises(psycopg.errors.CheckViolation):
            change()
        assert counts(sid)[0] == before


def test_concurrent_extraction_outcomes_cannot_overwrite_each_other(db):
    from fastapi import HTTPException
    sid = make_submission('CLASSIFYING')
    def change(state):
        try:
            repository.update_status(sid, state, expected_status='CLASSIFYING')
            return 'saved'
        except HTTPException as exc:
            return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(change, ['NEEDS_VERIFICATION', 'AI_UNAVAILABLE']))
    assert sorted(results, key=str) == [409, 'saved']
    assert counts(sid)[1] == 0


def test_confirmed_draft_cannot_be_replaced(client):
    from fastapi import HTTPException
    sid = make_submission()
    client.post('/api/v1/confirm', json=body(sid))
    with pytest.raises(HTTPException) as exc:
        repository.update_status(sid, 'CONFIRMED', draft_payload={'lines': []})
    assert exc.value.status_code == 409
    with connect() as conn:
        assert conn.execute('SELECT draft_payload FROM submissions WHERE id=%s', (sid,)).fetchone() == (None,)


@pytest.mark.parametrize('score,band', [(None,'UNSCORED'),('0','LOW'),('0.3999','LOW'),('0.40','LOW'),('0.5999','LOW'),('0.60','MEDIUM'),('0.84','MEDIUM'),('0.845','MEDIUM'),('0.85','HIGH'),('1','HIGH')])
def test_confidence_boundaries_persist_and_enforce_review(client, score, band):
    sid = make_submission('CLASSIFYING')
    repository.save_extraction_result(sid, '0.40', [{'itemName':'Bread','quantity':2,'unitPrice':1,
        'itemNameConfidence':score,'quantityConfidence':1,'unitPriceConfidence':1}])
    draft = repository.get_submission(sid)['lines'][0]
    assert draft['bands']['itemName'] == band
    assert draft['itemName'] == (None if band == 'LOW' else 'Bread')
    response = client.post('/api/v1/confirm', json=body(sid, draftLineId=draft['lineId'], quantity=2, unitPrice=1))
    assert response.status_code == (400 if band in ('MEDIUM','UNSCORED') else 201)


@pytest.mark.parametrize('overall,lines', [('0.3999',[{'itemName':'Bread'}]),('0.40',[])])
def test_unreadable_or_empty_result_rejected_without_rows(db, overall, lines):
    sid = make_submission('CLASSIFYING')
    repository.save_extraction_result(sid, overall, lines)
    assert counts(sid) == ('REJECTED',0)
    assert repository.get_submission(sid)['lines'] == []


@pytest.mark.parametrize('score', ['NaN','Infinity', '-0.1', '1.1', 'bad', True])
def test_invalid_confidence_does_not_change_submission(db, score):
    from fastapi import HTTPException
    sid = make_submission('CLASSIFYING')
    with pytest.raises(HTTPException):
        repository.save_extraction_result(sid, score, [{'itemName':'Bread'}])
    assert counts(sid) == ('CLASSIFYING',0)




def test_validation_errors_do_not_echo_private_values(client):
    private = 'private-contact@example.test'
    response = client.post('/api/v1/confirm', json=body(make_submission(), quantity=private))
    assert response.status_code == 400
    assert private not in response.text
    assert set(response.json()) == {'error'}
    assert set(response.json()['error']) == {'code','message','fields'}


def test_health_checks_database_and_hides_connection_details(client, monkeypatch):
    assert client.get('/health').status_code == 200
    monkeypatch.setenv('DATABASE_URL', 'postgresql://private:secret@127.0.0.1:1/missing?connect_timeout=1')
    result = client.get('/health')
    assert result.status_code == 503
    assert result.json() == {'status':'unavailable'}


def test_full_date_range_history_excludes_drafts_and_other_owners(client, monkeypatch):
    for _ in range(2):
        sid = make_submission()
        payload = body(sid)
        payload['lines'] *= 60
        assert client.post('/api/v1/confirm', json=payload).status_code == 201
    make_submission()
    assert len(repository.get_confirmed_records()) == 100
    assert len(repository.get_confirmed_records_between(date.today(),date.today())) == 120
    assert repository.get_confirmed_records_between(date.today()-timedelta(days=2),date.today()-timedelta(days=1)) == []
    monkeypatch.setenv('OWNER_ID', str(uuid4()))
    assert repository.get_confirmed_records_between(date.today(),date.today()) == []


def test_baseline_adapters_latest_owner_isolation_and_atomic_rollback(db, monkeypatch):
    from datetime import datetime, timezone
    from pydantic import ValidationError
    base = dict(itemName='Bread',windowDays=7,loggedDays=7,averageDailyQuantity='2.50',trend='STABLE',suggestedRestockQty=18)
    repository.save_baselines([{**base,'computedAt':datetime(2020,1,1,tzinfo=timezone.utc)}], as_of_date=date(2020,1,1))
    latest_id = uuid4()
    repository.save_baselines([{**base,'id':latest_id,'itemName':' bread ','averageDailyQuantity':'3.25'}], as_of_date=date.today())
    rows = repository.get_latest_baselines()
    assert len(rows) == 1 and rows[0]['id'] == latest_id
    assert rows[0]['averageDailyQuantity'] == Decimal('3.25')
    with pytest.raises(psycopg.errors.UniqueViolation):
        repository.save_baselines([{**base,'itemName':'Milk','id':latest_id},{**base,'id':latest_id}], as_of_date=date.today())
    assert len(repository.get_latest_baselines()) == 1
    with pytest.raises(ValidationError):
        repository.save_baselines([base,{**base,'averageDailyQuantity':'NaN'}], as_of_date=date.today())
    with connect() as conn:
        assert conn.execute('SELECT count(*) FROM baseline_estimates').fetchone()[0] == 2
    monkeypatch.setenv('OWNER_ID',str(uuid4()))
    assert repository.get_latest_baselines() == []
