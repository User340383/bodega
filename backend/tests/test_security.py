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


def test_health_needs_no_access_code(client):
    assert client.get("/health").json() == {"status": "ok"}


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
