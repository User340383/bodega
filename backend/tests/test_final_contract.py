"""October 23 final specification: data, lifecycle and capture/history API."""
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from decimal import Decimal
import pytest
from test_database import db,client,make_submission,body,uncertain_line,counts
from app import repository, data_lifecycle
from app.database import connect,migrate
from app.clock import today


def manual(client,day=None,**line):
    request=body(uuid4(),**line);request['saleDate']=(day or today()).isoformat()
    response=client.post('/api/v1/manual-entries',json=request)
    assert response.status_code==201,response.text
    return request,response.json()


def test_final_manual_replay_and_confirmation_body_ignored(client):
    request,saved=manual(client,itemName='  Rice   (lb) ')
    assert client.post('/api/v1/manual-entries',json=request).json()['recordIds']==saved['recordIds']
    repeated=client.post('/api/v1/confirm',json={'submissionId':request['submissionId'],'lines':'invalid and ignored'})
    assert repeated.status_code==200 and repeated.json()['recordIds']==saved['recordIds']
    with connect() as conn:
        assert conn.execute('SELECT input_type,status,confirmed_at IS NOT NULL FROM submissions WHERE id=%s',(request['submissionId'],)).fetchone()==('MANUAL','CONFIRMED',True)
        assert conn.execute('SELECT item_key FROM sales_records').fetchone()[0]=='rice (lb)'


@pytest.mark.parametrize('business_zone', ['America/Santo_Domingo', 'Asia/Tokyo'])
@pytest.mark.parametrize('server_zone', ['Pacific/Honolulu', 'Pacific/Kiritimati'])
@pytest.mark.parametrize('offset,expected_status', [(0, 201), (-365, 201), (1, 400), (-366, 400)])
def test_manual_date_boundaries_use_business_timezone(client, monkeypatch, business_zone, server_zone, offset, expected_status):
    import time
    from zoneinfo import ZoneInfo
    from app import clock

    instant = datetime(2026, 11, 5, 2, 30, tzinfo=timezone.utc)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            local = instant.astimezone(tz)
            return local if tz is not None else local.replace(tzinfo=None)

    try:
        with monkeypatch.context() as environment:
            environment.setenv('APP_TIMEZONE', business_zone)
            environment.setenv('TZ', server_zone)
            time.tzset()
            environment.setattr(clock, 'datetime', FrozenDatetime)
            business_today = instant.astimezone(ZoneInfo(business_zone)).date()
            sale_date = business_today + timedelta(days=offset)
            request = body(uuid4())
            request['saleDate'] = sale_date.isoformat()
            response = client.post('/api/v1/manual-entries', json=request)
            assert response.status_code == expected_status, response.text
            with connect() as conn:
                rows = conn.execute('SELECT sale_date,status FROM submissions').fetchall()
            if expected_status == 201:
                assert rows == [(sale_date, 'CONFIRMED')]
            else:
                assert rows == []
                assert response.json()['error']['code'] == 'VALIDATION_ERROR'
                assert 'saleDate' in response.json()['error']['fields']
    finally:
        time.tzset()


@pytest.mark.parametrize('changes',[{'quantity':'0.001'},{'quantity':'100000'},{'unitPrice':'10000000'},{'unitPrice':'1.001'},{'itemName':'x'*101}])
def test_final_limits_enforced_atomically(client,changes):
    sid=make_submission();result=client.post('/api/v1/confirm',json=body(sid,**changes))
    assert result.status_code==400 and result.json()['error']['code']=='VALIDATION_ERROR'
    assert counts(sid)==('NEEDS_VERIFICATION',0)


def test_final_default_range_boundary_and_order(client):
    manual(client,today()-timedelta(days=27),itemName='Included')
    manual(client,today()-timedelta(days=28),itemName='Outside')
    response=client.get('/api/v1/sales');assert response.status_code==200
    rows=response.json();assert [r['itemName'] for r in rows]==['Included']
    assert rows[0]['lineTotal']==3 and isinstance(rows[0]['quantity'],(int,float))
    assert client.get(f'/api/v1/sales?start={today()-timedelta(days=366)}&end={today()}').status_code==400
    assert client.get(f'/api/v1/sales?start={today()}&end={today()-timedelta(days=1)}').json()['error']['code']=='VALIDATION_ERROR'


def test_exact_json_totals_at_documented_limits(client):
    manual(client,quantity='99999.99',unitPrice='9999999.99')
    response=client.get('/api/v1/sales')
    assert response.status_code==200
    assert '"lineTotal":999999899000.0001' in response.text


def test_only_documented_capture_history_routes_are_exposed(client):
    paths=client.get('/openapi.json').json()['paths']
    assert '/api/v1/manual-entries' in paths and '/api/v1/submissions' in paths
    for path in ('/api/v1/sales/legacy','/api/v1/submissions/manual','/api/v1/data',
                 '/api/v1/submissions/{submission_id}/manual-review',
                 '/api/v1/submissions/{submission_id}/discard'):
        assert path not in paths


