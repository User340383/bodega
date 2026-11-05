"""Gabriel's review regressions; all writes use disposable PostgreSQL schemas."""
from decimal import Decimal
from datetime import date, datetime, timedelta, timezone
from copy import deepcopy
from uuid import uuid4

import pytest

from test_database import db, client, make_submission, body, uncertain_line, counts
from app import repository
from app.database import connect, migrate


def test_newer_database_is_refused_without_mutating_history(client):
    sid = make_submission()
    saved = client.post('/api/v1/confirm', json=body(sid)).json()['recordIds']
    repository.save_baselines([dict(itemName='Bread', windowDays=7, loggedDays=7,
        averageDailyQuantity='2.50', trend='STABLE', suggestedRestockQty=18)],as_of_date=date.today())
    with connect() as conn:
        # Simulate a newer database without inventing that migration's DDL.
        conn.execute("INSERT INTO schema_migrations(version) VALUES ('999_future.sql')")
        before = conn.execute('SELECT version,applied_at FROM schema_migrations ORDER BY version').fetchall()
    with pytest.raises(RuntimeError, match='unavailable in this checkout'):
        migrate()
    with connect() as conn:
        assert conn.execute('SELECT version,applied_at FROM schema_migrations ORDER BY version').fetchall() == before
        assert [str(row[0]) for row in conn.execute('SELECT id FROM sales_records WHERE submission_id=%s', (sid,))] == saved
        assert conn.execute('SELECT average_daily_quantity FROM baseline_estimates').fetchone()[0] == Decimal('2.50')
    assert counts(sid) == ('CONFIRMED', 1)


@pytest.mark.parametrize('band', ['MEDIUM', 'UNSCORED', 'UNKNOWN', None])
def test_equivalent_decimal_not_a_verification_bypass(client, band):
    sid = make_submission(payload={'lines': [uncertain_line(bands={
        'itemName': 'HIGH', 'quantity': 'HIGH', 'unitPrice': band})]})
    request = body(sid, draftLineId=1, unitPrice='1.2000')
    assert client.post('/api/v1/confirm', json=request).status_code == 400
    assert counts(sid) == ('NEEDS_VERIFICATION', 0)
    request['lines'][0]['verifiedFields'] = ['unitPrice']
    assert client.post('/api/v1/confirm', json=request).status_code == 201


