import io
import re
import uuid
from pathlib import Path
from fastapi import HTTPException
import pandas as pd

from core import config
from core.config import settings

_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

MAX_FILENAME_LEN = 255
_WINDOWS_PATH_PATTERN = re.compile(r"(?i)\b[A-Z]:[\\/][^\s<>\"|]+")
_POSIX_PATH_PATTERN = re.compile(r"(?<![\w:])/(?:[^/\s<>\"']+/)*[^/\s<>\"']*")
_PATH_FIELDS = {
    "path", "file_path", "filepath", "absolute_path", "filesystem_path",
    "report_path", "upload_path", "storage_path",
}


def sanitize_public_text(value: str) -> str:
    """Remove configured and absolute filesystem locations from public text."""
    safe_value = str(value)
    roots = (
        config.settings.storage_root,
        config.settings.report_dir,
        config.settings.upload_dir,
        config.settings.db_path.parent,
    )
    for root in roots:
        root_text = str(root)
        if root_text:
            safe_value = safe_value.replace(root_text, "[FlowPilot storage]")
            safe_value = safe_value.replace(root_text.replace("\\", "/"), "[FlowPilot storage]")
    safe_value = _WINDOWS_PATH_PATTERN.sub("[filesystem path]", safe_value)
    safe_value = _POSIX_PATH_PATTERN.sub("[filesystem path]", safe_value)
    return safe_value


def sanitize_public_data(value):
    """Recursively remove path-valued fields and redact paths in public data."""
    if isinstance(value, dict):
        return {
            key: sanitize_public_data(item)
            for key, item in value.items()
            if not (isinstance(key, str) and (
                key.lower() in _PATH_FIELDS or key.lower().endswith("_path")
            ))
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_public_data(item) for item in value]
    if isinstance(value, str):
        return sanitize_public_text(value)
    return value


def _resolve_storage_file(directory: Path, filename: str, label: str, *, must_exist: bool) -> Path:
    """Resolve one direct child file and reject symlinks or paths outside its root."""
    configured_directory = Path(directory)
    if configured_directory.is_symlink():
        raise HTTPException(status_code=400, detail=f"Invalid {label} storage directory")

    try:
        base = configured_directory.resolve(strict=True)
    except (OSError, RuntimeError):
        raise HTTPException(status_code=404, detail=f"{label.capitalize()} not found")
    if not base.is_dir():
        raise HTTPException(status_code=404, detail=f"{label.capitalize()} not found")

    candidate = base / filename
    if candidate.is_symlink():
        raise HTTPException(status_code=400, detail=f"Invalid {label} file path")
    try:
        target = candidate.resolve(strict=must_exist)
    except (OSError, RuntimeError):
        if must_exist:
            raise HTTPException(status_code=404, detail=f"{label.capitalize()} not found")
        target = candidate.resolve(strict=False)

    try:
        contained = target.is_relative_to(base)
    except AttributeError:  # Python versions before pathlib.is_relative_to
        try:
            target.relative_to(base)
            contained = True
        except ValueError:
            contained = False
    if not contained or target == base:
        raise HTTPException(status_code=400, detail=f"Invalid {label} file path")
    if target.exists() and not target.is_file():
        raise HTTPException(status_code=400, detail=f"Invalid {label} file type")
    if must_exist and not target.is_file():
        raise HTTPException(status_code=404, detail=f"{label.capitalize()} not found")
    return target


def validate_uuid(value: str, label: str = "identifier") -> None:
    """Raise 400 if value is not a valid UUID string."""
    if not isinstance(value, str) or not _UUID_PATTERN.match(value.strip()):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {label}: must be a valid UUID",
        )
    try:
        uuid.UUID(value.strip())
    except Exception:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {label}: must be a valid UUID",
        )


def resolve_upload_path(file_id: str) -> Path:
    """
    Resolve uploads/{file_id}.csv safely.

    Raises:
        400 — invalid UUID format or path traversal detected
        404 — file not found
    """
    validate_uuid(file_id, "file_id")

    return _resolve_storage_file(
        config.settings.upload_dir, f"{file_id}.csv", "upload", must_exist=True
    )


def resolve_report_path(report_id: str) -> Path:
    """
    Resolve reports/{report_id}.md safely.

    Raises:
        400 — invalid UUID format or path traversal
    """
    validate_uuid(report_id, "report_id")

    return _resolve_storage_file(
        config.settings.report_dir, f"{report_id}.md", "report", must_exist=False
    )


def validate_upload(filename: str, size: int) -> None:
    """
    Validate upload metadata.

    Raises:
        400 — filename too long
        415 — not a .csv file
        413 — file exceeds max size
    """
    if len(filename) > MAX_FILENAME_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"Filename too long (max {MAX_FILENAME_LEN} characters)",
        )

    if not filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=415,
            detail="Only CSV files are accepted (.csv extension required)",
        )

    if size > config.settings.max_upload_bytes:
        max_mb = config.settings.max_upload_bytes // (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum allowed size is {max_mb} MB",
        )


def validate_csv_content(contents: bytes) -> None:
    """
    Validate raw uploaded CSV file content.

    Raises:
        422 — empty file, non-text bytes, or unparseable CSV structure
    """
    if not contents or len(contents.strip()) == 0:
        raise HTTPException(status_code=422, detail="CSV file is empty")

    try:
        decoded = contents.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=422,
            detail="CSV content is not valid UTF-8 encoded text",
        )

    try:
        df = pd.read_csv(io.StringIO(decoded))
        if df.columns.empty or len(df.columns) == 0:
            raise HTTPException(
                status_code=422,
                detail="CSV contains no detectable columns or headers",
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"CSV content could not be parsed: {exc}",
        )
