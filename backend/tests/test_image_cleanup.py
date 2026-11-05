"""Durable image cleanup: real PostgreSQL, real isolated MinIO, crash boundaries."""
import os
import asyncio
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from uuid import uuid4

import psycopg
import pytest
from botocore.exceptions import ClientError
from psycopg import sql
from fastapi.testclient import TestClient

from test_database import db, client, body
from test_workflows import storage, image_bytes
from app import data_lifecycle, image_cleanup, repository
from app.database import connect, migrate
from app.main import app, ConfirmRequest
from app.storage import StorageError
from app import main as api


def image_submission(storage, *, confirmed=False):
    key = storage.put(image_bytes(), 'image/jpeg')
    sid = repository.create_submission(date.today(), image_key=key)
    repository.update_status(sid, 'CLASSIFYING')
    repository.update_status(sid, 'NEEDS_VERIFICATION')
    if confirmed:
        repository.save_sales_records(ConfirmRequest(**body(sid)))
    return sid, key


def jobs():
    with connect() as conn:
        return conn.execute('''SELECT bucket_name,image_key,attempt_count,last_attempt_at
            FROM image_deletion_jobs ORDER BY created_at,image_key''').fetchall()




@pytest.mark.parametrize('failure', ['process-crash', 'ambiguous-put'])
def test_unlinked_upload_survives_interruption_then_expires(db, storage, monkeypatch, failure):
    if failure == 'process-crash':
        script = '''
import os
from app.storage import ObjectStorage
store=ObjectStorage(endpoint=os.environ['TEST_S3_ENDPOINT'],bucket=os.environ['TEST_RETRY_BUCKET'],
    access_key='testaccess',secret_key='test-only-storage-password')
store.put(b'sanitized pixels', 'image/jpeg')
os._exit(23)
'''
        process = subprocess.run([sys.executable, '-c', script],
                                 env={**os.environ, 'TEST_RETRY_BUCKET': storage.bucket},
                                 capture_output=True, timeout=30)
        assert process.returncode == 23, process.stderr
    else:
        from botocore.exceptions import EndpointConnectionError
        original_put = storage.client.put_object

        def accepted_but_response_lost(**kwargs):
            original_put(**kwargs)
            raise EndpointConnectionError(endpoint_url='https://private-storage.test')

        monkeypatch.setattr(storage.client, 'put_object', accepted_but_response_lost)
        with pytest.raises(StorageError):
            storage.put(image_bytes(), 'image/jpeg')
    key = storage.client.list_objects_v2(Bucket=storage.bucket)['Contents'][0]['Key']
    assert jobs() == []
    with connect() as conn:
        assert conn.execute('SELECT count(*) FROM submissions').fetchone()[0] == 0
    data_lifecycle.sweep(storage)
    assert storage.client.head_object(Bucket=storage.bucket, Key=key)
    data_lifecycle.sweep(storage, datetime.now(timezone.utc) + timedelta(days=31))
    with pytest.raises(ClientError):
        storage.client.head_object(Bucket=storage.bucket, Key=key)
    assert jobs() == []


def test_orphan_discovery_preserves_live_fresh_and_unmarked_objects(db, storage):
    now = datetime.now(timezone.utc)
    orphan, live, fresh = [storage.put(image_bytes(), 'image/jpeg') for _ in range(3)]
    for key in (orphan, live):
        storage.client.copy_object(Bucket=storage.bucket, Key=key,
            CopySource={'Bucket': storage.bucket, 'Key': key}, MetadataDirective='REPLACE',
            Metadata={'bodega-raw': '1', 'expires-at': (now - timedelta(days=1)).isoformat()})
    storage.client.put_object(Bucket=storage.bucket, Key='unmarked', Body=b'keep',
                              Metadata={'expires-at': (now - timedelta(days=1)).isoformat()})
    sid = repository.create_submission(date.today(), image_key=live)
    data_lifecycle.sweep(storage, now)
    assert jobs()[0][:3] == (storage.bucket, live, 0)
    assert len(jobs()) == 1
    assert repository.get_image_key(sid) == live
    for key in (live, fresh, 'unmarked'):
        assert storage.client.head_object(Bucket=storage.bucket, Key=key)
    with pytest.raises(ClientError):
        storage.client.head_object(Bucket=storage.bucket, Key=orphan)


