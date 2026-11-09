"""SQLAlchemy models of the Bodega schema (M3 5.1 / 7.1-7.2, docs/DATA_DICTIONARY.md).

These declare the tables that Alembic migrations 001-006 create. tests/test_models.py compares
them with a freshly migrated database (Alembic compare_metadata), so model and schema cannot
drift apart. Business rules that live in PostgreSQL (CHECK constraints, the Diagram 4 lifecycle
trigger, deferred consistency triggers) stay in the migrations, where they are enforced for every
writer.
"""
from sqlalchemy import (Column, Computed, Date, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint,
                        text)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def _now():
    return text("now()")


class Owner(Base):
    __tablename__ = "owners"
    id = Column(UUID(as_uuid=True), primary_key=True)
    display_name = Column(String(100), nullable=False, server_default=text("'Owner'::character varying"))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=_now())


class Submission(Base):
    __tablename__ = "submissions"
    id = Column(UUID(as_uuid=True), primary_key=True)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("owners.id", ondelete="CASCADE"), nullable=False)
    image_key = Column(Text)
    sale_date = Column(Date, nullable=False)
    status = Column(Text, nullable=False, server_default=text("'UPLOADED'::text"))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=_now())
    draft_payload = Column(JSONB)
    input_type = Column(Text, nullable=False, server_default=text("'TEXT'::text"))
    masked_text = Column(Text)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=_now())
    confirmed_at = Column(TIMESTAMP(timezone=True))
    __table_args__ = (
        UniqueConstraint("id", "sale_date", name="submissions_id_sale_date_key"),
        Index("submissions_owner_status_date", "owner_id", "status", "sale_date"),
        Index("submissions_stale", "status", "updated_at"),
        Index("submissions_retention", "created_at"),
    )


class SalesRecord(Base):
    __tablename__ = "sales_records"
    id = Column(UUID(as_uuid=True), primary_key=True)
    submission_id = Column(UUID(as_uuid=True), ForeignKey("submissions.id", ondelete="CASCADE",
                                                          name="sales_records_submission_fk"), nullable=False)
    line_no = Column(Integer, nullable=False)
    item_name = Column(Text, nullable=False)
    quantity = Column(Numeric, nullable=False)
    unit_price = Column(Numeric, nullable=False)
    source = Column(Text, nullable=False)
    item_key = Column(Text, Computed("lower(btrim(item_name))", persisted=True))
    __table_args__ = (
        UniqueConstraint("submission_id", "line_no", name="sales_records_submission_id_line_number_key"),
        Index("sales_records_item_key", "item_key"),
        Index("sales_records_submission", "submission_id"),
    )


class BaselineEstimate(Base):
    __tablename__ = "baseline_estimates"
    id = Column(UUID(as_uuid=True), primary_key=True)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("owners.id", ondelete="CASCADE"), nullable=False)
    item_name = Column(Text, nullable=False)
    window_days = Column(Integer, nullable=False)
    logged_days = Column(Integer, nullable=False)
    average_daily_quantity = Column(Numeric, nullable=False)
    trend = Column(Text, nullable=False)
    suggested_restock_qty = Column(Integer, nullable=False)
    computed_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=_now())
    as_of_date = Column(Date)
    item_key = Column(Text)
    prior_average_daily_quantity = Column(Numeric)
    cover_days = Column(Integer, nullable=False, server_default=text("7"))
    __table_args__ = (
        Index("baselines_owner_date_item", "owner_id", "as_of_date", "item_key", unique=True),
        Index("baselines_as_of", "as_of_date"),
        # Expression index from 001: (owner_id, lower(btrim(item_name)), computed_at DESC).
        Index("baselines_owner_item_time", "owner_id", text("lower(btrim(item_name))"), text("computed_at DESC")),
    )


class ImageDeletionJob(Base):
    __tablename__ = "image_deletion_jobs"
    image_key = Column(Text, primary_key=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=_now())
    attempt_count = Column(Integer, nullable=False, server_default=text("0"))
    last_attempt_at = Column(TIMESTAMP(timezone=True))
