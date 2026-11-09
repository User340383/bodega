"""SQLAlchemy models and Alembic migrations agree, and existing databases upgrade safely."""
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from psycopg import sql
from sqlalchemy import text

from app import database
from app.models import Base

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def head():
    return ScriptDirectory.from_config(database.alembic_config()).get_current_head()


# Alembic cannot reflect the expression index baselines_owner_item_time and warns while skipping it.
@pytest.mark.filterwarnings("ignore:Can.t validate argument .dialect_options.")
def test_alembic_upgrade_reaches_head_and_models_match_the_schema(db):
    with database.engine().connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == head() == "006"
        context = MigrationContext.configure(conn, opts={
            "include_object": lambda obj, name, kind, reflected, compare_to: name not in ("alembic_version", "schema_migrations"),
            "compare_type": True})
        differences = compare_metadata(context, Base.metadata)
    # Expression indexes cannot be compared by Alembic and are skipped; everything else must match.
    assert differences == [], differences


def test_migrate_is_idempotent(db):
    database.migrate()
    database.migrate()
    with database.engine().connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM alembic_version")).scalar() == 1


@pytest.fixture
def blank_schema(db, monkeypatch):
    url, name = os.environ["TEST_DATABASE_URL"], "test_legacy_" + uuid4().hex
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
    monkeypatch.setenv("BODEGA_DB_SCHEMA", name)
    yield name
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


def legacy_ledger(files):
    """A database migrated by the pre-Alembic runner, which recorded files in schema_migrations."""
    with database.connect() as conn:
        conn.execute("CREATE TABLE schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        for name in files:
            conn.execute((MIGRATIONS / name).read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations(version) VALUES (%s)", (name,))


def test_a_pre_alembic_database_is_stamped_then_upgraded_once(blank_schema):
    legacy_ledger(["001_data_state.sql", "002_nonblank_labels.sql", "003_remove_label_length_limits.sql",
                   "004_capture_inputs.sql"])  # The state of main before this branch.
    database.migrate()
    with database.engine().connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "006"
        assert conn.execute(text("SELECT count(*) FROM schema_migrations")).scalar() == 4  # Ledger kept.
        columns = {row[0] for row in conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='sales_records'"))}
    assert "line_no" in columns and "sale_date" not in columns


def test_a_database_from_another_branch_is_refused_unchanged(blank_schema):
    legacy_ledger(["001_data_state.sql", "002_nonblank_labels.sql", "003_remove_label_length_limits.sql"])
    with database.connect() as conn:
        conn.execute("INSERT INTO schema_migrations(version) VALUES ('004_baseline_v1.sql')")  # Gabriel's old branch.
    with pytest.raises(RuntimeError, match="different branch"):
        database.migrate()
    with database.engine().connect() as conn:
        assert conn.execute(text("SELECT to_regclass(current_schema() || '.alembic_version')")).scalar() is None


def test_downgrade_is_refused():
    from alembic.script import ScriptDirectory as Scripts
    revision = Scripts.from_config(database.alembic_config()).get_revision("006")
    with pytest.raises(NotImplementedError, match="forward-only"):
        revision.module.downgrade()


def test_migrating_repeatedly_does_not_grow_sys_path(db):
    """Regression: revision files once inserted into sys.path on every load, slowing every import."""
    import sys
    before = len(sys.path)
    for _ in range(5):
        database.migrate()
    assert len(sys.path) == before
