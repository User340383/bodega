"""Structured JSON logging to stdout (NFR-723, M3 9.5 and 7.4).

Every line has a UTC timestamp, level, component (logger) and message. Inside a request it also
has the requestId. The request middleware writes one line per request with the operation (route
template, never the raw query string), status, errorCategory and durationMs. Only allow-listed
`extra` fields are emitted, so request bodies, sales text, images, prompts, model output and the
access code can never reach a log line, even if a caller passes them by mistake.
"""
import contextvars
import json
import logging
import sys
import time
from datetime import datetime, timezone
from uuid import uuid4

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
request_started_var: contextvars.ContextVar[float | None] = contextvars.ContextVar("request_started", default=None)

_base_factory = logging.getLogRecordFactory()


def _record_with_request_id(*args, **kwargs):
    # Stamp the ID when the record is created: a handler may format it after the request ends.
    record = _base_factory(*args, **kwargs)
    record.requestId = request_id_var.get()
    started = request_started_var.get()
    record.requestElapsedMs = round((time.monotonic() - started) * 1000, 2) if started is not None else None
    return record


if not getattr(_base_factory, "_bodega", False):
    _record_with_request_id._bodega = True
    logging.setLogRecordFactory(_record_with_request_id)

ALLOWED_FIELDS = (
    "operation", "method", "status", "errorCategory", "errorCode", "durationMs", "latencyMs",
    "submissionId", "attempt", "markedStale", "openDeleted", "inputsCleared", "snapshotsDeleted",
    "imagesDeleted", "imageDeletesFailed", "orphansQueued", "imagesPending", "provider", "rule",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "component": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "requestId", None) or request_id_var.get()
        if request_id:
            entry["requestId"] = request_id
        for field in ALLOWED_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                entry[field] = value
        if record.exc_info:
            entry["errorCategory"] = entry.get("errorCategory") or record.exc_info[0].__name__
        if record.levelno >= logging.WARNING:
            # NFR-723: every error line carries operation, error category and a duration.
            entry.setdefault("operation", f"{record.module}.{record.funcName}")
            entry.setdefault("errorCategory", entry.get("errorCode") or record.levelname)
            if "durationMs" not in entry:
                elapsed = getattr(record, "requestElapsedMs", None)
                entry["durationMs"] = elapsed if elapsed is not None else 0.0
        return json.dumps(entry, default=str)


def configure_logging(level=logging.INFO):
    """Idempotent: installs one JSON stdout handler on the root logger."""
    root = logging.getLogger()
    if not any(getattr(handler, "_bodega_json", False) for handler in root.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        handler._bodega_json = True
        root.addHandler(handler)
    root.setLevel(level)
    # uvicorn's access log prints raw URLs (including query values); the middleware replaces it.
    logging.getLogger("uvicorn.access").disabled = True


class RequestLogMiddleware:
    """Pure ASGI middleware: request ID, X-Request-ID response header, one log line per request."""

    def __init__(self, app):
        self.app = app
        self.logger = logging.getLogger("bodega.request")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(uuid4())
        token = request_id_var.set(request_id)
        started = time.monotonic()
        started_token = request_started_var.set(started)
        status_holder = {"status": 500}

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                message.setdefault("headers", [])
                message["headers"] = list(message["headers"]) + [(b"x-request-id", request_id.encode())]
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            status = status_holder["status"]
            route = scope.get("route")
            operation = getattr(route, "path", None) or "unmatched"
            level = logging.ERROR if status >= 500 else logging.WARNING if status >= 400 else logging.INFO
            extra = {"operation": f"{scope.get('method', '')} {operation}".strip(), "status": status,
                     "durationMs": round((time.monotonic() - started) * 1000, 2)}
            if status >= 400:
                extra["errorCategory"] = f"HTTP_{status}"
            self.logger.log(level, "request completed", extra=extra)
            request_id_var.reset(token)
            request_started_var.reset(started_token)
