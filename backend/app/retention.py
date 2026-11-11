"""RetentionService (M3 5.1, 5.4, 7.4; FR-211, FR-215, NFR-715).

run_sweep(now) = markStale(now - 120 s) + purgeExpired + orphan reconciliation + deletion of
queued images. The database work commits first; objects are removed from storage afterwards, and
a failed storage delete stays queued (image_deletion_jobs) for the next sweep, so a storage outage
can never roll back or block a database deletion.

Retention follows M3 7.4: stored input (sanitized photo, masked text), open submissions and
baseline snapshots are kept for 30 days (repository.RETENTION_DAYS); discarding or deleting a
submission queues its photo at once. The sweep runs at startup and every 6 hours.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

import psycopg

from . import repository
from .storage import StorageError

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 6 * 60 * 60
IMAGE_PREFIX = "images/"
# A photo is stored before its submission row exists (M3 5.4). Anything still unreferenced after
# this long was left behind by a failed request.
ORPHAN_GRACE = timedelta(hours=1)


def process_image_deletions(storage, limit=100) -> dict:
    deleted = failed = 0
    for key in repository.pending_image_deletions(limit):
        try:
            storage.delete_object(key)
        except StorageError:
            repository.finish_image_deletion(key, succeeded=False)
            failed += 1
        else:
            repository.finish_image_deletion(key, succeeded=True)
            deleted += 1
    return {"imagesDeleted": deleted, "imageDeletesFailed": failed}


def reconcile_orphans(storage, now=None) -> int:
    """Queue stored photos that no submission refers to, for example when the submission insert
    failed after the upload and the compensating delete failed too. Returns how many were queued."""
    now = now or datetime.now(timezone.utc)
    orphans = repository.unreferenced_image_keys(storage.list_keys(IMAGE_PREFIX, now - ORPHAN_GRACE))
    repository.enqueue_image_deletions(orphans)
    return len(orphans)


def run_sweep(storage, now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    result = {"markedStale": repository.mark_stale(now), **repository.purge_expired(now)}
    try:
        result["orphansQueued"] = reconcile_orphans(storage, now)
    except StorageError:
        result["orphansQueued"] = None  # Listing failed; the next sweep tries again.
        logger.warning("orphan reconciliation deferred", extra={"operation": "retention.orphans",
                                                                "errorCategory": "STORAGE_UNAVAILABLE"})
    result.update(process_image_deletions(storage))
    result["imagesPending"] = repository.queued_image_count()
    logger.info("retention sweep", extra={"operation": "retention.sweep", **result})
    return result


def safe_sweep(storage):
    """Never raises: a sweep failure is logged by category and retried on the next interval."""
    try:
        return run_sweep(storage)
    except (psycopg.Error, StorageError) as exc:
        logger.error("retention sweep failed", extra={"operation": "retention.sweep",
                                                      "errorCategory": type(exc).__name__})
        return None


async def sweep_forever(get_storage, interval=SWEEP_INTERVAL_SECONDS):
    while True:
        await asyncio.sleep(interval)
        await asyncio.to_thread(safe_sweep, get_storage())
