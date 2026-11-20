"""Live PostgreSQL integration coverage for the Phase 1B data foundation.

These tests require an already migrated, disposable PostgreSQL test database in
FLOWPILOT_DATABASE_URL. They never create or downgrade a schema.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError

from core.database import create_postgres_engine, create_session_factory
from core.db_models import (
    LEGACY_OWNER_ID,
    ApprovalEvent,
    Plan,
    Report,
    Session,
    Upload,
    User,
)
from core.pg_store import PostgresStore
from scripts.import_legacy_sqlite import import_legacy_snapshot


@pytest.fixture(scope="module")
def postgres_factory():
    database_url = os.environ.get("FLOWPILOT_DATABASE_URL")
    if not database_url:
        pytest.skip("FLOWPILOT_DATABASE_URL is unavailable; live PostgreSQL tests skipped")

    engine = create_postgres_engine(database_url)
    if engine.dialect.name != "postgresql":
        engine.dispose()
        pytest.fail("FLOWPILOT_DATABASE_URL must target PostgreSQL; SQLite fallback is forbidden")
    factory = create_session_factory(engine)
    try:
        # Verify reachability without logging the URL or any connection details.
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT 1")) == 1
        yield engine, factory
    finally:
        engine.dispose()


def _cleanup(factory, *, user_ids=(), session_ids=(), upload_ids=(), plan_ids=(), report_ids=()):
    """Delete only rows owned by this test, in foreign-key-safe order."""
    with factory.begin() as db:
        if report_ids:
            db.execute(delete(Report).where(Report.report_id.in_(report_ids)))
        if plan_ids:
            db.execute(delete(ApprovalEvent).where(ApprovalEvent.plan_id.in_(plan_ids)))
            db.execute(delete(Plan).where(Plan.plan_id.in_(plan_ids)))
        if upload_ids:
            db.execute(delete(Upload).where(Upload.file_id.in_(upload_ids)))
        if session_ids:
            db.execute(delete(Session).where(Session.id.in_(session_ids)))
        if user_ids:
            db.execute(delete(User).where(User.id.in_(user_ids)))


def _legacy_fixture(root, statuses):
    source_root = root / "legacy-source"
    uploads_dir = source_root / "uploads"
    reports_dir = source_root / "reports"
    uploads_dir.mkdir(parents=True)
    reports_dir.mkdir()
    source_db = root / "legacy-snapshot.sqlite3"
    upload_ids = [uuid.uuid4() for _ in statuses]
    orphan_upload_id = uuid.uuid4()
    plan_ids = [uuid.uuid4() for _ in statuses]
    report_ids = [uuid.uuid4() for _ in statuses]
    orphan_report_id = uuid.uuid4()

    with sqlite3.connect(source_db) as db:
        db.execute(
            """CREATE TABLE plans (
                plan_id TEXT PRIMARY KEY, file_id TEXT NOT NULL, goal TEXT NOT NULL,
                plan_json TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL,
                updated_at REAL NOT NULL, result_json TEXT, error TEXT
            )"""
        )
        for index, (status, file_id, plan_id, report_id) in enumerate(
            zip(statuses, upload_ids, plan_ids, report_ids)
        ):
            plan_data = {"plan_id": str(plan_id), "goal": f"Historical {status}", "steps": []}
            steps = [
                {"tool": "csv_inspector", "status": "success", "output": {}},
                {"tool": "csv_analyzer", "status": "success", "output": {}},
                {"tool": "report_generator", "status": "success", "output": {"report_id": str(report_id)}},
                {"tool": "artifact_verifier", "status": "success", "output": {
                    "report_id": str(report_id), "verified": True,
                    "integrity": {"passed": True}, "completeness": {"passed": True},
                }},
            ]
            result_data = {"report_id": str(report_id), "verified": True, "steps": steps}
            db.execute(
                "INSERT INTO plans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(plan_id), str(file_id), plan_data["goal"], json.dumps(plan_data), status,
                 1_700_000_000.0 + index, 1_700_000_100.0 + index,
                 json.dumps(result_data), "historical failure" if status == "failed" else None),
            )
            (uploads_dir / f"{file_id}.csv").write_bytes(f"value\n{index}\n".encode())
            # Leave the failed plan's linked report artifact absent to verify a
            # historical missing-report record, while preserving its report ID.
            if status != "failed":
                (reports_dir / f"{report_id}.md").write_bytes(f"# {status} report\n".encode())

    (uploads_dir / f"{orphan_upload_id}.csv").write_bytes(b"orphan upload\n")
    (reports_dir / f"{orphan_report_id}.md").write_bytes(b"# Orphan historical report\n")
    return source_db, source_root, upload_ids + [orphan_upload_id], plan_ids, report_ids + [orphan_report_id]



def test_postgres_repository_users_sessions_ownership_approval_and_types(postgres_factory):
    engine, factory = postgres_factory
    store = PostgresStore(factory)

    owner_id, other_id = uuid.uuid4(), uuid.uuid4()
    session_id = uuid.uuid4()
    file_id, plan_id, report_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    nonce = uuid.uuid4().hex

    user_ids = [owner_id, other_id]
    session_ids = [session_id]
    upload_ids = [file_id]
    plan_ids = [plan_id]
    report_ids = [report_id]

    try:
        now = datetime.now(timezone.utc)

        # Create parent users first, then create their dependent session.
        with factory.begin() as db:
            db.add_all([
                User(
                    id=owner_id,
                    oidc_issuer="https://phase1b.example.test",
                    oidc_subject=f"owner-{nonce}",
                ),
                User(
                    id=other_id,
                    oidc_issuer="https://phase1b.example.test",
                    oidc_subject=f"other-{nonce}",
                ),
            ])

            # Persist users before inserting the session referencing owner_id.
            db.flush()

            db.add(
                Session(
                    id=session_id,
                    user_id=owner_id,
                    token_hash=hashlib.sha256(
                        f"token-{nonce}".encode()
                    ).digest(),
                    csrf_token_hash=hashlib.sha256(
                        f"csrf-{nonce}".encode()
                    ).digest(),
                    created_at=now,
                    last_seen_at=now,
                    idle_expires_at=now + timedelta(hours=1),
                    absolute_expires_at=now + timedelta(hours=8),
                )
            )

        # Verify upload ownership.
        saved_upload = store.save_upload(
            file_id=file_id,
            owner_id=owner_id,
            storage_key=f"phase1b-test/{nonce}/upload.csv",
            original_filename="sample.csv",
            byte_size=12,
        )
        assert saved_upload.owner_id == owner_id

        # Create a plan and verify owner isolation.
        plan_data = {
            "plan_id": str(plan_id),
            "goal": "PG live coverage",
            "steps": [],
        }

        saved_plan = store.save_plan(
            plan=plan_data,
            file_id=file_id,
            owner_id=owner_id,
        )

        assert saved_plan.owner_id == owner_id
        assert store.get_plan(
            plan_id=plan_id,
            owner_id=other_id,
        ) is None

        # Verify PostgreSQL enforces the composite (file_id, owner_id) FK.
        with pytest.raises(IntegrityError):
            with factory.begin() as db:
                db.add(
                    Plan(
                        plan_id=uuid.uuid4(),
                        file_id=file_id,
                        owner_id=other_id,
                        goal="cross-owner upload",
                        plan_json={"goal": "cross-owner upload"},
                    )
                )

        # Verify approval authorization and idempotency.
        assert store.approve_plan(
            plan_id=plan_id,
            owner_id=other_id,
            actor_user_id=other_id,
        ) == (False, "not_found")

        assert store.approve_plan(
            plan_id=plan_id,
            owner_id=owner_id,
            actor_user_id=owner_id,
        ) == (True, "approved")

        assert store.approve_plan(
            plan_id=plan_id,
            owner_id=owner_id,
            actor_user_id=owner_id,
        ) == (True, "approved")

        # Exactly one approval audit event must exist.
        with factory() as db:
            event_count = db.scalar(
                select(func.count())
                .select_from(ApprovalEvent)
                .where(ApprovalEvent.plan_id == plan_id)
            )

            assert event_count == 1

            event = db.scalar(
                select(ApprovalEvent).where(
                    ApprovalEvent.plan_id == plan_id
                )
            )

            assert event.actor_user_id == owner_id
            assert event.approved_at.tzinfo is not None

        # Create a report and verify report ownership.
        with factory.begin() as db:
            db.add(
                Report(
                    report_id=report_id,
                    plan_id=plan_id,
                    owner_id=owner_id,
                    storage_key=f"phase1b-test/{nonce}/report.md",
                    verified=True,
                    integrity_passed=True,
                    completeness_passed=True,
                )
            )

        assert store.get_report(
            report_id=report_id,
            owner_id=owner_id,
        ) is not None

        assert store.get_report(
            report_id=report_id,
            owner_id=other_id,
        ) is None

        # Verify composite report/plan ownership enforcement.
        with pytest.raises(IntegrityError):
            with factory.begin() as db:
                db.add(
                    Report(
                        report_id=uuid.uuid4(),
                        plan_id=plan_id,
                        owner_id=other_id,
                        storage_key=f"phase1b-test/{nonce}/cross-owner-report.md",
                    )
                )

        # Verify actual PostgreSQL UUID, JSONB and timestamp types.
        with engine.connect() as connection:
            types = connection.execute(
                text(
                    "SELECT pg_typeof(id)::text, "
                    "(SELECT pg_typeof(plan_json)::text "
                    "FROM plans WHERE plan_id = :plan_id), "
                    "pg_typeof(created_at)::text "
                    "FROM users WHERE id = :owner_id"
                ),
                {
                    "plan_id": plan_id,
                    "owner_id": owner_id,
                },
            ).one()

        assert types == (
            "uuid",
            "jsonb",
            "timestamp with time zone",
        )

        # Verify JSONB and timezone-aware timestamp deserialization.
        with factory() as db:
            loaded_plan = db.get(Plan, plan_id)
            loaded_session = db.get(Session, session_id)

            assert loaded_plan is not None
            assert loaded_session is not None

            assert loaded_plan.plan_id == plan_id
            assert isinstance(loaded_plan.plan_json, dict)

            assert loaded_session.id == session_id
            assert loaded_session.idle_expires_at.tzinfo is not None
            assert loaded_plan.created_at.tzinfo is not None

        # Verify transaction rollback and subsequent connection recovery.
        rolled_back_user_id = uuid.uuid4()

        with pytest.raises(RuntimeError, match="intentional rollback"):
            with factory.begin() as db:
                db.add(
                    User(
                        id=rolled_back_user_id,
                        oidc_issuer="https://phase1b.example.test",
                        oidc_subject=f"rollback-{nonce}",
                    )
                )

                db.flush()
                raise RuntimeError("intentional rollback")

        with factory() as db:
            assert db.get(User, rolled_back_user_id) is None
            assert db.scalar(text("SELECT 1")) == 1

        # Delete the test session and verify connections remain usable.
        with factory.begin() as db:
            db.execute(
                delete(Session).where(
                    Session.id == session_id
                )
            )

        session_ids.clear()

        with engine.connect() as connection:
            assert connection.scalar(text("SELECT 1")) == 1

    finally:
        # Clean up only records created by this test.
        _cleanup(
            factory,
            user_ids=user_ids,
            session_ids=session_ids,
            upload_ids=upload_ids,
            plan_ids=plan_ids,
            report_ids=report_ids,
        )




def test_postgres_independent_connections_claim_execution_once(postgres_factory):
    engine, factory = postgres_factory
    store = PostgresStore(factory)
    owner_id, file_id, plan_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    user_ids, upload_ids, plan_ids = [owner_id], [file_id], [plan_id]
    nonce = uuid.uuid4().hex
    try:
        with factory.begin() as db:
            db.add(User(id=owner_id, oidc_issuer="https://phase1b.example.test", oidc_subject=f"race-{nonce}"))
        store.save_upload(file_id=file_id, owner_id=owner_id, storage_key=f"phase1b-test/{nonce}/race.csv", original_filename="race.csv", byte_size=1)
        store.save_plan(plan={"plan_id": str(plan_id), "goal": "race", "steps": []}, file_id=file_id, owner_id=owner_id)
        assert store.approve_plan(plan_id=plan_id, owner_id=owner_id, actor_user_id=owner_id) == (True, "approved")

        barrier = Barrier(2)

        def claim_from_own_connection():
            # Hold a distinct pooled connection during the synchronized claim;
            # the repository obtains its own connection for its transaction.
            with engine.connect() as held_connection:
                connection_pid = held_connection.scalar(text("SELECT pg_backend_pid()"))
                barrier.wait(timeout=15)
                claimed, row, status = store.claim_plan_for_execution(plan_id=plan_id, owner_id=owner_id)
                return claimed, status, row.status if row else None, connection_pid

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: claim_from_own_connection(), range(2)))
        assert sum(result[0] for result in results) == 1, results
        assert sorted(result[1] for result in results) == ["claimed", "executing"]
        assert len({result[3] for result in results}) == 2
        assert store.get_plan(plan_id=plan_id, owner_id=owner_id).status == "executing"
        assert store.approve_plan(plan_id=plan_id, owner_id=owner_id, actor_user_id=owner_id) == (False, "executing")
    finally:
        _cleanup(factory, user_ids=user_ids, upload_ids=upload_ids, plan_ids=plan_ids)


@pytest.mark.parametrize("statuses", [["created", "approved", "executing", "completed", "failed"]])
def test_postgres_legacy_import_states_artifacts_idempotency_and_conflicts(postgres_factory, tmp_path, statuses):
    _, factory = postgres_factory
    source_db, source_root, upload_ids, plan_ids, report_ids = _legacy_fixture(tmp_path, statuses)
    target_root = tmp_path / "separate-import-target"
    all_report_ids = report_ids
    try:
        first = import_legacy_snapshot(
            source_db=source_db, source_storage_root=source_root,
            target_storage_root=target_root, session_factory=factory,
        )
        assert first == {"plans": 5, "uploads": 6, "reports": 6, "files_copied": 11}

        with factory() as db:
            imported_plans = db.scalars(select(Plan).where(Plan.plan_id.in_(plan_ids))).all()
            imported_uploads = db.scalars(select(Upload).where(Upload.file_id.in_(upload_ids))).all()
            imported_reports = db.scalars(select(Report).where(Report.report_id.in_(all_report_ids))).all()
        assert {plan.status for plan in imported_plans} == set(statuses)
        assert all(plan.owner_id == LEGACY_OWNER_ID for plan in imported_plans)
        assert len(imported_uploads) == 6
        assert all(upload.owner_id == LEGACY_OWNER_ID for upload in imported_uploads)
        assert len(imported_reports) == 6
        by_report = {report.report_id: report for report in imported_reports}
        for status, plan_id, report_id in zip(statuses, plan_ids, report_ids):
            report = by_report[report_id]
            assert report.plan_id == plan_id
            assert report.storage_status == ("missing" if status == "failed" else "available")
            assert report.verified is (status == "completed")
        orphan_report = by_report[report_ids[-1]]
        assert orphan_report.plan_id is None and orphan_report.verified is False

        second = import_legacy_snapshot(
            source_db=source_db, source_storage_root=source_root,
            target_storage_root=target_root, session_factory=factory,
        )
        assert second == {"plans": 0, "uploads": 0, "reports": 0, "files_copied": 0}

        # Existing storage objects with different bytes are never overwritten.
        conflicting_upload = target_root / "legacy" / "uploads" / f"{upload_ids[0]}.csv"
        original_target = conflicting_upload.read_bytes()
        conflicting_upload.write_bytes(b"conflicting target bytes")
        with pytest.raises(FileExistsError, match="different content"):
            import_legacy_snapshot(
                source_db=source_db, source_storage_root=source_root,
                target_storage_root=target_root, session_factory=factory,
            )
        assert conflicting_upload.read_bytes() == b"conflicting target bytes"
        conflicting_upload.write_bytes(original_target)

        # A colliding relational ID with changed imported content is rejected.
        conflict_root = tmp_path / "conflicting-source"
        conflict_db, conflict_storage, _, _, _ = _legacy_fixture(conflict_root, statuses)
        # Use the original artifact bytes and IDs for this second snapshot so
        # the intended collision is the existing plan row, not a storage key.
        with sqlite3.connect(conflict_db) as db:
            db.execute("DELETE FROM plans")
            with sqlite3.connect(source_db) as original:
                rows = original.execute("SELECT * FROM plans ORDER BY created_at, plan_id").fetchall()
            db.executemany("INSERT INTO plans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
            first_row = db.execute("SELECT plan_id, goal, plan_json FROM plans ORDER BY created_at, plan_id LIMIT 1").fetchone()
            changed_plan = json.loads(first_row[2])
            changed_plan["goal"] = "conflicting content"
            db.execute("UPDATE plans SET goal = ?, plan_json = ? WHERE plan_id = ?", ("conflicting content", json.dumps(changed_plan), first_row[0]))
        # Keep all source artifact bytes identical while reusing the target IDs.
        for subdirectory in ("uploads", "reports"):
            conflict_dir = conflict_storage / subdirectory
            for path in conflict_dir.glob("*"):
                path.unlink()
            for path in (source_root / subdirectory).glob("*"):
                shutil.copy2(path, conflict_dir / path.name)
        with pytest.raises(ValueError, match="different ownership or content"):
            import_legacy_snapshot(
                source_db=conflict_db, source_storage_root=conflict_storage,
                target_storage_root=target_root, session_factory=factory,
            )
    finally:
        _cleanup(factory, upload_ids=upload_ids, plan_ids=plan_ids, report_ids=all_report_ids)
