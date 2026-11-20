"""Create the ownership-ready PostgreSQL schema and reserved legacy owner.

Revision ID: 0001_multi_user_foundation
Revises:
Create Date: 2026-11-20
"""
from datetime import datetime, timezone
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_multi_user_foundation"
down_revision = None
branch_labels = None
depends_on = None

LEGACY_OWNER_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")


def _json_type():
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("oidc_issuer", sa.String(length=512), nullable=True),
        sa.Column("oidc_subject", sa.String(length=255), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("email_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=True),
        sa.Column("is_legacy_owner", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "(is_legacy_owner AND oidc_issuer IS NULL AND oidc_subject IS NULL) OR "
            "(NOT is_legacy_owner AND oidc_issuer IS NOT NULL AND oidc_subject IS NOT NULL)",
            name="ck_users_identity_or_legacy",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("oidc_issuer", "oidc_subject", name="uq_users_oidc_identity"),
    )
    op.create_index(
        "uq_users_single_legacy_owner", "users", ["is_legacy_owner"], unique=True,
        postgresql_where=sa.text("is_legacy_owner IS TRUE"),
        sqlite_where=sa.text("is_legacy_owner = 1"),
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("csrf_token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("length(token_hash) = 32", name="ck_sessions_token_hash_sha256"),
        sa.CheckConstraint("length(csrf_token_hash) = 32", name="ck_sessions_csrf_hash_sha256"),
        sa.CheckConstraint("idle_expires_at <= absolute_expires_at", name="ck_sessions_expiry_order"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_sessions_user_id_users", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_sessions"),
        sa.UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
    )
    op.create_index("ix_sessions_user_expiry", "sessions", ["user_id", "absolute_expires_at"])
    op.create_index(
        "ix_sessions_active_expiry", "sessions", ["idle_expires_at"],
        postgresql_where=sa.text("revoked_at IS NULL"),
        sqlite_where=sa.text("revoked_at IS NULL"),
    )

    op.create_table(
        "uploads",
        sa.Column("file_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("byte_size", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("sha256", sa.LargeBinary(length=32), nullable=True),
        sa.Column("storage_status", sa.String(length=16), server_default=sa.text("'available'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("byte_size >= 0", name="ck_uploads_byte_size_nonnegative"),
        sa.CheckConstraint("storage_status IN ('available', 'missing')", name="ck_uploads_storage_status"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_uploads_owner_id_users", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("file_id", name="pk_uploads"),
        sa.UniqueConstraint("file_id", "owner_id", name="uq_uploads_file_owner"),
        sa.UniqueConstraint("storage_key", name="uq_uploads_storage_key"),
    )
    op.create_index("ix_uploads_owner_created", "uploads", ["owner_id", "created_at"])

    op.create_table(
        "plans",
        sa.Column("plan_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("file_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("plan_json", _json_type(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'created'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("result_json", _json_type(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint("status IN ('created', 'approved', 'executing', 'completed', 'failed')", name="ck_plans_status"),
        sa.ForeignKeyConstraint(["file_id", "owner_id"], ["uploads.file_id", "uploads.owner_id"], name="fk_plans_upload_owner", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_plans_owner_id_users", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("plan_id", name="pk_plans"),
        sa.UniqueConstraint("plan_id", "owner_id", name="uq_plans_id_owner"),
    )
    op.create_index("ix_plans_owner_created", "plans", ["owner_id", "created_at"])
    op.create_index("ix_plans_owner_status_updated", "plans", ["owner_id", "status", "updated_at"])

    op.create_table(
        "approval_events",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("plan_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], name="fk_approval_events_actor_user_id_users", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["plan_id", "actor_user_id"], ["plans.plan_id", "plans.owner_id"], name="fk_approval_actor_plan_owner", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_approval_events"),
        sa.UniqueConstraint("plan_id", name="uq_approval_events_plan"),
    )
    op.create_index("ix_approval_events_actor_time", "approval_events", ["actor_user_id", "approved_at"])

    op.create_table(
        "reports",
        sa.Column("report_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("plan_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("integrity_passed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("completeness_passed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("storage_status", sa.String(length=16), server_default=sa.text("'available'"), nullable=False),
        sa.Column("byte_size", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("sha256", sa.LargeBinary(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("storage_status IN ('available', 'missing')", name="ck_reports_storage_status"),
        sa.CheckConstraint("byte_size >= 0", name="ck_reports_byte_size_nonnegative"),
        sa.CheckConstraint("NOT verified OR (integrity_passed AND completeness_passed)", name="ck_reports_verified_checks_passed"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_reports_owner_id_users", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["plan_id", "owner_id"], ["plans.plan_id", "plans.owner_id"], name="fk_reports_plan_owner", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("report_id", name="pk_reports"),
        sa.UniqueConstraint("report_id", "owner_id", name="uq_reports_id_owner"),
        sa.UniqueConstraint("storage_key", name="uq_reports_storage_key"),
    )
    op.create_index("ix_reports_owner_created", "reports", ["owner_id", "created_at"])
    op.create_index("ix_reports_plan", "reports", ["plan_id"])

    # A reserved principal with no OIDC identity. It cannot log in and is used
    # only by the explicit legacy import/transfer process.
    users = sa.table(
        "users",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("is_legacy_owner", sa.Boolean()),
        sa.column("email_verified", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    now = datetime.now(timezone.utc)
    op.bulk_insert(users, [{
        "id": LEGACY_OWNER_ID,
        "is_legacy_owner": True,
        "email_verified": False,
        "created_at": now,
        "updated_at": now,
    }])


def downgrade():
    bind = op.get_bind()
    for table in ("sessions", "uploads", "plans", "approval_events", "reports"):
        if bind.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one():
            raise RuntimeError(
                "Refusing to drop the multi-user schema while application data exists; "
                "take a verified backup and use an explicit export plan."
            )

    bind.execute(sa.text("DELETE FROM users WHERE id = :legacy_id"), {"legacy_id": str(LEGACY_OWNER_ID)})
    op.drop_index("ix_reports_plan", table_name="reports")
    op.drop_index("ix_reports_owner_created", table_name="reports")
    op.drop_table("reports")
    op.drop_index("ix_approval_events_actor_time", table_name="approval_events")
    op.drop_table("approval_events")
    op.drop_index("ix_plans_owner_status_updated", table_name="plans")
    op.drop_index("ix_plans_owner_created", table_name="plans")
    op.drop_table("plans")
    op.drop_index("ix_uploads_owner_created", table_name="uploads")
    op.drop_table("uploads")
    op.drop_index("ix_sessions_active_expiry", table_name="sessions")
    op.drop_index("ix_sessions_user_expiry", table_name="sessions")
    op.drop_table("sessions")
    op.drop_index("uq_users_single_legacy_owner", table_name="users")
    op.drop_table("users")