def test_discovery_failure_rolls_back_partial_jobs_and_existing_retries_continue(db, storage, monkeypatch, caplog):
    sid, deleted = image_submission(storage)
    data_lifecycle.delete_submission(sid, bucket_name=storage.bucket)
    orphan = storage.put(image_bytes(), 'image/jpeg')

    def interrupted_listing(now):
        yield orphan
        raise StorageError('private listing details')

    with monkeypatch.context() as outage:
        outage.setattr(storage, 'expired_keys', interrupted_listing)
        data_lifecycle.sweep(storage)
    assert jobs() == []  # The partial discovery transaction rolled back.
    assert storage.client.head_object(Bucket=storage.bucket, Key=orphan)
    with pytest.raises(ClientError):
        storage.client.head_object(Bucket=storage.bucket, Key=deleted)
    assert 'Expired image discovery deferred' in caplog.text
    assert 'private listing details' not in caplog.text
    data_lifecycle.sweep(storage, datetime.now(timezone.utc) + timedelta(days=31))
    with pytest.raises(ClientError):
        storage.client.head_object(Bucket=storage.bucket, Key=orphan)


class FailedStorage:
    def __init__(self, bucket):
        self.bucket = bucket
        self.calls = []

    def delete(self, key):
        self.calls.append(key)
        raise StorageError('private provider details must not be logged')

    def expired_keys(self, now=None):
        return iter(())


def test_delete_commits_before_object_call_and_clears_job(client, storage, monkeypatch):
    sid, key = image_submission(storage, confirmed=True)

    class ObserveCommit:
        bucket = storage.bucket

        def delete(self, object_key):
            # A different connection must see both changes before S3 is called.
            with connect() as conn:
                assert conn.execute('SELECT 1 FROM submissions WHERE id=%s', (sid,)).fetchone() is None
                assert conn.execute('SELECT 1 FROM image_deletion_jobs WHERE image_key=%s', (key,)).fetchone()
            storage.delete(object_key)

    monkeypatch.setattr(app.state, 'object_storage', ObserveCommit(), raising=False)
    assert client.delete(f'/api/v1/submissions/{sid}').status_code == 204
    assert jobs() == []
    with pytest.raises(ClientError):
        storage.client.head_object(Bucket=storage.bucket, Key=key)


def test_failed_delete_survives_row_and_owner_deletion_and_retries(client, storage, monkeypatch, caplog):
    sid, key = image_submission(storage, confirmed=True)
    failed = FailedStorage(storage.bucket)
    monkeypatch.setattr(app.state, 'object_storage', failed, raising=False)
    assert client.delete(f'/api/v1/submissions/{sid}').status_code == 204
    queued = jobs()
    assert queued[0][:3] == (storage.bucket, key, 1) and queued[0][3] is not None
    assert 'private provider details' not in caplog.text and key not in caplog.text
    with connect() as conn:
        conn.execute('DELETE FROM owners WHERE id=%s', (repository.owner_id(),))
    assert len(jobs()) == 1  # No cascading FK can lose a deletion obligation.
    data_lifecycle.sweep(storage)
    assert jobs() == []
    with pytest.raises(ClientError):
        storage.client.head_object(Bucket=storage.bucket, Key=key)


def test_database_failure_rolls_back_job_and_never_deletes_object(client, storage, monkeypatch):
    sid, key = image_submission(storage, confirmed=True)
    failed = FailedStorage(storage.bucket)
    monkeypatch.setattr(app.state, 'object_storage', failed, raising=False)
    data_lifecycle.replace_baselines(date.today(), [dict(itemName='Bread', windowDays=7, loggedDays=7,
        averageDailyQuantity='2.5', trend='STABLE', suggestedRestockQty=18)])
    with connect() as conn:
        conn.execute("CREATE FUNCTION fail_delete() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected'; END $$")
        conn.execute('CREATE TRIGGER fail_delete BEFORE DELETE ON submissions FOR EACH ROW EXECUTE FUNCTION fail_delete()')
    assert client.delete(f'/api/v1/submissions/{sid}').status_code == 500
    assert failed.calls == [] and jobs() == []
    with connect() as conn:
        assert conn.execute('SELECT count(*) FROM sales_records').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM baseline_estimates').fetchone()[0] == 1
    assert storage.client.head_object(Bucket=storage.bucket, Key=key)


