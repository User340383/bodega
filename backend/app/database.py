"""PostgreSQL connections and ordered, transactional SQL migrations."""
import os
import re
from pathlib import Path

import psycopg


def connect():
    schema = os.getenv("BODEGA_DB_SCHEMA", "public")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
        raise ValueError("Invalid database schema")
    return psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=5,
                          options=f"-c search_path={schema}")


def migrate():
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(425004)")
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        paths = sorted((Path(__file__).resolve().parents[1] / "migrations").glob("[0-9]*.sql"))
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        if applied - {path.name for path in paths}:
            # An older checkout must not silently run against a newer schema.
            # Never roll back or delete the newer database's migration ledger.
            raise RuntimeError("Database contains migrations unavailable in this checkout; use the matching application version")
        for path in paths:
            if not conn.execute("SELECT 1 FROM schema_migrations WHERE version=%s", (path.name,)).fetchone():
                conn.execute(path.read_text(encoding="utf-8"))
                if path.name == '004_baseline_v1.sql':
                    # PostgreSQL 16 lower() is not Unicode casefold(). Backfill in
                    # Python without changing any stored sales values or IDs.
                    conn.execute('SET CONSTRAINTS ALL IMMEDIATE')
                    conn.execute('ALTER TABLE sales_records DISABLE TRIGGER immutable_sales')
                    for table in ('sales_records', 'baseline_estimates'):
                        for row_id, name in conn.execute(f'SELECT id,item_name FROM {table}').fetchall():
                            conn.execute(f'UPDATE {table} SET item_key=%s WHERE id=%s', (' '.join(name.split()).casefold(), row_id))
                    conn.execute('ALTER TABLE sales_records ENABLE TRIGGER immutable_sales')
                    conn.execute('SET CONSTRAINTS ALL DEFERRED')
                conn.execute("INSERT INTO schema_migrations(version) VALUES (%s)", (path.name,))
        if os.getenv('OWNER_ID'):
            conn.execute('INSERT INTO owners(id,display_name) VALUES (%s,%s) ON CONFLICT DO NOTHING',
                         (os.environ['OWNER_ID'],os.getenv('OWNER_DISPLAY_NAME','Owner')))


if __name__ == "__main__":
    migrate()
