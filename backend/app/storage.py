"""ObjectStorage: private bucket for sanitized images (M3 4.1, 5.4). MinIO locally, Cloudflare R2 in production.

Keys are random (images/{uuid4}.jpg). Images are shown only through short-lived signed URLs.
S3_PUBLIC_ENDPOINT_URL lets signed URLs use a browser-reachable host (e.g. http://localhost:9000
locally, where the backend itself reaches MinIO as http://minio:9000).
"""
import os
from datetime import datetime, timezone
from uuid import uuid4

SIGNED_URL_SECONDS = 300


class StorageError(Exception):
    """Storage unavailable or misconfigured. Never carries credentials or object contents."""


class ObjectStorage:
    def __init__(self, endpoint_url, bucket, access_key_id, secret_access_key, public_endpoint_url=None):
        import boto3
        from botocore.config import Config
        config = Config(signature_version="s3v4", s3={"addressing_style": "path"},
                        connect_timeout=5, read_timeout=15, retries={"max_attempts": 2})
        kwargs = dict(aws_access_key_id=access_key_id, aws_secret_access_key=secret_access_key,
                      region_name="auto", config=config)
        self.bucket = bucket
        self.client = boto3.client("s3", endpoint_url=endpoint_url, **kwargs)
        self.signer = boto3.client("s3", endpoint_url=public_endpoint_url or endpoint_url, **kwargs)
        self._bucket_checked = False

    def _ensure_bucket(self):
        """Create the bucket on first use if it is missing (local MinIO starts empty)."""
        if self._bucket_checked:
            return
        from botocore.exceptions import ClientError
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in ("404", "NoSuchBucket", "NotFound"):
                raise
            self.client.create_bucket(Bucket=self.bucket)
        self._bucket_checked = True

    def put_object(self, data: bytes, content_type: str) -> str:
        key = f"images/{uuid4()}.jpg"
        try:
            self._ensure_bucket()
            self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        except Exception as exc:
            raise StorageError("could not store object") from exc
        return key

    def get_signed_url(self, key: str, expires_in: int = SIGNED_URL_SECONDS) -> str:
        try:
            return self.signer.generate_presigned_url(
                "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires_in)
        except Exception as exc:
            raise StorageError("could not sign URL") from exc

    def get_object(self, key: str) -> bytes:
        """The stored sanitized image (used by the storage check and tests)."""
        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except Exception as exc:
            raise StorageError("could not read object") from exc

    def delete_object(self, key: str) -> None:
        # S3 deletes are idempotent: deleting a missing key succeeds, so a retried job is safe.
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            raise StorageError("could not delete object") from exc

    def list_keys(self, prefix: str, older_than: datetime) -> list[str]:
        """Keys under `prefix` last modified before `older_than` (orphan reconciliation)."""
        try:
            self._ensure_bucket()
            keys = []
            for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
                keys += [item["Key"] for item in page.get("Contents", []) if item["LastModified"] < older_than]
            return keys
        except Exception as exc:
            raise StorageError("could not list objects") from exc


class InMemoryObjectStorage:
    """Test double with the same interface. `fail_puts` simulates an outage."""

    def __init__(self, fail_puts: bool = False, fail_deletes: bool = False, fail_gets: bool = False):
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.stored_at: dict[str, datetime] = {}
        self.fail_puts = fail_puts
        self.fail_deletes = fail_deletes
        self.fail_gets = fail_gets
        self.deleted: list[str] = []

    def put_object(self, data, content_type):
        if self.fail_puts:
            raise StorageError("simulated outage")
        key = f"images/{uuid4()}.jpg"
        self.objects[key] = (data, content_type)
        self.stored_at[key] = datetime.now(timezone.utc)
        return key

    def get_object(self, key):
        if self.fail_gets or key not in self.objects:
            raise StorageError("simulated read failure")
        return self.objects[key][0]

    def list_keys(self, prefix, older_than):
        return [key for key in self.objects if key.startswith(prefix) and self.stored_at[key] < older_than]

    def get_signed_url(self, key, expires_in=SIGNED_URL_SECONDS):
        return f"memory://{key}?expires={expires_in}"

    def delete_object(self, key):
        if self.fail_deletes:
            raise StorageError("simulated outage")
        self.objects.pop(key, None)
        self.stored_at.pop(key, None)
        self.deleted.append(key)


class UnconfiguredObjectStorage:
    """Used when S3 settings are absent: photo uploads return 503; typed text still works."""

    def put_object(self, data, content_type):
        raise StorageError("object storage is not configured")

    def get_signed_url(self, key, expires_in=SIGNED_URL_SECONDS):
        raise StorageError("object storage is not configured")

    def get_object(self, key):
        raise StorageError("object storage is not configured")

    def delete_object(self, key):
        raise StorageError("object storage is not configured")

    def list_keys(self, prefix, older_than):
        return []  # Nothing can have been stored.


_storage = None


def get_object_storage():
    """FastAPI dependency (one client per process); tests replace it through dependency_overrides."""
    global _storage
    if _storage is None:
        required = ("S3_ENDPOINT_URL", "S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY")
        if all(os.getenv(name) for name in required):
            _storage = ObjectStorage(os.environ["S3_ENDPOINT_URL"], os.environ["S3_BUCKET"],
                                     os.environ["S3_ACCESS_KEY_ID"], os.environ["S3_SECRET_ACCESS_KEY"],
                                     os.getenv("S3_PUBLIC_ENDPOINT_URL"))
        else:
            _storage = UnconfiguredObjectStorage()
    return _storage
