"""R2 uses mocked provider responses; MinIO and PostgreSQL remain real."""
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import httpx
import pytest
from botocore.exceptions import ClientError
from botocore.stub import ANY, Stubber

from test_database import db, client, body, counts
from test_workflows import storage, image_bytes
from app import clock, repository, data_lifecycle
from app.database import connect
from app.main import app
from app.storage import ObjectStorage, StorageError


@pytest.fixture
def r2(monkeypatch):
    monkeypatch.delenv('S3_PUBLIC_ENDPOINT_URL', raising=False)
    monkeypatch.setenv('CLOUDFLARE_API_TOKEN', 'test-only-privacy-token')
    store = ObjectStorage(endpoint='https://' + 'a' * 32 + '.r2.cloudflarestorage.com',
                          bucket='private-images', access_key='testaccess', secret_key='testsecret')
    policy_calls = []

    def unsupported(**kwargs):
        policy_calls.append(kwargs)
        raise ClientError({'Error': {'Code': 'NotImplemented', 'Message': 'Unsupported operation'},
                           'ResponseMetadata': {'HTTPStatusCode': 501}}, 'GetBucketPolicy')

    monkeypatch.setattr(store.client, 'get_bucket_policy', unsupported)
    return store, policy_calls


def mock_privacy(monkeypatch, managed=None, custom=None, *, status=200, invalid_json=False):
    calls = []
    actual_client = httpx.Client

    def response(request):
        calls.append(request)
        assert request.url.host == 'api.cloudflare.com'
        assert request.headers['Authorization'] == 'Bearer test-only-privacy-token'
        assert request.method == 'GET'
        if invalid_json:
            return httpx.Response(status, text='private non-JSON response')
        result = managed if request.url.path.endswith('/managed') else custom
        return httpx.Response(status, json={'success': True, 'result': result})

    monkeypatch.setattr(httpx, 'Client', lambda **kwargs:
                        actual_client(transport=httpx.MockTransport(response), **kwargs))
    return calls


def test_r2_upload_does_not_call_unsupported_policy_operation(r2, monkeypatch):
    store, policy_calls = r2
    calls = mock_privacy(monkeypatch, {'enabled': False}, {'domains': []})
    data = image_bytes()
    with Stubber(store.client) as s3:
        s3.add_response('head_bucket', {}, {'Bucket': store.bucket})
        s3.add_response('put_object', {}, {'Bucket': store.bucket, 'Key': ANY, 'Body': data,
            'ContentType': 'image/jpeg', 'Metadata': ANY})
        key = store.put(data, 'image/jpeg')
        s3.assert_no_pending_responses()
    assert policy_calls == []
    assert [request.url.path.rsplit('/', 1)[-1] for request in calls] == ['managed', 'custom']
    signed = urlsplit(store.signed_url(key))
    assert signed.scheme == 'https'
    assert signed.hostname.endswith('.r2.cloudflarestorage.com')
    assert parse_qs(signed.query)['X-Amz-Expires'] == ['300']


@pytest.mark.parametrize('managed,custom', [
    ({'enabled': True}, {'domains': []}),
    ({'enabled': False}, {'domains': [{'enabled': True}]}),
    ({}, {'domains': []}),
    ({'enabled': False}, {}),
    ({'enabled': False}, {'domains': [{'enabled': 'false'}]}),
])
def test_r2_public_or_unverified_settings_block_upload(r2, monkeypatch, managed, custom):
    store, _ = r2
    calls = mock_privacy(monkeypatch, managed, custom)
    with Stubber(store.client) as s3:
        s3.add_response('head_bucket', {}, {'Bucket': store.bucket})
        with pytest.raises(StorageError):
            store.put(image_bytes(), 'image/jpeg')
        s3.assert_no_pending_responses()
    assert calls  # Rejection must come from the supported privacy check.


@pytest.mark.parametrize('status,invalid_json', [(403, False), (503, False), (200, True)])
def test_r2_privacy_failures_are_closed_and_sanitized(r2, monkeypatch, status, invalid_json):
    store, _ = r2
    calls = mock_privacy(monkeypatch, status=status, invalid_json=invalid_json)
    with Stubber(store.client) as s3:
        s3.add_response('head_bucket', {}, {'Bucket': store.bucket})
        with pytest.raises(StorageError) as error:
            store.put(image_bytes(), 'image/jpeg')
        s3.assert_no_pending_responses()
    assert calls
    assert 'private non-JSON response' not in str(error.value)
    assert 'test-only-privacy-token' not in str(error.value)


def test_r2_requires_privacy_credentials(r2, monkeypatch):
    store, _ = r2
    monkeypatch.delenv('CLOUDFLARE_API_TOKEN')
    with Stubber(store.client) as s3:
        s3.add_response('head_bucket', {}, {'Bucket': store.bucket})
        with pytest.raises(StorageError, match='privacy'):
            store.put(image_bytes(), 'image/jpeg')


