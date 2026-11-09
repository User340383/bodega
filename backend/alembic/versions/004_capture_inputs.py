"""004_capture_inputs.sql

Revision ID: 004
Revises: 003
"""
from pathlib import Path

from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None
SQL = Path(__file__).resolve().parents[2] / "migrations" / "004_capture_inputs.sql"


def upgrade():
    # The raw psycopg connection runs the reviewed file exactly (DO $$ blocks, '%' in LIKE
    # patterns) inside Alembic's transaction; SQLAlchemy text() would treat ':' and '%' specially.
    op.get_bind().connection.driver_connection.execute(SQL.read_text(encoding="utf-8"))


def downgrade():
    raise NotImplementedError("Bodega migrations are forward-only; restore from a backup instead.")