def test_queue_survives_commit_without_an_immediate_attempt(db, storage):
    sid, key = image_submission(storage)
    data_lifecycle.delete_submission(sid, bucket_name=storage.bucket)
    assert jobs()[0][:3] == (storage.bucket, key, 0)
    assert image_cleanup.process_pending(None) == 0
    assert image_cleanup.process_pending(storage) == 1
    assert jobs() == []


def test_application_startup_drains_persisted_jobs(db, storage, monkeypatch):
    sid, key = image_submission(storage)
    data_lifecycle.delete_submission(sid, bucket_name=storage.bucket)
    assert len(jobs()) == 1
    monkeypatch.setattr(app.state, 'object_storage', storage, raising=False)
    with TestClient(app) as restarted:
        assert restarted.get('/health').status_code == 200
        assert jobs() == []
    with pytest.raises(ClientError):
        storage.client.head_object(Bucket=storage.bucket, Key=key)


def test_daily_worker_continues_after_database_and_storage_outages(monkeypatch, caplog):
    waits, calls = [], []
    outcomes = iter([psycopg.OperationalError('private database details'),
                     StorageError('private storage details'), None])

    async def clock_tick(seconds):
        waits.append(seconds)
        if len(waits) == 4:
            raise asyncio.CancelledError()

    def sweep(_storage):
        calls.append(True)
        failure = next(outcomes)
        if failure is not None:
            raise failure

    monkeypatch.setattr(api.asyncio, 'sleep', clock_tick)
    monkeypatch.setattr(api.data_lifecycle, 'sweep', sweep)
    monkeypatch.setattr(api, 'object_storage', lambda: None)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(api.cleanup_loop())
    assert len(calls) == 3 and waits == [86400] * 4
    assert 'private database details' not in caplog.text
    assert 'private storage details' not in caplog.text


def test_missing_bucket_keeps_original_submission(db, storage):
    sid, key = image_submission(storage)
    with pytest.raises(StorageError):
        data_lifecycle.delete_submission(sid)
    assert jobs() == [] and repository.get_image_key(sid) == key


def test_crash_after_s3_success_is_recovered_by_new_process(db, storage):
    sid, key = image_submission(storage)
    data_lifecycle.delete_submission(sid, bucket_name=storage.bucket)
    setup = '''
import os
from app.storage import ObjectStorage
from app.image_cleanup import process_pending
store=ObjectStorage(endpoint=os.environ['TEST_S3_ENDPOINT'],bucket=os.environ['TEST_RETRY_BUCKET'],
    access_key='testaccess',secret_key='test-only-storage-password')
'''
    crash = setup + '''
class CrashAfterDelete:
    bucket=store.bucket
    def delete(self,key):
        store.delete(key)
        os._exit(23)
process_pending(CrashAfterDelete())
'''
    env = {**os.environ, 'TEST_RETRY_BUCKET': storage.bucket}
    first = subprocess.run([sys.executable, '-c', crash], env=env, capture_output=True, timeout=30)
    assert first.returncode == 23
    assert jobs()[0][:3] == (storage.bucket, key, 0)  # Attempt bookkeeping rolled back.
    with pytest.raises(ClientError):
        storage.client.head_object(Bucket=storage.bucket, Key=key)
    second = subprocess.run([sys.executable, '-c', setup + '\nprint(process_pending(store))'],
        env=env, capture_output=True, text=True, timeout=30)
    assert second.returncode == 0, second.stderr
    assert second.stdout.strip() == '1' and jobs() == []


def test_concurrent_workers_skip_locked_job(db, storage):
    sid, _ = image_submission(storage)
    data_lifecycle.delete_submission(sid, bucket_name=storage.bucket)
    started, release = Event(), Event()
    calls = []

    class SlowStorage:
        bucket = storage.bucket

        def delete(self, key):
            calls.append(key)
            started.set()
            assert release.wait(10)
            storage.delete(key)

    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(image_cleanup.process_pending, SlowStorage())
        try:
            assert started.wait(10)
            assert image_cleanup.process_pending(SlowStorage()) == 0
        finally:
            release.set()
        assert worker.result(timeout=10) == 1
    assert len(calls) == 1 and jobs() == []


