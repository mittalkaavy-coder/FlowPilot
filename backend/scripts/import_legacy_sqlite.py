"""Copy a SQLite legacy snapshot and artifacts into an already-migrated target.

The source database is opened read-only and must be a copy, not the active
FlowPilot SQLite database. Target artifact files are created exclusively and
never overwrite an existing object.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from core.config import settings
from core.database import create_postgres_engine
from core.db_models import LEGACY_OWNER_ID, Plan, Report, Upload, User


def _utc_timestamp(value: Any) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    return datetime.fromtimestamp(float(value), tz=timezone.utc)


def _contained_direct_file(root: Path, subdirectory: str, filename: str) -> Path | None:
    base = root / subdirectory
    if base.is_symlink() or not base.is_dir():
        return None
    candidate = base / filename
    if candidate.is_symlink() or not candidate.is_file():
        return None
    resolved_root = root.resolve()
    resolved_base = base.resolve()
    resolved_candidate = candidate.resolve(strict=True)
    try:
        if resolved_base.parent != resolved_root or resolved_candidate.parent != resolved_base:
            raise ValueError("Legacy artifact is outside the expected storage directory")
    except OSError as exc:
        raise ValueError("Legacy artifact could not be resolved safely") from exc
    return candidate


def _copy_without_overwrite(
    source: Path, destination: Path, target_root: Path
) -> tuple[int, bytes, bool]:
    if target_root.is_symlink():
        raise ValueError("Target storage root must not be a symlink")
    relative_parent = destination.parent.relative_to(target_root)
    current = target_root
    for component in relative_parent.parts:
        current = current / component
        if current.is_symlink():
            raise ValueError("Target legacy storage contains a symlink")
        current.mkdir(exist_ok=True)
        if not current.resolve().is_relative_to(target_root.resolve()):
            raise ValueError("Target storage directory resolves outside its root")
    digest = hashlib.sha256()
    size = 0
    if destination.exists():
        if destination.is_symlink() or not destination.is_file():
            raise FileExistsError("A target storage key is occupied by an unexpected file type")
        with source.open("rb") as source_stream, destination.open("rb") as target_stream:
            source_hash = hashlib.sha256(source_stream.read()).digest()
            target_hash = hashlib.sha256(target_stream.read()).digest()
        if source_hash != target_hash:
            raise FileExistsError("An existing target storage object has different content")
        return destination.stat().st_size, source_hash, False

    created = False
    try:
        with source.open("rb") as source_stream, destination.open("xb") as target_stream:
            created = True
            while True:
                block = source_stream.read(1024 * 1024)
                if not block:
                    break
                target_stream.write(block)
                digest.update(block)
                size += len(block)
        return size, digest.digest(), True
    except Exception:
        if created:
            destination.unlink(missing_ok=True)
        raise


def _read_legacy_snapshot(source_db: Path, source_storage_root: Path):
    uri = f"{source_db.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("SELECT * FROM plans ORDER BY created_at, plan_id").fetchall()
    finally:
        connection.close()

    plans = []
    report_plan_ids: dict[uuid.UUID, tuple[uuid.UUID, dict]] = {}
    required_upload_ids: set[uuid.UUID] = set()
    for row in rows:
        plan_id = uuid.UUID(str(row["plan_id"]))
        file_id = uuid.UUID(str(row["file_id"]))
        status = str(row["status"])
        if status not in {"created", "approved", "executing", "completed", "failed"}:
            raise ValueError("Legacy database contains an unsupported workflow state")
        plan_data = json.loads(row["plan_json"])
        result_data = json.loads(row["result_json"]) if row["result_json"] else None
        plans.append({
            "plan_id": plan_id,
            "file_id": file_id,
            "goal": row["goal"],
            "plan_json": plan_data,
            "status": status,
            "created_at": _utc_timestamp(row["created_at"]),
            "updated_at": _utc_timestamp(row["updated_at"]),
            "result_json": result_data,
            "error": row["error"],
        })
        required_upload_ids.add(file_id)
        report_id = (result_data or {}).get("report_id")
        if report_id:
            parsed_report_id = uuid.UUID(str(report_id))
            if parsed_report_id in report_plan_ids:
                raise ValueError("Legacy database associates one report with multiple workflows")
            report_plan_ids[parsed_report_id] = (plan_id, result_data or {})

    return plans, required_upload_ids, report_plan_ids


def import_legacy_snapshot(
    *,
    source_db: str | Path,
    source_storage_root: str | Path,
    target_storage_root: str | Path,
    session_factory: sessionmaker,
    active_sqlite_db: str | Path | None = None,
) -> dict[str, int]:
    """Import a copied SQLite DB and its CSV/Markdown files idempotently."""
    source_db = Path(source_db).resolve(strict=True)
    source_root = Path(source_storage_root).resolve(strict=True)
    target_root = Path(target_storage_root).resolve()
    if active_sqlite_db and source_db == Path(active_sqlite_db).resolve():
        raise ValueError("Refusing to import the active SQLite database; provide a verified copy")
    if source_root == target_root or source_root in target_root.parents:
        raise ValueError("Target storage must be separate from the legacy source storage")
    target_root.mkdir(parents=True, exist_ok=True)

    plan_rows, upload_ids, report_links = _read_legacy_snapshot(source_db, source_root)
    counts = {"plans": 0, "uploads": 0, "reports": 0, "files_copied": 0}
    upload_records: dict[uuid.UUID, dict] = {}
    report_records: dict[uuid.UUID, dict] = {}

    upload_source_dir = source_root / "uploads"
    if upload_source_dir.exists() and (upload_source_dir.is_symlink() or not upload_source_dir.is_dir()):
        raise ValueError("Legacy upload storage is not a regular directory")
    for source in sorted(upload_source_dir.iterdir()) if upload_source_dir.exists() else []:
        if source.suffix.lower() != ".csv":
            continue
        if source.is_symlink() or not source.is_file():
            raise ValueError("Legacy upload directory contains an unexpected artifact type")
        file_id = uuid.UUID(source.stem)
        safe_source = _contained_direct_file(source_root, "uploads", source.name)
        if safe_source is None:
            raise ValueError("Legacy upload artifact could not be resolved safely")
        storage_key = f"legacy/uploads/{file_id}.csv"
        destination = target_root / storage_key
        byte_size, sha256, copied = _copy_without_overwrite(safe_source, destination, target_root)
        counts["files_copied"] += int(copied)
        # `available` and the content digest are copied into relational metadata.
        upload_records[file_id] = {
            "file_id": file_id,
            "owner_id": LEGACY_OWNER_ID,
            "storage_key": storage_key,
            "original_filename": source.name,
            "byte_size": byte_size,
            "sha256": sha256,
            "storage_status": "available",
        }

    for file_id in upload_ids:
        upload_records.setdefault(file_id, {
            "file_id": file_id,
            "owner_id": LEGACY_OWNER_ID,
            "storage_key": f"legacy/uploads/{file_id}.csv",
            "original_filename": None,
            "byte_size": 0,
            "sha256": None,
            "storage_status": "missing",
        })

    report_source_dir = source_root / "reports"
    if report_source_dir.exists() and (report_source_dir.is_symlink() or not report_source_dir.is_dir()):
        raise ValueError("Legacy report storage is not a regular directory")
    for source in sorted(report_source_dir.iterdir()) if report_source_dir.exists() else []:
        if source.suffix.lower() != ".md":
            continue
        if source.is_symlink() or not source.is_file():
            raise ValueError("Legacy report directory contains an unexpected artifact type")
        report_id = uuid.UUID(source.stem)
        safe_source = _contained_direct_file(source_root, "reports", source.name)
        if safe_source is None:
            raise ValueError("Legacy report artifact could not be resolved safely")
        storage_key = f"legacy/reports/{report_id}.md"
        destination = target_root / storage_key
        byte_size, sha256, copied = _copy_without_overwrite(safe_source, destination, target_root)
        counts["files_copied"] += int(copied)
        plan_link = report_links.get(report_id)
        result_data = plan_link[1] if plan_link else {}
        steps = result_data.get("steps", [])
        if not isinstance(steps, list):
            steps = []
        verifier = next(
            (step.get("output", {}) for step in steps
             if isinstance(step, dict) and step.get("tool") == "artifact_verifier"),
            {},
        )
        report_output = next(
            (step.get("output", {}) for step in steps
             if isinstance(step, dict) and step.get("tool") == "report_generator"),
            {},
        )
        if not isinstance(verifier, dict):
            verifier = {}
        integrity = verifier.get("integrity", {}) if isinstance(verifier, dict) else {}
        completeness = verifier.get("completeness", {}) if isinstance(verifier, dict) else {}
        integrity_passed = integrity.get("passed") is True
        completeness_passed = completeness.get("passed") is True
        verified = (
            result_data.get("verified") is True
            and verifier.get("verified") is True
            and verifier.get("report_id") == str(report_id)
            and isinstance(report_output, dict)
            and report_output.get("report_id") == str(report_id)
            and integrity_passed
            and completeness_passed
            and len(steps) == 4
            and [step.get("tool") for step in steps if isinstance(step, dict)] == [
                "csv_inspector", "csv_analyzer", "report_generator", "artifact_verifier"
            ]
            and all(step.get("status") == "success" for step in steps if isinstance(step, dict))
            and plan_link is not None
            and next((p["status"] for p in plan_rows if p["plan_id"] == plan_link[0]), None) == "completed"
        )
        report_records[report_id] = {
            "report_id": report_id,
            "plan_id": plan_link[0] if plan_link else None,
            "owner_id": LEGACY_OWNER_ID,
            "storage_key": storage_key,
            "verified": verified,
            "integrity_passed": integrity_passed,
            "completeness_passed": completeness_passed,
            "storage_status": "available",
            "byte_size": byte_size,
            "sha256": sha256,
        }

    for report_id, (plan_id, _) in report_links.items():
        report_records.setdefault(report_id, {
            "report_id": report_id,
            "plan_id": plan_id,
            "owner_id": LEGACY_OWNER_ID,
            "storage_key": f"legacy/reports/{report_id}.md",
            "verified": False,
            "integrity_passed": False,
            "completeness_passed": False,
            "storage_status": "missing",
            "byte_size": 0,
            "sha256": None,
        })

    with session_factory.begin() as session:
        legacy_owner = session.get(User, LEGACY_OWNER_ID)
        if legacy_owner is None or not legacy_owner.is_legacy_owner:
            raise RuntimeError("The reserved non-login legacy owner is missing from the target schema")

        for record in upload_records.values():
            existing = session.get(Upload, record["file_id"])
            if existing:
                if (
                    existing.owner_id != LEGACY_OWNER_ID
                    or existing.storage_key != record["storage_key"]
                    or existing.sha256 != record["sha256"]
                    or existing.byte_size != record["byte_size"]
                    or existing.storage_status != record["storage_status"]
                ):
                    raise ValueError("A target upload ID is already assigned to a different owner or storage key")
                continue
            session.add(Upload(**record))
            counts["uploads"] += 1

        session.flush()
        for record in plan_rows:
            existing = session.get(Plan, record["plan_id"])
            if existing:
                if (
                    existing.owner_id != LEGACY_OWNER_ID
                    or existing.plan_json != record["plan_json"]
                    or existing.status != record["status"]
                    or existing.result_json != record["result_json"]
                ):
                    raise ValueError("A target plan ID already has different ownership or content")
                continue
            session.add(Plan(owner_id=LEGACY_OWNER_ID, **record))
            counts["plans"] += 1

        session.flush()
        for record in report_records.values():
            existing = session.get(Report, record["report_id"])
            if existing:
                if (
                    existing.owner_id != LEGACY_OWNER_ID
                    or existing.storage_key != record["storage_key"]
                    or existing.sha256 != record["sha256"]
                    or existing.verified != record["verified"]
                    or existing.plan_id != record["plan_id"]
                ):
                    raise ValueError("A target report ID is already assigned to a different owner or storage key")
                continue
            session.add(Report(**record))
            counts["reports"] += 1

    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db-copy", required=True, help="Verified, read-only SQLite backup copy")
    parser.add_argument("--source-storage-root", required=True, help="Storage root corresponding to that backup")
    parser.add_argument("--target-storage-root", required=True, help="Separate private persistent target storage")
    parser.add_argument("--confirm-import", action="store_true", help="Required acknowledgement before copying legacy records")
    args = parser.parse_args()
    if not args.confirm_import:
        parser.error("pass --confirm-import after verifying source and target backups")
    if not settings.database_url:
        parser.error("FLOWPILOT_DATABASE_URL must point to a separate development PostgreSQL database")
    engine = create_postgres_engine()
    try:
        result = import_legacy_snapshot(
            source_db=args.source_db_copy,
            source_storage_root=args.source_storage_root,
            target_storage_root=args.target_storage_root,
            session_factory=sessionmaker(bind=engine, expire_on_commit=False, autoflush=False),
            active_sqlite_db=settings.db_path,
        )
        print("Legacy import complete: " + ", ".join(f"{key}={value}" for key, value in result.items()))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
