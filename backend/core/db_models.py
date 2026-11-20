"""SQLAlchemy schema for the future PostgreSQL multi-user store.

The current application continues to use ``core.store`` (SQLite). These models
define the isolated PostgreSQL foundation until a reviewed migration/cutover.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON, Uuid


LEGACY_OWNER_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("oidc_issuer", "oidc_subject", name="uq_users_oidc_identity"),
        CheckConstraint(
            "(is_legacy_owner AND oidc_issuer IS NULL AND oidc_subject IS NULL) OR "
            "(NOT is_legacy_owner AND oidc_issuer IS NOT NULL AND oidc_subject IS NOT NULL)",
            name="ck_users_identity_or_legacy",
        ),
        Index(
            "uq_users_single_legacy_owner",
            "is_legacy_owner",
            unique=True,
            postgresql_where=text("is_legacy_owner"),
            sqlite_where=text("is_legacy_owner = 1"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    oidc_issuer: Mapped[str | None] = mapped_column(String(512))
    oidc_subject: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(320))
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    display_name: Mapped[str | None] = mapped_column(String(200))
    is_legacy_owner: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=func.now())


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint("length(token_hash) = 32", name="ck_sessions_token_hash_sha256"),
        CheckConstraint("length(csrf_token_hash) = 32", name="ck_sessions_csrf_hash_sha256"),
        CheckConstraint("idle_expires_at <= absolute_expires_at", name="ck_sessions_expiry_order"),
        Index("ix_sessions_user_expiry", "user_id", "absolute_expires_at"),
        Index("ix_sessions_active_expiry", "idle_expires_at", postgresql_where=text("revoked_at IS NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False, unique=True)
    csrf_token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now())
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Upload(Base):
    __tablename__ = "uploads"
    __table_args__ = (
        UniqueConstraint("file_id", "owner_id", name="uq_uploads_file_owner"),
        CheckConstraint("byte_size >= 0", name="ck_uploads_byte_size_nonnegative"),
        CheckConstraint("storage_status IN ('available', 'missing')", name="ck_uploads_storage_status"),
        Index("ix_uploads_owner_created", "owner_id", "created_at"),
    )

    file_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    original_filename: Mapped[str | None] = mapped_column(String(255))
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    sha256: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    storage_status: Mapped[str] = mapped_column(String(16), nullable=False, default="available", server_default=text("'available'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Plan(Base):
    __tablename__ = "plans"
    __table_args__ = (
        UniqueConstraint("plan_id", "owner_id", name="uq_plans_id_owner"),
        ForeignKeyConstraint(
            ["file_id", "owner_id"], ["uploads.file_id", "uploads.owner_id"],
            name="fk_plans_upload_owner", ondelete="RESTRICT",
        ),
        CheckConstraint("status IN ('created', 'approved', 'executing', 'completed', 'failed')", name="ck_plans_status"),
        Index("ix_plans_owner_created", "owner_id", "created_at"),
        Index("ix_plans_owner_status_updated", "owner_id", "status", "updated_at"),
    )

    plan_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    file_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    plan_json: Mapped[dict] = mapped_column(JSON_DOCUMENT, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="created", server_default=text("'created'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=func.now())
    result_json: Mapped[dict | None] = mapped_column(JSON_DOCUMENT)
    error: Mapped[str | None] = mapped_column(Text)


class ApprovalEvent(Base):
    __tablename__ = "approval_events"
    __table_args__ = (
        UniqueConstraint("plan_id", name="uq_approval_events_plan"),
        ForeignKeyConstraint(
            ["plan_id", "actor_user_id"], ["plans.plan_id", "plans.owner_id"],
            name="fk_approval_actor_plan_owner", ondelete="RESTRICT",
        ),
        Index("ix_approval_events_actor_time", "actor_user_id", "approved_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now())


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (
        UniqueConstraint("report_id", "owner_id", name="uq_reports_id_owner"),
        ForeignKeyConstraint(
            ["plan_id", "owner_id"], ["plans.plan_id", "plans.owner_id"],
            name="fk_reports_plan_owner", ondelete="RESTRICT",
        ),
        CheckConstraint("storage_status IN ('available', 'missing')", name="ck_reports_storage_status"),
        CheckConstraint("byte_size >= 0", name="ck_reports_byte_size_nonnegative"),
        CheckConstraint(
            "NOT verified OR (integrity_passed AND completeness_passed)",
            name="ck_reports_verified_checks_passed",
        ),
        Index("ix_reports_owner_created", "owner_id", "created_at"),
        Index("ix_reports_plan", "plan_id"),
    )

    report_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    plan_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    integrity_passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    completeness_passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    storage_status: Mapped[str] = mapped_column(String(16), nullable=False, default="available", server_default=text("'available'"))
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    sha256: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now())
