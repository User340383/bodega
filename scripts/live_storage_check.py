"""Live check of object storage (Cloudflare R2 in production, MinIO locally): FR-213/FR-215 path.

    S3_ENDPOINT_URL=... S3_BUCKET=... S3_ACCESS_KEY_ID=... S3_SECRET_ACCESS_KEY=... \
        python scripts/live_storage_check.py

Uses one synthetic, metadata-free JPEG (no owner data). Checks: upload, read-back,
a 300-second signed URL that serves the object, listing for orphan reconciliation, delete, that
the deleted object can no longer be fetched, and that a repeated delete succeeds (retried cleanup
jobs are idempotent). Without the four S3 settings it exits with code 2 and contacts nothing.
Run it only against a bucket you are authorized to test; it leaves nothing behind.
"""
import io
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
REQUIRED = ("S3_ENDPOINT_URL", "S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY")


def _fetch(url):
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""


def main():
    missing = [name for name in REQUIRED if not os.getenv(name)]
    if missing:
        print(f"Not configured ({', '.join(missing)} missing): nothing was contacted.")
        return 2
    from PIL import Image

    from app.privacy import sanitize_image
    from app.retention import IMAGE_PREFIX
    from app.storage import ObjectStorage

    storage = ObjectStorage(*(os.environ[name] for name in REQUIRED), os.getenv("S3_PUBLIC_ENDPOINT_URL"))
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(buffer, format="JPEG")
    data = sanitize_image(buffer.getvalue())

    results = {}
    key = storage.put_object(data, "image/jpeg")
    try:
        results["put"] = key.startswith(IMAGE_PREFIX)
        results["getForRetry"] = storage.get_object(key) == data
        status, body = _fetch(storage.get_signed_url(key))
        results["signedUrlServes"] = status == 200 and body == data
        later = datetime.now(timezone.utc) + timedelta(minutes=5)
        results["listedForReconciliation"] = key in storage.list_keys(IMAGE_PREFIX, later)
    finally:
        storage.delete_object(key)
    status, _ = _fetch(storage.get_signed_url(key))
    results["goneAfterDelete"] = status in (403, 404)
    storage.delete_object(key)  # Must not raise: queued deletions are retried.
    results["repeatDeleteSucceeds"] = True

    for name, passed in results.items():
        print(f"{'PASS' if passed else 'FAIL'}  {name}")
    ok = all(results.values())
    print("LIVE STORAGE CHECK:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
