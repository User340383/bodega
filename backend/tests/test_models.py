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
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == head() == "008"
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
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "008"
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
    revision = Scripts.from_config(database.alembic_config()).get_revision("008")
    with pytest.raises(NotImplementedError, match="forward-only"):
        revision.module.downgrade()


def test_migrating_repeatedly_does_not_grow_sys_path(db):
    """Regression: revision files once inserted into sys.path on every load, slowing every import."""
    import sys
    before = len(sys.path)
    for _ in range(5):
        database.migrate()
    assert len(sys.path) == before


# ---- 007/008: a database that ran the withdrawn draft of 007 (D-27, D-29) -----------------------
DRAFT_007 = Path(__file__).resolve().parent / "fixtures" / "unreleased_007_draft.sql"
OPEN_DRAFT = {"version": 1, "message": None, "lines": [{
    "lineId": 1, "itemName": "Bread", "quantity": "3", "unitPrice": "1.50",
    "bands": {"itemName": "HIGH", "quantity": "HIGH", "unitPrice": "MEDIUM"}}]}


def _owner_rows():
    with database.connect() as conn:
        submissions = conn.execute("""SELECT id, owner_id, sale_date, status, input_type, image_key, masked_text,
            draft_payload::text, created_at, confirmed_at FROM submissions ORDER BY id""").fetchall()
        records = conn.execute("""SELECT id, submission_id, line_no, item_name, item_key, quantity, unit_price, source
            FROM sales_records ORDER BY id""").fetchall()
    return submissions, records


@pytest.mark.filterwarnings("ignore:Can.t validate argument .dialect_options.")
def test_a_database_that_ran_the_withdrawn_007_draft_is_upgraded_and_keeps_its_data(db):
    from datetime import date, timedelta

    from app import repository
    day = date.today() - timedelta(days=1)
    with database.connect() as conn:  # The state of a local database stamped "007" by the draft.
        conn.execute(DRAFT_007.read_text(encoding="utf-8"))
        conn.execute("UPDATE alembic_version SET version_num = '007'")
    open_id = repository.create_submission(day, input_type="TEXT", masked_text="Bread 3 1.50")
    repository.update_status(open_id, "CLASSIFYING")
    repository.update_status(open_id, "NEEDS_VERIFICATION", draft_payload=OPEN_DRAFT)
    failed_id = repository.create_submission(day, input_type="TEXT", masked_text="Milk 2")
    repository.update_status(failed_id, "CLASSIFYING")
    repository.update_status(failed_id, "AI_UNAVAILABLE")
    confirmed_id = repository.create_submission(day, input_type="TEXT", masked_text="Rice 1")
    repository.update_status(confirmed_id, "CLASSIFYING")
    repository.update_status(confirmed_id, "AI_UNAVAILABLE")
    repository.replace_baselines(day, [{  # A Milestone 3 snapshot: must survive.
        "itemName": "Bread", "itemKey": "bread", "windowDays": 7, "loggedDays": 7, "averageDailyQuantity": 3,
        "priorAverageDailyQuantity": None, "trend": "INSUFFICIENT_DATA", "suggestedRestockQty": 21}])
    with database.connect() as conn:
        conn.execute("UPDATE submissions SET extraction_failed_at = now(), retry_count = 2 WHERE id = %s", (failed_id,))
        conn.execute("""INSERT INTO sales_records(id, submission_id, line_no, item_name, quantity, unit_price, source)
            VALUES (%s, %s, 1, 'Rice', 1, 0.80, 'MANUAL')""", (uuid4(), confirmed_id))
        conn.execute("UPDATE submissions SET status = 'CONFIRMED' WHERE id = %s", (confirmed_id,))
        conn.execute("""INSERT INTO baseline_estimates(id, owner_id, item_name, item_key, as_of_date, method, logged_days,
                total_quantity, recorded_dates, baseline_estimate, first_sale_date, last_sale_date)
            VALUES (%s, %s, 'Milk', 'milk', %s, 'SRS_RECORDED_DATES', 2, 6, 2, 3, %s, %s)""",
                     (uuid4(), repository.owner_id(), day, day - timedelta(days=1), day))
    before = _owner_rows()
    assert (len(before[0]), len(before[1])) == (3, 1)

    database.migrate()
    database.migrate()  # A second start changes nothing.

    assert _owner_rows() == before  # Every submission, draft and sales record is unchanged.
    with database.engine().connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "008"
        leftovers = conn.execute(text(
            "SELECT table_name || '.' || column_name FROM information_schema.columns WHERE table_schema = current_schema() "
            "AND column_name IN ('extraction_failed_at', 'retry_count', 'method', 'total_quantity', 'recorded_dates', "
            "'baseline_estimate', 'first_sale_date', 'last_sale_date')")).scalars().all()
        assert leftovers == []
        assert conn.execute(text("SELECT item_key, trend, suggested_restock_qty FROM baseline_estimates")).all() == [
            ("bread", "INSUFFICIENT_DATA", 21)]
        context = MigrationContext.configure(conn, opts={
            "include_object": lambda obj, name, kind, reflected, compare_to: name not in ("alembic_version", "schema_migrations"),
            "compare_type": True})
        assert compare_metadata(context, Base.metadata) == []  # Same schema as a fresh database.
    with pytest.raises(psycopg.errors.CheckViolation), database.connect() as conn:  # M3 Diagram 4 again.
        conn.execute("UPDATE submissions SET status = 'CLASSIFYING' WHERE id = %s", (failed_id,))
    # The application works on the upgraded database: the open draft still confirms.
    assert repository.get_submission(open_id)["lines"][0]["itemName"] == "Bread"


def test_007_and_008_change_nothing_on_a_database_without_the_draft(db):
    def shape():
        with database.engine().connect() as conn:
            columns = conn.execute(text(
                "SELECT table_name, column_name, is_nullable, data_type FROM information_schema.columns "
                "WHERE table_schema = current_schema() ORDER BY 1, 2")).all()
            function = conn.execute(text(
                "SELECT pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = current_schema() AND p.proname = 'enforce_submission_transition'")).scalar()
        return columns, function
    after_head = shape()
    with database.connect() as conn:  # Replaying 008 by hand is a no-op.
        conn.execute((MIGRATIONS / "008_remove_unreleased_007.sql").read_text(encoding="utf-8"))
    assert shape() == after_head
    assert "AI_UNAVAILABLE' AND NEW.status = 'CLASSIFYING'" not in after_head[1]