def test_private_minio_upload_and_signed_retrieval(storage):
    data = image_bytes()
    key = storage.put(data, 'image/jpeg')
    assert httpx.get(storage.signed_url(key)).content == data
    unsigned = f"{storage.client.meta.endpoint_url}/{storage.bucket}/{key}"
    assert httpx.get(unsigned).status_code == 403
    storage.delete(key)
    with pytest.raises(ClientError):
        storage.client.head_object(Bucket=storage.bucket, Key=key)


def test_r2_deletion_retry_and_orphan_discovery_use_supported_operations(client, r2, monkeypatch):
    store, policy_calls = r2
    mock_privacy(monkeypatch, {'enabled': False}, {'domains': []})
    monkeypatch.setattr(app.state, 'object_storage', store, raising=False)
    data = image_bytes()
    with Stubber(store.client) as s3:
        s3.add_response('head_bucket', {}, {'Bucket': store.bucket})
        s3.add_response('put_object', {}, {'Bucket': store.bucket, 'Key': ANY, 'Body': data,
            'ContentType': 'image/jpeg', 'Metadata': ANY})
        key = store.put(data, 'image/jpeg')
        sid = repository.create_submission(clock.today(), image_key=key)
        repository.update_status(sid, 'CLASSIFYING')
        repository.update_status(sid, 'NEEDS_VERIFICATION')
        assert client.post('/api/v1/confirm', json=body(sid)).status_code == 201
        s3.add_client_error('delete_object', service_error_code='ServiceUnavailable',
                            http_status_code=503, expected_params={'Bucket': store.bucket, 'Key': key})
        assert client.delete(f'/api/v1/submissions/{sid}').status_code == 204
        with connect() as conn:
            assert conn.execute('SELECT image_key,attempt_count FROM image_deletion_jobs').fetchall() == [(key, 1)]
            assert conn.execute('SELECT count(*) FROM submissions').fetchone()[0] == 0
        s3.add_response('list_objects_v2', {'Contents': [{'Key': key}]}, {'Bucket': store.bucket})
        s3.add_response('head_object', {'Metadata': {'bodega-raw': '1',
            'expires-at': (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()}},
            {'Bucket': store.bucket, 'Key': key})
        s3.add_response('delete_object', {}, {'Bucket': store.bucket, 'Key': key})
        data_lifecycle.sweep(store)
        s3.assert_no_pending_responses()
    with connect() as conn:
        assert conn.execute('SELECT count(*) FROM image_deletion_jobs').fetchone()[0] == 0
    assert policy_calls == []


def test_non_r2_unsupported_policy_is_not_ignored(monkeypatch):
    store = ObjectStorage(endpoint='https://storage.example.test', bucket='private-images',
                          access_key='testaccess', secret_key='testsecret')

    def unsupported(**kwargs):
        raise ClientError({'Error': {'Code': 'NotImplemented'}}, 'GetBucketPolicy')

    monkeypatch.setattr(store.client, 'get_bucket_policy', unsupported)
    with Stubber(store.client) as s3:
        s3.add_response('head_bucket', {}, {'Bucket': store.bucket})
        with pytest.raises(StorageError):
            store.put(image_bytes(), 'image/jpeg')
        s3.assert_no_pending_responses()


@pytest.mark.parametrize('business_zone,server_zone,expected_status', [
    ('Pacific/Kiritimati', 'America/Los_Angeles', 201),
    ('America/Santo_Domingo', 'Asia/Tokyo', 400),
])
def test_confirmation_uses_business_today(client, monkeypatch, business_zone, server_zone, expected_status):
    instant = datetime(2026, 11, 5, 2, 30, tzinfo=timezone.utc)
    business_today = instant.astimezone(ZoneInfo(business_zone)).date()
    server_today = instant.astimezone(ZoneInfo(server_zone)).date()

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz)

    class ServerDate(date):
        @classmethod
        def today(cls):
            return server_today

    monkeypatch.setenv('APP_TIMEZONE', business_zone)
    monkeypatch.setattr(clock, 'datetime', FrozenDatetime)
    monkeypatch.setattr(repository, 'date', ServerDate)
    sale_date = max(business_today, server_today)
    sid = repository.create_submission(sale_date)
    repository.update_status(sid, 'CLASSIFYING')
    repository.update_status(sid, 'NEEDS_VERIFICATION')
    response = client.post('/api/v1/confirm', json=body(sid))
    assert response.status_code == expected_status, response.text
    if expected_status == 201:
        assert counts(sid) == ('CONFIRMED', 1)
        assert client.post('/api/v1/confirm', json=body(sid)).json()['recordIds'] == response.json()['recordIds']
        with connect() as conn:
            assert conn.execute('SELECT sale_date FROM submissions WHERE id=%s', (sid,)).fetchone()[0] == sale_date
    else:
        assert counts(sid) == ('NEEDS_VERIFICATION', 0)


def test_confirmation_preserves_valid_historical_dates(client):
    sid = repository.create_submission(clock.today() - timedelta(days=400))
    repository.update_status(sid, 'CLASSIFYING')
    repository.update_status(sid, 'NEEDS_VERIFICATION')
    assert client.post('/api/v1/confirm', json=body(sid)).status_code == 201
