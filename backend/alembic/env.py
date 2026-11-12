"""Alembic environment: one transaction for the whole upgrade, in the configured schema."""
from alembic import context

from app.database import engine, schema
from app.models import Base

config = context.config


def run(connection):
    context.configure(connection=connection, target_metadata=Base.metadata,
                      version_table_schema=schema(), transaction_per_migration=False)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("Offline SQL generation is not supported; run against a database.")
connection = config.attributes.get("connection")
if connection is not None:
    run(connection)  # Called by app.database.migrate(), which already holds the advisory lock.
else:
    with engine().begin() as conn:
        run(conn)
