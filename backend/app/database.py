"""PostgreSQL connections and ordered, transactional SQL migrations."""
import os
import re
from pathlib import Path

import psycopg


def connect():
    schema = os.getenv("BODEGA_DB_SCHEMA", "public")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
        raise ValueError("Invalid database schema")
    return psycopg.connect(os.environ["DATABASE_URL"], options=f"-c search_path={schema}")


def migrate():
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(425004)")
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        for path in sorted((Path(__file__).resolve().parents[1] / "migrations").glob("[0-9]*.sql")):
            if not conn.execute("SELECT 1 FROM schema_migrations WHERE version=%s", (path.name,)).fetchone():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations(version) VALUES (%s)", (path.name,))


if __name__ == "__main__":
    migrate()