def test_bucket_scoping_and_live_reference_protection(db, storage):
    sid, key = image_submission(storage)
    other = repository.create_submission(date.today(), image_key=key)
    data_lifecycle.delete_submission(sid, bucket_name=storage.bucket)
    wrong = FailedStorage('another-bucket')
    assert image_cleanup.process_pending(wrong) == 0 and wrong.calls == []
    assert image_cleanup.process_pending(storage) == 0 and jobs()[0][2] == 0
    repository.update_status(other, 'CLASSIFYING')
    repository.update_status(other, 'REJECTED')
    data_lifecycle.delete_submission(other, bucket_name=storage.bucket)
    assert len(jobs()) == 1
    assert image_cleanup.process_pending(storage) == 1 and jobs() == []


def test_one_failed_job_does_not_block_other_jobs(db, storage):
    one, first_key = image_submission(storage)
    two, _ = image_submission(storage)
    for sid in (one, two):
        data_lifecycle.delete_submission(sid, bucket_name=storage.bucket)

    class PartlyAvailable:
        bucket = storage.bucket

        def delete(self, key):
            if key == first_key:
                raise StorageError('unavailable')
            storage.delete(key)

    assert image_cleanup.process_pending(PartlyAvailable()) == 1
    assert jobs()[0][:3] == (storage.bucket, first_key, 1)
    assert image_cleanup.process_pending(storage) == 1 and jobs() == []


def test_expiration_preserves_history_and_durably_retries_images(db, storage):
    confirmed, key1 = image_submission(storage, confirmed=True)
    opened, key2 = image_submission(storage)
    with connect() as conn:
        conn.execute("UPDATE submissions SET created_at=now()-interval '31 days' WHERE id IN (%s,%s)", (confirmed, opened))
    data_lifecycle.sweep(FailedStorage(storage.bucket), datetime.now(timezone.utc))
    assert len(jobs()) == 2 and all(row[2] == 1 for row in jobs())
    with connect() as conn:
        assert conn.execute('SELECT image_key FROM submissions WHERE id=%s', (confirmed,)).fetchone() == (None,)
        assert conn.execute('SELECT 1 FROM submissions WHERE id=%s', (opened,)).fetchone() is None
        assert conn.execute('SELECT count(*) FROM sales_records').fetchone()[0] == 1
    data_lifecycle.sweep(storage)
    assert jobs() == []
    for key in (key1, key2):
        with pytest.raises(ClientError):
            storage.client.head_object(Bucket=storage.bucket, Key=key)


def test_outbox_upgrade_is_repeatable_and_preserves_populated_v6(db, monkeypatch):
    schema = 'test_outbox_upgrade_' + uuid4().hex
    url = os.environ['TEST_DATABASE_URL']
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    monkeypatch.setenv('BODEGA_DB_SCHEMA', schema)
    try:
        with connect() as conn:
            conn.execute('CREATE TABLE schema_migrations(version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())')
            for path in sorted((Path(__file__).parents[1] / 'migrations').glob('00[1-6]*.sql')):
                conn.execute(path.read_text())
                conn.execute('INSERT INTO schema_migrations(version) VALUES (%s)', (path.name,))
        sid = repository.create_submission(date.today())
        repository.update_status(sid, 'CLASSIFYING')
        repository.update_status(sid, 'NEEDS_VERIFICATION')
        saved, _ = repository.save_sales_records(ConfirmRequest(**body(sid)))
        data_lifecycle.replace_baselines(date.today(), [dict(itemName='Bread', windowDays=7,
            loggedDays=7, averageDailyQuantity='2.5', trend='STABLE', suggestedRestockQty=18)])
        with connect() as conn:
            before_sales = conn.execute('SELECT * FROM sales_records').fetchall()
            before_baselines = conn.execute('SELECT * FROM baseline_estimates').fetchall()
            before_ledger = conn.execute('SELECT version,applied_at FROM schema_migrations ORDER BY version').fetchall()
        migrate()
        migrate()
        with connect() as conn:
            assert conn.execute('SELECT * FROM sales_records').fetchall() == before_sales
            assert conn.execute('SELECT * FROM baseline_estimates').fetchall() == before_baselines
            assert conn.execute('SELECT version,applied_at FROM schema_migrations ORDER BY version').fetchall()[:6] == before_ledger
            assert conn.execute('SELECT count(*) FROM schema_migrations').fetchone()[0] == 7
            assert conn.execute('SELECT id FROM sales_records').fetchone()[0] == saved[0]
        assert jobs() == []
    finally:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