@pytest.mark.parametrize('supplied_ids', [
    [2, None, None],
    [None, 1, None],
    [None, None, 1, 2],
])
@pytest.mark.parametrize('legacy', [False, True], ids=['write-boundary', 'stored-draft'])
def test_mixed_draft_ids_stay_unique_stable_and_linked(client, supplied_ids, legacy):
    from psycopg.types.json import Jsonb

    payload = {'lines': [uncertain_line(
        lineId=line_id, itemName=f'Item {index}', quantity=str(index + 1),
        unitPrice=f'{index + 1}.20') for index, line_id in enumerate(supplied_ids)]}
    original = deepcopy(payload)
    if legacy:
        sid = make_submission()
        # Model an existing draft whose missing IDs were never persisted.
        with connect() as conn:
            conn.execute('UPDATE submissions SET draft_payload=%s WHERE id=%s',
                         (Jsonb(payload), sid))
    else:
        sid = make_submission(payload=payload)

    url = '/api/v1/submissions/' + str(sid)
    response = client.get(url)
    assert response.status_code == 200, response.text
    lines = response.json()['lines']
    ids = [line['lineId'] for line in lines]
    assert len(set(ids)) == len(ids)
    assert all(type(line_id) is int and line_id > 0 for line_id in ids)
    for index, supplied in enumerate(supplied_ids):
        if supplied is not None:
            assert ids[index] == supplied
        assert lines[index]['itemName'] == payload['lines'][index]['itemName']
    assert client.get(url).json()['lines'] == lines
    assert [line['lineId'] for line in repository._normalize_draft(sid, payload)['lines']] == ids
    assert payload == original

    repository.update_status(sid, 'NEEDS_VERIFICATION', draft_payload={'lines': lines})
    with connect() as conn:
        assert conn.execute('SELECT draft_payload FROM submissions WHERE id=%s',
                            (sid,)).fetchone()[0]['lines'] == lines
    assert client.get(url).json()['lines'] == lines

    # Reverse the request order to verify IDs remain attached to their own values.
    request = {'submissionId': str(sid), 'lines': [
        {field: line[field] for field in ('itemName', 'quantity', 'unitPrice')}
        | {'draftLineId': line['lineId']} for line in reversed(lines)]}
    assert client.post('/api/v1/confirm', json=request).status_code == 400
    assert counts(sid) == ('NEEDS_VERIFICATION', 0)
    for line in request['lines']:
        line['verifiedFields'] = ['unitPrice']
    confirmed = client.post('/api/v1/confirm', json=request)
    assert confirmed.status_code == 201, confirmed.text
    with connect() as conn:
        saved = conn.execute('''SELECT item_name,quantity,unit_price,source
            FROM sales_records WHERE submission_id=%s ORDER BY line_no''', (sid,)).fetchall()
    assert saved == [(line['itemName'], Decimal(str(line['quantity'])),
                      Decimal(str(line['unitPrice'])), 'AI_EXTRACTED')
                     for line in request['lines']]
    repeated = client.post('/api/v1/confirm', json=request)
    assert repeated.status_code == 200
    assert repeated.json()['recordIds'] == confirmed.json()['recordIds']
    assert counts(sid) == ('CONFIRMED', len(lines))


@pytest.mark.parametrize('old_name,new_name', [
    ('Stra\u00dfe', 'STRASSE'),
    ('\ufb03 flour', 'FFI flour'),
])
def test_latest_snapshot_uses_stored_casefold_key_without_changing_history(db, monkeypatch, old_name, new_name):
    owner = repository.owner_id()
    old_id, latest_id, other_owner_id = uuid4(), uuid4(), uuid4()
    as_of = date.today()
    base = dict(windowDays=7, loggedDays=7, averageDailyQuantity='1.25',
                trend='STABLE', suggestedRestockQty=9)
    repository.replace_baselines(as_of - timedelta(days=1),
                                [{**base, 'id': old_id, 'itemName': old_name}])
    repository.replace_baselines(as_of,
                                [{**base, 'id': latest_id, 'itemName': new_name,
                                  'averageDailyQuantity': '2.50', 'suggestedRestockQty': 18}])
    monkeypatch.setenv('OWNER_ID', str(uuid4()))
    repository.replace_baselines(as_of,
                                [{**base, 'id': other_owner_id, 'itemName': new_name}])
    monkeypatch.setenv('OWNER_ID', str(owner))
    with connect() as conn:
        # Make latest ordering deterministic, independent of test execution speed.
        for row_id, day in ((old_id, as_of - timedelta(days=1)), (latest_id, as_of)):
            conn.execute('UPDATE baseline_estimates SET computed_at=%s WHERE id=%s',
                         (datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc), row_id))
        before = conn.execute('SELECT * FROM baseline_estimates ORDER BY id').fetchall()
        keys = conn.execute('SELECT item_key FROM baseline_estimates WHERE owner_id=%s',
                            (owner,)).fetchall()
        assert keys == [(new_name.casefold(),)] * 2

    latest = repository.get_latest_baselines()
    assert len(latest) == 1
    assert latest[0]['id'] == latest_id
    assert latest[0]['ownerId'] == owner
    assert latest[0]['itemName'] == new_name
    assert latest[0]['averageDailyQuantity'] == Decimal('2.50')
    assert latest[0]['suggestedRestockQty'] == 18
    with connect() as conn:
        assert conn.execute('SELECT * FROM baseline_estimates ORDER BY id').fetchall() == before
