from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import uuid
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from core.config import settings
from core.database import create_postgres_engine
from core.db_models import (
    LEGACY_OWNER_ID,
    Plan,
    Report,
    Upload,
    User,
)
from scripts.import_legacy_sqlite import import_legacy_snapshot


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def run_test():
    if not settings.database_url:
        raise RuntimeError("FLOWPILOT_DATABASE_URL is not configured.")

    engine = create_postgres_engine()
    factory = sessionmaker(
        bind=engine,
        expire_on_commit=False,
        autoflush=False,
    )

    file_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    report_id = uuid.uuid4()

    csv_content = b"name,marks\nAlice,85\nBob,91\n"
    report_content = (
        b"# Synthetic FlowPilot Report\n\n"
        b"This report exists only for the importer integration test.\n"
    )

    try:
        with tempfile.TemporaryDirectory(prefix="flowpilot-import-test-") as temp:
            root = Path(temp)
            source_root = root / "legacy-source"
            target_root = root / "target-storage"
            uploads_dir = source_root / "uploads"
            reports_dir = source_root / "reports"

            uploads_dir.mkdir(parents=True)
            reports_dir.mkdir(parents=True)
            target_root.mkdir(parents=True)

            source_db = root / "legacy-snapshot.sqlite"

            # Create the exact legacy SQLite plans schema.
            with sqlite3.connect(source_db) as conn:
                conn.execute("""
                    CREATE TABLE plans (
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
                """)

                steps = [
                    {
                        "tool": "csv_inspector",
                        "status": "success",
                        "output": {"columns": ["name", "marks"]},
                    },
                    {
                        "tool": "csv_analyzer",
                        "status": "success",
                        "output": {"rows_analyzed": 2},
                    },
                    {
                        "tool": "report_generator",
                        "status": "success",
                        "output": {"report_id": str(report_id)},
                    },
                    {
                        "tool": "artifact_verifier",
                        "status": "success",
                        "output": {
                            "verified": True,
                            "report_id": str(report_id),
                            "integrity": {"passed": True},
                            "completeness": {"passed": True},
                        },
                    },
                ]

                result_data = {
                    "report_id": str(report_id),
                    "verified": True,
                    "steps": steps,
                }

                plan_data = {
                    "plan_id": str(plan_id),
                    "goal": "Synthetic importer integration test",
                    "steps": [],
                }
                conn.execute(
                    """
                    INSERT INTO plans (
                        plan_id, file_id, goal, plan_json, status,
                        created_at, updated_at, result_json, error
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(plan_id),
                        str(file_id),
                        "Synthetic importer integration test",
                        json.dumps(plan_data),
                        "completed",
                        1790000000.0,
                        1790000100.0,
                        json.dumps(result_data),
                        None,
                    ),
                )

            conn.close()

            upload_source = uploads_dir / f"{file_id}.csv"
            report_source = reports_dir / f"{report_id}.md"

            upload_source.write_bytes(csv_content)
            report_source.write_bytes(report_content)

            # Record source hashes to confirm the importer does not modify them.
            source_db_hash = sha256(source_db.read_bytes())
            source_csv_hash = sha256(upload_source.read_bytes())
            source_report_hash = sha256(report_source.read_bytes())

            with factory() as session:
                legacy_owner = session.get(User, LEGACY_OWNER_ID)
                assert legacy_owner is not None
                assert legacy_owner.is_legacy_owner is True

            print("Starting first import...")
            first = import_legacy_snapshot(
                source_db=source_db,
                source_storage_root=source_root,
                target_storage_root=target_root,
                session_factory=factory,
                active_sqlite_db=settings.db_path,
            )

            print("First import:", first)

            assert first == {
                "plans": 1,
                "uploads": 1,
                "reports": 1,
                "files_copied": 2,
            }, f"Unexpected first import counts: {first}"

            copied_csv = target_root / "legacy" / "uploads" / f"{file_id}.csv"
            copied_report = target_root / "legacy" / "reports" / f"{report_id}.md"

            assert copied_csv.read_bytes() == csv_content
            assert copied_report.read_bytes() == report_content

            # Verify PostgreSQL records and ownership.
            with factory() as session:
                upload = session.get(Upload, file_id)
                plan = session.get(Plan, plan_id)
                report = session.get(Report, report_id)

                assert upload is not None
                assert plan is not None
                assert report is not None

                assert upload.owner_id == LEGACY_OWNER_ID
                assert upload.storage_status == "available"
                assert upload.byte_size == len(csv_content)
                assert upload.sha256 == sha256(csv_content)

                assert plan.owner_id == LEGACY_OWNER_ID
                assert plan.file_id == file_id
                assert plan.status == "completed"

                assert report.owner_id == LEGACY_OWNER_ID
                assert report.plan_id == plan_id
                assert report.verified is True
                assert report.integrity_passed is True
                assert report.completeness_passed is True
                assert report.storage_status == "available"
                assert report.byte_size == len(report_content)
                assert report.sha256 == sha256(report_content)

            print("PostgreSQL records and legacy ownership verified.")
            print("Both copied artifacts and SHA-256 hashes verified.")

            # Retry: no duplicate records and no additional file copies.
            print("Starting second import to test idempotency...")
            second = import_legacy_snapshot(
                source_db=source_db,
                source_storage_root=source_root,
                target_storage_root=target_root,
                session_factory=factory,
                active_sqlite_db=settings.db_path,
            )

            print("Second import:", second)

            assert second == {
                "plans": 0,
                "uploads": 0,
                "reports": 0,
                "files_copied": 0,
            }, f"Retry was not idempotent: {second}"

            # Confirm original source files and database remain unchanged.
            assert sha256(source_db.read_bytes()) == source_db_hash
            assert sha256(upload_source.read_bytes()) == source_csv_hash
            assert sha256(report_source.read_bytes()) == source_report_hash

            print("Source SQLite database and artifacts remain unchanged.")
            print("Idempotent retry verified.")

            print("\nALL LIVE LEGACY IMPORT CHECKS PASSED.")

    finally:
        # Delete only this test's uniquely generated records.
        # Children first, then their referenced parent records.
        try:
            with factory.begin() as session:
                session.execute(
                    delete(Report).where(Report.report_id == report_id)
                )
                session.execute(
                    delete(Plan).where(Plan.plan_id == plan_id)
                )
                session.execute(
                    delete(Upload).where(Upload.file_id == file_id)
                )
            print("Test-created Neon records cleaned up.")
        finally:
            engine.dispose()


if __name__ == "__main__":
    run_test()
