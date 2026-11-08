"""Access code, rate limit, CORS origins and startup settings (NFR-718, M3 4.2 and 8.3). No database needed."""
import pytest
from fastapi.testclient import TestClient

from app import security
from app.main import app


class FakeTime:
    def __init__(self):
        self.value = 1000.0

    def __call__(self):
        return self.value


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("APP_ACCESS_CODE", "correct-code-123")
    app.state.access_limiter = security.FailedAttemptLimiter()
    # No `with`: the lifespan (migrations) does not run, and auth fails before any database access.
    yield TestClient(app, raise_server_exceptions=False)
    app.state.access_limiter = security.FailedAttemptLimiter()


class _FakeConnection:
    def __init__(self, fail=False):
        self.fail = fail

    def __enter__(self):
        if self.fail:
            import psycopg
            raise psycopg.OperationalError("down")
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *_):
        return None


def test_health_needs_no_access_code_and_checks_the_database(client, monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, "connect", lambda: _FakeConnection())
    assert client.get("/health").json() == {"status": "ok"}
    monkeypatch.setattr(main, "connect", lambda: _FakeConnection(fail=True))
    response = client.get("/health")
    assert response.status_code == 503 and response.json() == {"status": "unavailable"}


@pytest.mark.parametrize("header", [None, "Bearer wrong", "correct-code-123", "Bearer correct-code-1234", "bearer correct-code-123"])
def test_wrong_or_missing_code_is_401(client, header):
    headers = {"Authorization": header} if header else {}
    assert client.get("/api/v1/sales", headers=headers).status_code == 401


def test_eleventh_attempt_after_ten_failures_is_429_even_with_the_right_code(client):
    for _ in range(10):
        assert client.get("/api/v1/sales", headers={"Authorization": "Bearer wrong"}).status_code == 401
    response = client.get("/api/v1/sales", headers={"Authorization": "Bearer correct-code-123"})
    assert response.status_code == 429
    assert "15 minutes" in response.json()["detail"]


def test_spoofed_forwarded_for_cannot_bypass_the_limit(client, monkeypatch):
    """Regression: a new left-most X-Forwarded-For value on every attempt used to reset the count."""
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    for attempt in range(10):
        spoofed = {"Authorization": "Bearer wrong", "X-Forwarded-For": f"10.9.{attempt}.1, 203.0.113.7"}
        assert client.get("/api/v1/sales", headers=spoofed).status_code == 401
    rotated = {"Authorization": "Bearer correct-code-123", "X-Forwarded-For": "198.51.100.99, 203.0.113.7"}
    assert client.get("/api/v1/sales", headers=rotated).status_code == 429


def test_without_trusted_proxies_forwarded_for_is_ignored(client, monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
    for attempt in range(10):
        headers = {"Authorization": "Bearer wrong", "X-Forwarded-For": f"192.0.2.{attempt}"}
        assert client.get("/api/v1/sales", headers=headers).status_code == 401
    assert client.get("/api/v1/sales", headers={"Authorization": "Bearer correct-code-123",
                                                "X-Forwarded-For": "192.0.2.200"}).status_code == 429


def test_distinct_clients_behind_the_proxy_are_counted_separately(client, monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    for _ in range(10):
        assert client.get("/api/v1/sales", headers={"Authorization": "Bearer wrong",
                                                    "X-Forwarded-For": "203.0.113.7"}).status_code == 401
    other = {"Authorization": "Bearer correct-code-123", "X-Forwarded-For": "203.0.113.8"}
    assert client.get("/api/v1/sales", headers=other).status_code != 429


def test_container_does_not_trust_every_forwarded_header():
    """uvicorn with --forwarded-allow-ips='*' takes the left-most (spoofable) X-Forwarded-For entry."""
    from pathlib import Path
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(encoding="utf-8")
    command = next(line for line in dockerfile.splitlines() if line.startswith("CMD"))
    assert "forwarded-allow-ips" not in command and "--proxy-headers" not in command
    assert "--no-proxy-headers" in command


@pytest.mark.parametrize("value", ["-1", "abc", "6", "1.5"])
def test_invalid_proxy_hop_setting_refuses_startup(monkeypatch, value):
    for name in security.REQUIRED_SETTINGS:
        monkeypatch.setenv(name, "set")
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", value)
    with pytest.raises(RuntimeError, match="TRUSTED_PROXY_HOPS"):
        security.check_required_settings()


def test_limiter_forgets_failures_after_the_window():
    clock = FakeTime()
    limiter = security.FailedAttemptLimiter(max_failures=10, window_seconds=900, now=clock)
    for _ in range(10):
        limiter.record_failure("1.2.3.4")
    assert limiter.blocked("1.2.3.4")
    assert not limiter.blocked("5.6.7.8")
    clock.value += 900
    assert not limiter.blocked("1.2.3.4")


def test_missing_access_code_setting_is_503(monkeypatch):
    monkeypatch.delenv("APP_ACCESS_CODE", raising=False)
    response = TestClient(app).get("/api/v1/sales", headers={"Authorization": "Bearer anything"})
    assert response.status_code == 503


def test_startup_refuses_missing_required_settings(monkeypatch):
    for name in security.REQUIRED_SETTINGS:
        monkeypatch.setenv(name, "set")
    security.check_required_settings()
    monkeypatch.delenv("OWNER_ID")
    monkeypatch.setenv("APP_ACCESS_CODE", "  ")
    with pytest.raises(RuntimeError, match="APP_ACCESS_CODE, OWNER_ID"):
        security.check_required_settings()


@pytest.mark.parametrize(("value", "expected"), [
    ("", []),
    ("https://bodega-frontend.onrender.com/", ["https://bodega-frontend.onrender.com"]),
    ("bodega-frontend.onrender.com", ["https://bodega-frontend.onrender.com"]),
    ("http://localhost:5173, https://x.example", ["http://localhost:5173", "https://x.example"]),
])
def test_allowed_origins(monkeypatch, value, expected):
    monkeypatch.setenv("FRONTEND_URL", value)
    assert security.allowed_origins() == expected
