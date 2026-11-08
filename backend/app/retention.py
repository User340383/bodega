"""RetentionService (M3 5.1, 7.4; FR-211, FR-215, NFR-715).

run_sweep(now) = markStale(now - 120 s) + purgeExpired(now - 30 days) + deletion of queued
images. The database work commits first; objects are removed from storage afterwards, and a
failed storage delete stays queued (image_deletion_jobs) for the next sweep, so a storage
outage can never roll back or block a database deletion.
"""
import asyncio
import logging
from datetime import datetime, timezone

import psycopg

from . import repository
from .storage import StorageError

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 6 * 60 * 60


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


def run_sweep(storage, now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    result = {"markedStale": repository.mark_stale(now), **repository.purge_expired(now)}
    result.update(process_image_deletions(storage))
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
