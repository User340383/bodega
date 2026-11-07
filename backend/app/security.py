"""Owner access code (NFR-718), CORS origins and required startup configuration (M3 4.2, 8.3)."""
import hmac
import os
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

REQUIRED_SETTINGS = ("DATABASE_URL", "APP_ACCESS_CODE", "OWNER_ID")
MAX_FAILURES = 10
FAILURE_WINDOW_SECONDS = 15 * 60


def check_required_settings():
    """The backend refuses to start when a required variable is missing (M3 8.3)."""
    missing = [name for name in REQUIRED_SETTINGS if not os.getenv(name, "").strip()]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")


def allowed_origins():
    """FRONTEND_URL is the only cross-origin caller (comma-separated values are allowed).

    Render's service references give a bare host name, so https:// is assumed when no scheme is set.
    """
    origins = []
    for value in os.getenv("FRONTEND_URL", "").split(","):
        value = value.strip().rstrip("/")
        if value:
            origins.append(value if "://" in value else f"https://{value}")
    return origins


class FailedAttemptLimiter:
    """In-memory count of wrong access codes per client IP over a sliding 15-minute window."""

    def __init__(self, max_failures=MAX_FAILURES, window_seconds=FAILURE_WINDOW_SECONDS, now=time.monotonic):
        self._max = max_failures
        self._window = window_seconds
        self._now = now
        self._failures = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key):
        attempts = self._failures[key]
        cutoff = self._now() - self._window
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        if not attempts:
            del self._failures[key]
        return attempts

    def blocked(self, key):
        with self._lock:
            return len(self._prune(key)) >= self._max

    def record_failure(self, key):
        with self._lock:
            self._prune(key)
            self._failures[key].append(self._now())


def require_owner_access(request: Request):
    """FastAPI dependency: `Authorization: Bearer <APP_ACCESS_CODE>`; 401 when wrong, 429 after 10 failures."""
    expected_code = os.getenv("APP_ACCESS_CODE")
    if not expected_code:
        raise HTTPException(status_code=503, detail="API access code is not configured.")

    limiter = getattr(request.app.state, "access_limiter", None)
    if limiter is None:
        limiter = request.app.state.access_limiter = FailedAttemptLimiter()
    client = request.client.host if request.client else "unknown"
    if limiter.blocked(client):
        raise HTTPException(status_code=429, detail="Too many wrong access codes. Wait 15 minutes and try again.")

    supplied = request.headers.get("authorization", "")
    if not hmac.compare_digest(supplied.encode(), f"Bearer {expected_code}".encode()):
        limiter.record_failure(client)
        raise HTTPException(status_code=401, detail="Unauthorized")