def test_rejected_direct_confirmation_and_pending(client):
    sid=make_submission('REJECTED')
    assert any(r['submissionId']==str(sid) for r in client.get('/api/v1/submissions').json())
    assert client.post('/api/v1/confirm',json=body(sid)).status_code==201
    assert client.get('/api/v1/submissions').json()==[]
    assert len(client.get(f'/api/v1/submissions/{sid}').json()['records'])==1


def test_stale_and_thirty_day_retention(db):
    stale=make_submission('CLASSIFYING');old=make_submission();confirmed=make_submission()
    from app.main import ConfirmRequest
    repository.save_sales_records(ConfirmRequest(**body(confirmed)))
    with connect() as conn:
        conn.execute("UPDATE submissions SET created_at=now()-interval '31 days',masked_text='masked' WHERE id IN (%s,%s)",(old,confirmed))
    data_lifecycle.sweep(None,datetime.now(timezone.utc)+timedelta(seconds=121))
    assert counts(stale)[0]=='AI_UNAVAILABLE'
    assert counts(confirmed)==('CONFIRMED',1)
    with connect() as conn:
        assert conn.execute('SELECT 1 FROM submissions WHERE id=%s',(old,)).fetchone() is None
        assert conn.execute('SELECT masked_text FROM submissions WHERE id=%s',(confirmed,)).fetchone()==(None,)


def test_snapshot_replace_scope_rollback_and_delete(client):
    row=dict(itemName='Rice',windowDays=7,loggedDays=7,averageDailyQuantity='2.55555',trend='STABLE',suggestedRestockQty=18)
    data_lifecycle.replace_baselines(today(),[row]);data_lifecycle.replace_baselines(today(),[row])
    data_lifecycle.replace_baselines(today()-timedelta(days=1),[row])
    with pytest.raises(Exception): data_lifecycle.replace_baselines(today(),[row,{**row,'itemName':' rice '}])
    with connect() as conn:
        assert conn.execute('SELECT count(*) FROM baseline_estimates').fetchone()[0]==2
        assert conn.execute('SELECT average_daily_quantity FROM baseline_estimates LIMIT 1').fetchone()[0]==Decimal('2.5556')
    request,_=manual(client)
    assert client.delete('/api/v1/submissions/'+request['submissionId']).status_code==204
    with connect() as conn:
        assert conn.execute('SELECT count(*) FROM sales_records').fetchone()[0]==0
        assert conn.execute('SELECT count(*) FROM baseline_estimates').fetchone()[0]==0


def test_legacy_snapshot_duplicates_survive_upgrade(db,monkeypatch):
    # The main upgrade regression supplies populated old-schema sales. Here prove
    # unknown historical asOf dates are not fabricated or old snapshots discarded.
    from pathlib import Path
    from psycopg import sql
    import os,psycopg
    schema='test_legacy_'+uuid4().hex
    with psycopg.connect(os.environ['TEST_DATABASE_URL'],autocommit=True) as c:c.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    monkeypatch.setenv('BODEGA_DB_SCHEMA',schema)
    try:
        with connect() as c:
            c.execute('CREATE TABLE schema_migrations(version text PRIMARY KEY)')
            for filename in ('001_data_state.sql','002_nonblank_labels.sql','003_remove_label_length_limits.sql'):
                c.execute((Path(__file__).parents[1]/'migrations'/filename).read_text());c.execute('INSERT INTO schema_migrations VALUES (%s)',(filename,))
            c.execute('INSERT INTO owners(id) VALUES (%s)',(repository.owner_id(),))
            for _ in range(2):c.execute("INSERT INTO baseline_estimates(id,owner_id,item_name,window_days,logged_days,average_daily_quantity,trend,suggested_restock_qty) VALUES (%s,%s,'Rice',7,7,2.5,'STABLE',18)",(uuid4(),repository.owner_id()))
        legacy_sid=uuid4();legacy_rid=uuid4()
        with connect() as c:
            c.execute('INSERT INTO submissions(id,owner_id,sale_date) VALUES (%s,%s,%s)',(legacy_sid,repository.owner_id(),today()))
            c.execute("UPDATE submissions SET status='CLASSIFYING' WHERE id=%s",(legacy_sid,))
            c.execute("UPDATE submissions SET status='NEEDS_VERIFICATION' WHERE id=%s",(legacy_sid,))
            c.execute("INSERT INTO sales_records(id,submission_id,line_number,item_name,quantity,unit_price,sale_date,source) VALUES (%s,%s,0,'Stra\u00dfe',123456.123456,0.00001,%s,'MANUAL')",(legacy_rid,legacy_sid,today()))
            c.execute("UPDATE submissions SET status='CONFIRMED' WHERE id=%s",(legacy_sid,))
        with pytest.raises(Exception,match='Upgrade blocked'):
            migrate()
        with connect() as c:
            assert c.execute('SELECT quantity,unit_price,item_name FROM sales_records WHERE id=%s',(legacy_rid,)).fetchone()==(Decimal('123456.123456'),Decimal('0.00001'),'Stra\u00dfe')
            assert c.execute('SELECT count(*) FROM baseline_estimates').fetchone()[0]==2
            assert c.execute('SELECT count(*) FROM schema_migrations').fetchone()[0]==3
    finally:
        with psycopg.connect(os.environ['TEST_DATABASE_URL'],autocommit=True) as c:c.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
