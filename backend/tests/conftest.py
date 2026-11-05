"""Fixtures for analysis/guidance tests. Each test gets a disposable schema on TEST_DATABASE_URL.

test_database.py keeps its own identical module-level db/client fixtures, which take
precedence there, so Gabriel's file and open PR #2 are not disturbed.
"""
import os
from datetime import date, timedelta
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql

from app.clock import get_clock
from app.database import migrate
from app.main import app

AS_OF = date(2026, 4, 30)


class FixedClock:
    def __init__(self, today):
        self._today = today

    def today(self):
        return self._today


@pytest.fixture
def db(monkeypatch):
    url = os.getenv('TEST_DATABASE_URL')
    if not url:
        pytest.skip('TEST_DATABASE_URL must point to a disposable PostgreSQL database')
    schema = 'test_' + uuid4().hex
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    monkeypatch.setenv('DATABASE_URL', url)
    monkeypatch.setenv('BODEGA_DB_SCHEMA', schema)
    monkeypatch.setenv('OWNER_ID', '00000000-0000-4000-8000-000000000001')
    monkeypatch.setenv('APP_ACCESS_CODE', 'test-code')
    migrate()
    try:
        yield
    finally:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


@pytest.fixture
def client(db):
    app.dependency_overrides[get_clock] = lambda: FixedClock(AS_OF)
    try:
        with TestClient(app, raise_server_exceptions=False) as test_client:
            test_client.headers['Authorization'] = 'Bearer test-code'
            yield test_client
    finally:
        app.dependency_overrides.clear()


def logged_dates(count, end=AS_OF, step=1):
    """`count` dates ending at `end`, oldest first."""
    return [end - timedelta(days=step * index) for index in reversed(range(count))]
