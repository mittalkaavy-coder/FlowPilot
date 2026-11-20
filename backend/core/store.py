import json
import sqlite3
import time
from pathlib import Path
from typing import Optional, Any
from pydantic import BaseModel, Field

from core.config import settings
from api.models.plan import WorkflowPlan
from api.models.execute import ExecuteResponse


class StoredPlan(BaseModel):
    plan_id: str
    file_id: str
    goal: str
    plan: WorkflowPlan
    status: str = "created"  # created | approved | executing | completed | failed
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    result: Optional[Any] = None
    error: Optional[str] = None


def get_connection() -> sqlite3.Connection:
    """Create and configure a SQLite connection with WAL mode and row factory."""
    db_file = Path(settings.db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_file), timeout=10.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    _init_db(conn)
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    """Ensure plans table and indexes exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS plans (
            plan_id TEXT PRIMARY KEY,
            file_id TEXT NOT NULL,
            goal TEXT NOT NULL,
            plan_json TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            result_json TEXT,
            error TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_plans_status ON plans(status)")
    conn.commit()


def _row_to_stored(row: sqlite3.Row) -> StoredPlan:
    """Convert a database row into a StoredPlan instance."""
    plan_data = json.loads(row["plan_json"])
    plan = WorkflowPlan(**plan_data)
    result = None
    if row["result_json"]:
        try:
            result_data = json.loads(row["result_json"])
            result = ExecuteResponse(**result_data)
        except Exception:
            result = json.loads(row["result_json"])

    return StoredPlan(
        plan_id=row["plan_id"],
        file_id=row["file_id"],
        goal=row["goal"],
        plan=plan,
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        result=result,
        error=row["error"],
    )


def save_plan(plan: WorkflowPlan, file_id: str) -> StoredPlan:
    """Persist a validated plan in SQLite with status 'created'."""
    stored = StoredPlan(
        plan_id=plan.plan_id,
        file_id=file_id,
        goal=plan.goal,
        plan=plan,
        status="created",
    )
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO plans (plan_id, file_id, goal, plan_json, status, created_at, updated_at, result_json, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stored.plan_id,
                    stored.file_id,
                    stored.goal,
                    stored.plan.model_dump_json(),
                    stored.status,
                    stored.created_at,
                    stored.updated_at,
                    None,
                    None,
                ),
            )
    finally:
        conn.close()
    return stored


def get_plan(plan_id: str) -> Optional[StoredPlan]:
    """Retrieve stored plan from SQLite by ID."""
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT * FROM plans WHERE plan_id = ?", (plan_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return _row_to_stored(row)
    finally:
        conn.close()


def get_workflows_for_report(report_id: str) -> list[StoredPlan]:
    """Find persisted workflow results that reference a report ID."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM plans WHERE result_json IS NOT NULL"
        ).fetchall()
        matches: list[StoredPlan] = []
        for row in rows:
            try:
                stored = _row_to_stored(row)
            except (ValueError, TypeError, KeyError):
                continue
            result = stored.result
            stored_report_id = (
                result.get("report_id") if isinstance(result, dict)
                else getattr(result, "report_id", None)
            )
            if stored_report_id == report_id:
                matches.append(stored)
        return matches
    finally:
        conn.close()


def approve_plan(plan_id: str) -> tuple[bool, Optional[StoredPlan], str]:
    """Atomically transition a newly created plan to approved.

    Repeated approval of an already approved plan is idempotent. Other states
    cannot be approved, and legacy ``created`` plans remain unapproved until an
    explicit approval request is received.
    """
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM plans WHERE plan_id = ?", (plan_id,)
        ).fetchone()
        if not row:
            conn.commit()
            return False, None, "not_found"

        current_status = row["status"]
        if current_status == "approved":
            conn.commit()
            return True, _row_to_stored(row), "approved"
        if current_status != "created":
            conn.commit()
            return False, _row_to_stored(row), current_status

        now = time.time()
        conn.execute(
            "UPDATE plans SET status = 'approved', updated_at = ? "
            "WHERE plan_id = ? AND status = 'created'",
            (now, plan_id),
        )
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM plans WHERE plan_id = ?", (plan_id,)
        ).fetchone()
        stored = _row_to_stored(updated) if updated else None
        return stored is not None and stored.status == "approved", stored, (
            stored.status if stored else "not_found"
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def claim_plan_for_execution(plan_id: str) -> tuple[bool, Optional[StoredPlan], str]:
    """
    Atomically claim a plan for execution using SQLite BEGIN IMMEDIATE.
    Guarantees atomic check-and-set from 'approved' to 'executing'.

    Returns:
        (claimed: bool, stored: Optional[StoredPlan], status: str)
        - If claimed is True: status is 'claimed', plan is now in 'executing' status.
        - If claimed is False and plan not found: status is 'not_found'.
        - If claimed is False and plan already started/completed/failed: status is current status.
    """
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute("SELECT * FROM plans WHERE plan_id = ?", (plan_id,))
        row = cursor.fetchone()
        if not row:
            conn.commit()
            return False, None, "not_found"

        current_status = row["status"]
        if current_status != "approved":
            conn.commit()
            return False, _row_to_stored(row), current_status

        now = time.time()
        conn.execute(
            "UPDATE plans SET status = 'executing', updated_at = ? WHERE plan_id = ? AND status = 'approved'",
            (now, plan_id),
        )
        conn.commit()

        cursor = conn.execute("SELECT * FROM plans WHERE plan_id = ?", (plan_id,))
        return True, _row_to_stored(cursor.fetchone()), "claimed"
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_plan_status(
    plan_id: str,
    status: str,
    result: Optional[Any] = None,
    error: Optional[str] = None,
) -> Optional[StoredPlan]:
    """Update status, result, and error of a plan in SQLite."""
    now = time.time()
    result_json = None
    if result is not None:
        if hasattr(result, "model_dump_json"):
            result_json = result.model_dump_json()
        elif hasattr(result, "dict"):
            result_json = json.dumps(result.dict())
        else:
            result_json = json.dumps(result)

    conn = get_connection()
    try:
        with conn:
            cursor = conn.execute("SELECT * FROM plans WHERE plan_id = ?", (plan_id,))
            row = cursor.fetchone()
            if not row:
                return None

            conn.execute(
                """
                UPDATE plans
                SET status = ?,
                    updated_at = ?,
                    result_json = COALESCE(?, result_json),
                    error = COALESCE(?, error)
                WHERE plan_id = ?
                """,
                (status, now, result_json, error, plan_id),
            )
            cursor = conn.execute("SELECT * FROM plans WHERE plan_id = ?", (plan_id,))
            updated_row = cursor.fetchone()
            return _row_to_stored(updated_row) if updated_row else None
    finally:
        conn.close()


def clear_store() -> None:
    """Clear all records from plans table."""
    conn = get_connection()
    try:
        with conn:
            conn.execute("DELETE FROM plans")
    finally:
        conn.close()
