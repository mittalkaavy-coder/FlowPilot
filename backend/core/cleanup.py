"""Safe, retention-based cleanup for FlowPilot uploads and report artifacts."""
import logging
import sqlite3
import threading
import time
from pathlib import Path

from core.config import settings

logger = logging.getLogger(__name__)
_cleanup_lock = threading.Lock()
_ARTIFACT_EXTENSIONS = {"uploads": ".csv", "reports": ".md"}


def _active_workflow_references() -> tuple[set[str], bool, bool]:
    """Return active upload IDs, whether workflows are active, and DB read safety."""
    database = Path(settings.db_path).resolve()
    if not database.is_file():
        return set(), False, True

    try:
        connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=2)
        try:
            rows = connection.execute(
                "SELECT file_id FROM plans WHERE status IN ('created', 'approved', 'executing')"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        # If workflow state cannot be read, fail closed and preserve all artifacts.
        logger.warning("Cleanup skipped because workflow state could not be read (%s)", type(exc).__name__)
        return set(), False, False

    file_ids = {str(row[0]) for row in rows if row[0]}
    return file_ids, bool(rows), True


def _is_within_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return path != root
    except ValueError:
        return False


def _clean_directory(
    directory: Path,
    root: Path,
    cutoff: float,
    extension: str,
    protected_names: set[str],
    database_path: Path,
) -> tuple[int, int]:
    """Delete expired direct regular files only; return deleted and failed counts."""
    deleted = failed = 0
    try:
        if directory.is_symlink() or not directory.is_dir():
            return deleted, failed
        resolved_directory = directory.resolve()
        expected_directory_name = "uploads" if extension == ".csv" else "reports"
        if (
            not _is_within_root(resolved_directory, root)
            or resolved_directory.parent != root
            or resolved_directory.name != expected_directory_name
        ):
            logger.warning("Cleanup skipped a storage directory outside the configured root")
            return deleted, failed

        for candidate in resolved_directory.iterdir():
            try:
                # Do not follow symlinks or recurse into unrelated directories.
                if candidate.is_symlink() or not candidate.is_file():
                    continue
                if candidate.suffix.lower() != extension:
                    continue
                if candidate.name in protected_names:
                    continue
                resolved_candidate = candidate.resolve(strict=True)
                if not _is_within_root(resolved_candidate, root):
                    continue
                if resolved_candidate == database_path:
                    continue
                if candidate.stat().st_mtime >= cutoff:
                    continue
                candidate.unlink()
                deleted += 1
            except OSError as exc:
                failed += 1
                logger.warning("Cleanup could not process an artifact (%s)", type(exc).__name__)
    except OSError as exc:
        failed += 1
        logger.warning("Cleanup could not access a storage directory (%s)", type(exc).__name__)

    return deleted, failed


def cleanup_old_files(
    max_age_hours: int | None = None,
    *,
    upload_retention_hours: int | None = None,
    report_retention_hours: int | None = None,
) -> dict[str, int]:
    """Remove expired CSV and Markdown artifacts under the configured storage root.

    ``max_age_hours`` remains available for compatibility and applies to both
    artifact types when provided. Automatic invocation is controlled by app
    settings; this function itself remains callable by explicit maintenance code.
    """
    cleaned = {"uploads": 0, "reports": 0}
    if not _cleanup_lock.acquire(blocking=False):
        logger.info("Cleanup skipped because another cleanup run is active")
        return cleaned

    failures = 0
    try:
        if max_age_hours is not None:
            upload_retention_hours = report_retention_hours = max_age_hours
        if upload_retention_hours is None:
            upload_retention_hours = settings.upload_retention_hours
        if report_retention_hours is None:
            report_retention_hours = settings.report_retention_hours

        root = Path(settings.storage_root).resolve()
        database_path = Path(settings.db_path).resolve()
        active_file_ids, has_active_workflows, state_is_safe = _active_workflow_references()
        if not state_is_safe:
            logger.warning("Cleanup skipped because active workflow references are unavailable")
            return cleaned

        now = time.time()
        upload_cutoff = now - upload_retention_hours * 3600
        report_cutoff = now - report_retention_hours * 3600
        upload_names = {f"{file_id}.csv" for file_id in active_file_ids}

        cleaned["uploads"], upload_failures = _clean_directory(
            Path(settings.upload_dir), root, upload_cutoff, _ARTIFACT_EXTENSIONS["uploads"],
            upload_names, database_path,
        )
        failures += upload_failures

        # A report may exist before its in-progress workflow result is persisted.
        # Preserve reports while plans are pending approval or executing.
        if not has_active_workflows:
            cleaned["reports"], report_failures = _clean_directory(
                Path(settings.report_dir), root, report_cutoff, _ARTIFACT_EXTENSIONS["reports"],
                set(), database_path,
            )
            failures += report_failures
        else:
            logger.info("Report cleanup deferred while workflows are active")

        logger.info(
            "Cleanup complete: removed %d uploads and %d reports; failures: %d",
            cleaned["uploads"], cleaned["reports"], failures,
        )
        return cleaned
    finally:
        _cleanup_lock.release()
