import uuid
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from core.security import (
    resolve_report_path,
    validate_upload,
    validate_csv_content,
    validate_uuid,
)
from core.config import settings
from core.store import get_workflows_for_report
from tools.csv_inspector import run as inspect_csv

router = APIRouter(prefix="/api", tags=["files"])


class UploadTooLargeError(MultiPartException):
    """Raised by the streaming multipart parser when a file exceeds its limit."""


class SizeLimitedMultiPartParser(MultiPartParser):
    """Enforce the configured total file bytes while multipart data is parsed."""

    def __init__(self, headers, stream, max_file_bytes: int):
        super().__init__(headers, stream)
        self.max_file_bytes = max_file_bytes
        self.file_bytes = 0

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if self._current_part.file is not None:
            self.file_bytes += end - start
            if self.file_bytes > self.max_file_bytes:
                raise UploadTooLargeError("Upload exceeds the maximum allowed size")
        super().on_part_data(data, start, end)


@router.post("/upload")
async def upload_file(request: Request):
    """
    Accept and validate a CSV file upload.

    Enforces:
    - Max 10 MB file size
    - .csv extension
    - Content validation: valid UTF-8, non-empty, parseable tabular format
    - Generates isolated UUIDv4 file identifier
    """
    # Parse this endpoint's multipart body directly. The parser counts file bytes
    # as they arrive, so a missing or inaccurate Content-Length cannot bypass the
    # upload ceiling and an oversized body is stopped before full buffering.
    parser = SizeLimitedMultiPartParser(
        request.headers,
        request.stream(),
        max_file_bytes=settings.max_upload_bytes,
    )
    try:
        form = await parser.parse()
    except UploadTooLargeError:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum allowed size is {settings.max_upload_bytes // (1024 * 1024)} MB",
        )
    except MultiPartException:
        raise HTTPException(status_code=400, detail="Invalid multipart upload")

    file = form.get("file")
    if not isinstance(file, UploadFile):
        await form.close()
        raise HTTPException(status_code=422, detail="Missing upload file")

    try:
        filename = file.filename or "upload.csv"
        contents = await file.read()
    finally:
        await form.close()

    size = len(contents)

    # Validate metadata (length, extension, size)
    validate_upload(filename, size)

    # Validate raw CSV content (UTF-8, tabular structure)
    validate_csv_content(contents)

    file_id = str(uuid.uuid4())
    dest = settings.upload_dir / f"{file_id}.csv"
    dest.write_bytes(contents)

    # Run quick schema inspection
    try:
        inspection = inspect_csv(file_id)
    except Exception:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="Could not parse uploaded CSV")

    return {
        "file_id": file_id,
        "filename": filename,
        "rows": inspection["shape"]["rows"],
        "columns": inspection["shape"]["columns"],
        "column_names": [col["name"] for col in inspection["columns"]],
    }


@router.get("/download/{report_id}")
def download_report(report_id: str):
    """
    Download a generated Markdown report safely.

    Enforces:
    - Valid UUID report identifier
    - Path traversal prevention
    - File existence and non-zero size checks
    """
    validate_uuid(report_id, "report_id")
    workflows = get_workflows_for_report(report_id)
    if not workflows:
        raise HTTPException(status_code=404, detail="Verified report not found")

    def is_completed_and_verified(workflow) -> bool:
        if workflow.status != "completed" or workflow.result is None:
            return False
        result = (
            workflow.result.model_dump()
            if hasattr(workflow.result, "model_dump")
            else workflow.result
        )
        if not isinstance(result, dict):
            return False
        if result.get("plan_id") != workflow.plan_id:
            return False
        if result.get("report_id") != report_id or result.get("verified") is not True:
            return False
        steps = result.get("steps")
        if not isinstance(steps, list) or len(steps) != 4:
            return False
        if any(not isinstance(step, dict) or step.get("status") != "success" for step in steps):
            return False
        if [step.get("tool") for step in steps] != [
            "csv_inspector", "csv_analyzer", "report_generator", "artifact_verifier"
        ]:
            return False
        report_step = next(
            (step for step in steps if step.get("tool") == "report_generator"), None
        )
        verifier_step = next(
            (step for step in steps if step.get("tool") == "artifact_verifier"), None
        )
        if not report_step or not verifier_step:
            return False
        report_output = report_step.get("output") or {}
        verification = verifier_step.get("output") or {}
        if not isinstance(report_output, dict) or report_output.get("report_id") != report_id:
            return False
        if not isinstance(verification, dict):
            return False
        return (
            verification.get("verified") is True
            and verification.get("report_id") == report_id
            and isinstance(verification.get("integrity"), dict)
            and verification["integrity"].get("passed") is True
            and isinstance(verification.get("completeness"), dict)
            and verification["completeness"].get("passed") is True
        )

    if not any(is_completed_and_verified(workflow) for workflow in workflows):
        raise HTTPException(
            status_code=409,
            detail="Report is unavailable because its workflow did not complete verification.",
        )

    path = resolve_report_path(report_id)

    if not path.exists():
        raise HTTPException(status_code=404, detail="Verified report artifact not found")

    if path.stat().st_size == 0:
        raise HTTPException(status_code=404, detail="Verified report artifact not found")

    return FileResponse(
        path=str(path),
        media_type="text/markdown",
        filename=f"report_{report_id}.md",
        headers={"Content-Disposition": f'attachment; filename="report_{report_id}.md"'},
    )
