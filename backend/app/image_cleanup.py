"""Durable post-commit image cleanup. No object keys or provider errors are logged."""
import logging

from .database import connect
from .storage import StorageError


def enqueue(conn, bucket_name, image_key):
    """Use the caller's transaction, never a separately committed connection."""
    if not bucket_name:
        raise StorageError('Image bucket is not configured; deletion was not committed')
    conn.execute('''INSERT INTO image_deletion_jobs(bucket_name,image_key)
        VALUES (%s,%s) ON CONFLICT DO NOTHING''', (bucket_name, image_key))


def enqueue_expired(storage, now):
    """Recover unlinked uploads from their existing durable retention metadata.

    A PUT can succeed before the submission exists, even if its response is lost.
    Discovery never deletes objects; the normal worker protects live references.
    """
    if storage is None:
        return
    try:
        with connect() as conn:
            for key in storage.expired_keys(now):
                enqueue(conn, storage.bucket, key)
    except StorageError:
        # Failed discovery must not prevent retries of already committed jobs.
        logging.getLogger(__name__).warning('Expired image discovery deferred; retry on next purge')


def process_pending(storage, *, image_key=None):
    """Attempt each selected job once; retry failures on startup/next daily purge.

    Bucket scoping prevents reconfiguration from directing old jobs at another
    bucket. Per-job locks serialize workers, while SKIP LOCKED permits progress.
    A crash after an S3 success leaves a durable job; S3 DELETE is idempotent.
    """
    if storage is None:
        return 0
    bucket = storage.bucket
    with connect() as conn:
        candidates = conn.execute('''SELECT image_key FROM image_deletion_jobs
            WHERE bucket_name=%s AND (%s::text IS NULL OR image_key=%s)
            ORDER BY created_at,image_key''', (bucket, image_key, image_key)).fetchall()
    completed = 0
    for (key,) in candidates:
        with connect() as conn:
            job = conn.execute('''SELECT image_key FROM image_deletion_jobs j
                WHERE bucket_name=%s AND image_key=%s
                AND NOT EXISTS (SELECT 1 FROM submissions s WHERE s.image_key=j.image_key)
                FOR UPDATE SKIP LOCKED''', (bucket, key)).fetchone()
            if job is None:
                continue
            conn.execute('''UPDATE image_deletion_jobs SET attempt_count=attempt_count+1,
                last_attempt_at=clock_timestamp() WHERE bucket_name=%s AND image_key=%s''', (bucket, key))
            try:
                storage.delete(key)
            except StorageError:
                logging.getLogger(__name__).warning('Image deletion deferred; durable job retained')
            else:
                conn.execute('DELETE FROM image_deletion_jobs WHERE bucket_name=%s AND image_key=%s', (bucket, key))
                completed += 1
    return completed
