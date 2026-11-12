"""Database access through SQLAlchemy, with Alembic migrations (M3 5 "app/db (SQLAlchemy models,
RecordRepository, Alembic migrations)", M3 8.5 "alembic upgrade head").

- engine(): one SQLAlchemy engine per database URL and schema (psycopg 3 driver).
- connect(): a transaction. It commits on success and rolls back on any exception, and yields
  the driver connection, so RecordRepository keeps its reviewed SQL unchanged.
- migrate(): `alembic upgrade head` under a PostgreSQL advisory lock, so concurrent starts
  serialize. Databases created before Alembic (tracked by the old `schema_migrations` ledger)
  are stamped at the matching revision first, so no migration ever runs twice.
"""
import os
import re
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

BACKEND = Path(__file__).resolve().parents[1]
# Legacy ledger file name -> Alembic revision that applies the same SQL file.
LEGACY_REVISIONS = {
    "001_data_state.sql": "001", "002_nonblank_labels.sql": "002",
    "003_remove_label_length_limits.sql": "003", "004_capture_inputs.sql": "004",
    "005_final_data_design.sql": "005", "006_sales_records_final_shape.sql": "006",
}
_engines = {}


def schema():
    name = os.getenv("BODEGA_DB_SCHEMA", "public")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
        raise ValueError("Invalid database schema")
    return name


def database_url():
    """DATABASE_URL in SQLAlchemy form. Render and Compose give postgres(ql):// URLs."""
    url = os.environ["DATABASE_URL"]
    return re.sub(r"^postgres(?:ql)?(?:\+\w+)?://", "postgresql+psycopg://", url)


MAX_ENGINES = 4  # Production uses one; tests switch schemas. Bounded so the cache cannot grow.


def engine():
    key = (database_url(), schema())
    if key in _engines:
        _engines[key] = _engines.pop(key)  # Most recently used last.
        return _engines[key]
    while len(_engines) >= MAX_ENGINES:
        _engines.pop(next(iter(_engines))).dispose()
    # NullPool: one short transaction per request, as before; nothing idles open between calls.
    _engines[key] = create_engine(key[0], poolclass=NullPool,
                                  connect_args={"options": f"-c search_path={key[1]}", "connect_timeout": 10})
    return _engines[key]


@contextmanager
def connect():
    raw = engine().raw_connection()
    conn = raw.driver_connection
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        raw.close()


def alembic_config(connection=None):
    from alembic.config import Config
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.attributes["connection"] = connection
    return config


def _bridge_legacy_ledger(connection):
    """Stamp a pre-Alembic database at the revision its `schema_migrations` ledger reached."""
    has = lambda table: connection.execute(  # noqa: E731
        text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"{schema()}.{table}"}).scalar()
    if has("alembic_version") or not has("schema_migrations"):
        return None
    applied = [row[0] for row in connection.execute(text("SELECT version FROM schema_migrations ORDER BY version"))]
    unknown = [name for name in applied if name not in LEGACY_REVISIONS]
    if unknown:
        raise RuntimeError(
            "This database was migrated by a different branch (" + ", ".join(unknown) + "). "
            "Nothing was changed. Use a fresh database for this version of Bodega.")
    expected = list(LEGACY_REVISIONS)[:len(applied)]
    if applied != expected:
        raise RuntimeError("The schema_migrations ledger is out of order; nothing was changed.")
    return LEGACY_REVISIONS[applied[-1]] if applied else None


def migrate():
    from alembic import command
    with engine().begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(425004)"))
        stamp = _bridge_legacy_ledger(connection)
        config = alembic_config(connection)
        if stamp:
            command.stamp(config, stamp)
        command.upgrade(config, "head")
    seed_owner()


def seed_owner():
    """M3 7.1: OWNERS holds the single owner, seeded from OWNER_ID and OWNER_DISPLAY_NAME."""
    if not os.getenv("OWNER_ID"):
        return
    with connect() as conn:
        conn.execute("""INSERT INTO owners(id, display_name) VALUES (%s, %s)
            ON CONFLICT (id) DO UPDATE SET display_name = EXCLUDED.display_name""",
                     (os.environ["OWNER_ID"], (os.getenv("OWNER_DISPLAY_NAME") or "Owner")[:100]))


if __name__ == "__main__":
    migrate()
