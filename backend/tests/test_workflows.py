import asyncio
import io
import os
import time
from datetime import date, timedelta, datetime, timezone
from uuid import uuid4
import pytest
from PIL import Image
from botocore.exceptions import ClientError, EndpointConnectionError
from test_database import db, client, make_submission, body
from app import repository
from app.database import connect
from app.main import app
from app.storage import ObjectStorage, StorageError


def image_bytes(fmt='JPEG'):
    image = Image.new('RGB',(10,10),'white')
    exif = Image.Exif(); exif[271] = 'Private device'; exif[306] = '2026:01:01 12:00:00'
    stream = io.BytesIO(); image.save(stream,format=fmt,exif=exif)
    return stream.getvalue()



@pytest.fixture
def storage():
    endpoint = os.getenv('TEST_S3_ENDPOINT')
    if not endpoint: pytest.skip('TEST_S3_ENDPOINT must point to disposable MinIO')
    store = ObjectStorage(endpoint=endpoint,bucket='test-'+uuid4().hex,access_key='testaccess',secret_key='test-only-storage-password')
    for attempt in range(50):
        try:
            store.client.list_buckets(); break
        except EndpointConnectionError:
            if attempt == 49: raise
            time.sleep(0.2)
    yield store
    try:
        for row in store.client.list_objects_v2(Bucket=store.bucket).get('Contents',[]):
            store.delete(row['Key'])
        store.client.delete_bucket(Bucket=store.bucket)
    except ClientError as exc:
        if exc.response['Error']['Code'] != 'NoSuchBucket': raise



def test_submission_deletion_preserves_other_owner(client,monkeypatch):
    owner=os.environ['OWNER_ID']
    sid=make_submission();client.post('/api/v1/confirm',json=body(sid))
    monkeypatch.setenv('OWNER_ID',str(uuid4()))
    other=make_submission();client.post('/api/v1/confirm',json=body(other))
    assert client.delete('/api/v1/submissions/'+str(sid)).status_code==404
    monkeypatch.setenv('OWNER_ID',owner)
    assert client.delete('/api/v1/submissions/'+str(sid)).status_code==204
    assert repository.get_confirmed_records()==[]
    with connect() as conn:assert conn.execute('SELECT count(*) FROM sales_records').fetchone()[0]==1






def test_discard_and_delete_remove_raw_objects(client,storage,monkeypatch):
    monkeypatch.setattr(app.state,'object_storage',storage,raising=False)
    for action in ('discard','delete'):
        key = storage.put(image_bytes(),'image/jpeg')
        sid = repository.create_submission(date.today(),image_key=key)
        repository.update_status(sid,'CLASSIFYING')
        repository.update_status(sid,'REJECTED')
        if action == 'discard':
            result = client.delete(f'/api/v1/submissions/{sid}')
        else:
            result = client.delete(f'/api/v1/submissions/{sid}')
        assert result.status_code == 204,result.text
        with pytest.raises(ClientError): storage.client.head_object(Bucket=storage.bucket,Key=key)



def test_ten_backend_process_restarts_during_confirmation(client):
    """Kill only spawned test servers; preserve a committed row across ten crashes."""
    import subprocess
    import sys
    import httpx
    from concurrent.futures import ThreadPoolExecutor
    saved = make_submission()
    assert client.post('/api/v1/confirm',json=body(saved)).status_code == 201
    with connect() as conn:
        conn.execute("CREATE FUNCTION slow_confirm() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.status='CONFIRMED' THEN PERFORM pg_sleep(5); END IF; RETURN NEW; END $$")
        conn.execute('CREATE TRIGGER slow_confirm BEFORE UPDATE ON submissions FOR EACH ROW EXECUTE FUNCTION slow_confirm()')
    pending = []
    for _ in range(10):
        sid = make_submission(); pending.append(sid)
        process = subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port','18765','--no-access-log'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            for attempt in range(100):
                try:
                    if httpx.get('http://127.0.0.1:18765/health',timeout=0.2).status_code == 200: break
                except httpx.HTTPError: pass
                time.sleep(0.05)
            else: pytest.fail('Test server failed to start')
            def confirm():
                try:
                    return httpx.post('http://127.0.0.1:18765/api/v1/confirm',json=body(sid),headers={'Authorization':'Bearer test-code'},timeout=10)
                except httpx.HTTPError: return None
            with ThreadPoolExecutor(max_workers=1) as pool:
                request = pool.submit(confirm)
                for attempt in range(100):
                    with connect() as conn:
                        sleeping = conn.execute("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE wait_event='PgSleep' AND query LIKE 'UPDATE submissions%')").fetchone()[0]
                    if sleeping: break
                    time.sleep(0.02)
                else: pytest.fail('Confirmation never entered its transaction')
                process.kill(); process.wait(timeout=5)
                request.result(timeout=10)
            with connect() as conn:
                assert conn.execute('SELECT count(*) FROM sales_records WHERE submission_id=%s',(saved,)).fetchone()[0] == 1
                assert conn.execute('SELECT count(*) FROM sales_records WHERE submission_id=%s',(sid,)).fetchone()[0] == 0
                assert conn.execute('SELECT status FROM submissions WHERE id=%s',(sid,)).fetchone()[0] == 'NEEDS_VERIFICATION'
        finally:
            if process.poll() is None: process.kill(); process.wait(timeout=5)
    with connect() as conn:
        conn.execute('DROP TRIGGER slow_confirm ON submissions')
    for sid in pending:
        assert client.post('/api/v1/confirm',json=body(sid)).status_code == 201
        assert client.post('/api/v1/confirm',json=body(sid)).status_code == 200
    assert len(repository.get_confirmed_records()) == 11



def test_public_bucket_is_rejected(storage):
    import json
    storage.put(image_bytes(),'image/jpeg')
    policy = {'Version':'2012-10-17','Statement':[{'Effect':'Allow','Principal':'*','Action':['s3:GetObject'],'Resource':[f'arn:aws:s3:::{storage.bucket}/*']}]}
    storage.client.put_bucket_policy(Bucket=storage.bucket,Policy=json.dumps(policy))
    with pytest.raises(StorageError): storage.put(image_bytes(),'image/jpeg')



def test_delete_storage_failure_keeps_durable_cleanup(client,monkeypatch):
    sid = make_submission(); client.post('/api/v1/confirm',json=body(sid))
    with connect() as conn: conn.execute('UPDATE submissions SET image_key=%s WHERE id=%s',('key',sid))
    class Broken:
        bucket = 'test-deferred-bucket'
        def delete(self,key): raise StorageError('private details')
    monkeypatch.setattr(app.state,'object_storage',Broken(),raising=False)
    result = client.delete(f'/api/v1/submissions/{sid}')
    assert result.status_code == 204 and repository.get_confirmed_records() == []
    assert 'private details' not in result.text
    with connect() as conn:
        assert conn.execute('SELECT image_key,attempt_count FROM image_deletion_jobs').fetchone() == ('key',1)



def test_safe_validation_log_fields(client,caplog):
    secret = 'contact@example.test'
    client.post('/api/v1/confirm',json=body(uuid4(),quantity=secret))
    import json
    entries = [json.loads(r.message) for r in caplog.records if r.message.startswith('{')]
    assert entries and set(entries[-1]) == {'timestamp','requestId','component','operation','errorCategory','durationMs'}
    assert secret not in caplog.text
