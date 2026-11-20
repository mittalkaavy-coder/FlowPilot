"""Owner-scoped PostgreSQL repository; not wired into the active SQLite app yet."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker

from core.db_models import ApprovalEvent, Plan, Report, Upload


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "dict"):
        return value.dict()
    return value


class PostgresStore:
    """Small transaction-oriented repository preserving the existing lifecycle."""

    def __init__(self, session_factory: sessionmaker):
        self.session_factory = session_factory

    def save_upload(self, *, file_id, owner_id, storage_key, original_filename, byte_size, sha256=None):
        row = Upload(
            file_id=uuid.UUID(str(file_id)),
            owner_id=uuid.UUID(str(owner_id)),
            storage_key=storage_key,
            original_filename=original_filename,
            byte_size=byte_size,
            sha256=sha256,
        )
        with self.session_factory.begin() as session:
            session.add(row)
            session.flush()
        return row

    def save_plan(self, *, plan, file_id, owner_id):
        plan_data = _json_value(plan)
        row = Plan(
            plan_id=uuid.UUID(str(plan_data["plan_id"])),
            file_id=uuid.UUID(str(file_id)),
            owner_id=uuid.UUID(str(owner_id)),
            goal=plan_data["goal"],
            plan_json=plan_data,
            status="created",
        )
        with self.session_factory.begin() as session:
            session.add(row)
            session.flush()
        return row

    def get_plan(self, *, plan_id, owner_id):
        with self.session_factory() as session:
            return session.scalar(
                select(Plan).where(
                    Plan.plan_id == uuid.UUID(str(plan_id)),
                    Plan.owner_id == uuid.UUID(str(owner_id)),
                )
            )

    def list_plans(self, *, owner_id, limit=50):
        with self.session_factory() as session:
            return list(
                session.scalars(
                    select(Plan)
                    .where(Plan.owner_id == uuid.UUID(str(owner_id)))
                    .order_by(Plan.created_at.desc(), Plan.plan_id.desc())
                    .limit(min(max(int(limit), 1), 100))
                )
            )

    def approve_plan(self, *, plan_id, owner_id, actor_user_id):
        """Atomically approve once and record the owner as the approving actor."""
        plan_uuid = uuid.UUID(str(plan_id))
        owner_uuid = uuid.UUID(str(owner_id))
        actor_uuid = uuid.UUID(str(actor_user_id))
        now = datetime.now(timezone.utc)
        with self.session_factory.begin() as session:
            changed = session.execute(
                update(Plan)
                .where(
                    Plan.plan_id == plan_uuid,
                    Plan.owner_id == owner_uuid,
                    Plan.status == "created",
                )
                .values(status="approved", updated_at=now)
                .returning(Plan.plan_id)
            ).scalar_one_or_none()
            if changed is not None:
                session.add(ApprovalEvent(plan_id=plan_uuid, actor_user_id=actor_uuid, approved_at=now))
                session.flush()
                return True, "approved"

            current = session.scalar(
                select(Plan.status).where(
                    Plan.plan_id == plan_uuid,
                    Plan.owner_id == owner_uuid,
                )
            )
            if current is None:
                return False, "not_found"
            if current == "approved":
                return True, "approved"
            return False, current

    def claim_plan_for_execution(self, *, plan_id, owner_id):
        """One conditional UPDATE is the cross-process atomic execution claim."""
        plan_uuid = uuid.UUID(str(plan_id))
        owner_uuid = uuid.UUID(str(owner_id))
        now = datetime.now(timezone.utc)
        with self.session_factory.begin() as session:
            claimed_id = session.execute(
                update(Plan)
                .where(
                    Plan.plan_id == plan_uuid,
                    Plan.owner_id == owner_uuid,
                    Plan.status == "approved",
                )
                .values(status="executing", updated_at=now)
                .returning(Plan.plan_id)
            ).scalar_one_or_none()
            if claimed_id is not None:
                row = session.scalar(
                    select(Plan).where(Plan.plan_id == plan_uuid, Plan.owner_id == owner_uuid)
                )
                return True, row, "claimed"

            row = session.scalar(
                select(Plan).where(Plan.plan_id == plan_uuid, Plan.owner_id == owner_uuid)
            )
            if row is None:
                return False, None, "not_found"
            return False, row, row.status

    def update_plan_status(self, *, plan_id, owner_id, status, result=None, error=None):
        values = {"status": status, "updated_at": datetime.now(timezone.utc)}
        if result is not None:
            values["result_json"] = _json_value(result)
        if error is not None:
            values["error"] = error
        with self.session_factory.begin() as session:
            changed = session.execute(
                update(Plan)
                .where(
                    Plan.plan_id == uuid.UUID(str(plan_id)),
                    Plan.owner_id == uuid.UUID(str(owner_id)),
                )
                .values(**values)
                .returning(Plan.plan_id)
            ).scalar_one_or_none()
            return changed is not None

    def get_report(self, *, report_id, owner_id):
        with self.session_factory() as session:
            return session.scalar(
                select(Report).where(
                    Report.report_id == uuid.UUID(str(report_id)),
                    Report.owner_id == uuid.UUID(str(owner_id)),
                )
            )
