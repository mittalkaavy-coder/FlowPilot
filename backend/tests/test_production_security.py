"""Production hardening checks for report access and public response safety."""
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from unittest.mock import patch

from api.models.execute import ExecuteResponse, StepResult
from api.models.plan import WorkflowPlan, WorkflowStep
from core import store
from core.config import settings
from core.security import resolve_report_path, resolve_upload_path
from main import app

client = TestClient(app)


@pytest.fixture
def isolated_storage(tmp_path, monkeypatch):
    root = tmp_path / "storage"
    uploads = root / "uploads"
    reports = root / "reports"
    uploads.mkdir(parents=True)
    reports.mkdir()
    monkeypatch.setattr(settings, "storage_root", root)
    monkeypatch.setattr(settings, "upload_dir", uploads)
    monkeypatch.setattr(settings, "report_dir", reports)
    monkeypatch.setattr(settings, "db_path", root / "flowpilot.db")
    store.clear_store()
    yield root, uploads, reports
    store.clear_store()


def _verified_result(plan_id: str, report_id: str, *, verified=True) -> ExecuteResponse:
    return ExecuteResponse(
        plan_id=plan_id,
        goal="Analyze the uploaded dataset",
        steps=[
            StepResult(step_number=1, tool="csv_inspector", description="Inspect", status="success"),
            StepResult(step_number=2, tool="csv_analyzer", description="Analyze", status="success"),
            StepResult(
                step_number=3,
                tool="report_generator",
                description="Generate report",
                status="success",
                output={"report_id": report_id, "path": "C:/private/reports/report.md", "size_bytes": 25},
            ),
            StepResult(
                step_number=4,
                tool="artifact_verifier",
                description="Verify report",
                status="success" if verified else "error",
                output={
                    "verified": verified,
                    "report_id": report_id,
                    "integrity": {"passed": True},
                    "completeness": {"passed": verified},
                },
            ),
        ],
        report_id=report_id,
        verified=verified,
        summary="Workflow result",
    )


def _persist_report(report_id: str, *, status="completed", result=None) -> str:
    plan_id = str(uuid.uuid4())
    file_id = str(uuid.uuid4())
    plan = WorkflowPlan(
        plan_id=plan_id,
        goal="Analyze the uploaded dataset",
        steps=[
            WorkflowStep(step_number=1, tool="csv_inspector", description="Inspect", args={"file_id": file_id}),
            WorkflowStep(step_number=2, tool="csv_analyzer", description="Analyze", args={"file_id": file_id, "value_column": "Value"}),
            WorkflowStep(step_number=3, tool="report_generator", description="Report", args={"file_id": file_id, "title": "Report"}),
            WorkflowStep(step_number=4, tool="artifact_verifier", description="Verify", args={}),
        ],
    )
    store.save_plan(plan, file_id)
    store.update_plan_status(
        plan_id,
        status,
        result=result if result is not None else _verified_result(plan_id, report_id),
    )
    return plan_id


def test_download_completed_verified_report(isolated_storage):
    _, _, reports = isolated_storage
    report_id = str(uuid.uuid4())
    _persist_report(report_id)
    (reports / f"{report_id}.md").write_text("# Verified report\n", encoding="utf-8")

    response = client.get(f"/api/download/{report_id}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.headers["content-disposition"] == f'attachment; filename="report_{report_id}.md"'
    assert response.content == (reports / f"{report_id}.md").read_bytes()


@pytest.mark.parametrize(
    ("status", "verified", "expected_status"),
    [("completed", False, 409), ("failed", True, 409)],
)
def test_download_unverified_or_failed_report_is_rejected(
    isolated_storage, status, verified, expected_status
):
    _, _, reports = isolated_storage
    report_id = str(uuid.uuid4())
    plan_id = _persist_report(
        report_id,
        status=status,
        result=_verified_result("placeholder", report_id, verified=verified),
    )
    # The result must identify the same stored plan as well as the report.
    stored = store.get_plan(plan_id)
    store.update_plan_status(
        plan_id, status, result=_verified_result(plan_id, report_id, verified=verified)
    )
    assert stored is not None
    (reports / f"{report_id}.md").write_text("# Report\n", encoding="utf-8")

    response = client.get(f"/api/download/{report_id}")

    assert response.status_code == expected_status
    assert "verification" in response.json()["detail"].lower()


def test_download_unrelated_report_id_is_not_served(isolated_storage):
    _, _, reports = isolated_storage
    known_id, unrelated_id = str(uuid.uuid4()), str(uuid.uuid4())
    _persist_report(known_id)
    (reports / f"{unrelated_id}.md").write_text("# Unrelated\n", encoding="utf-8")

    response = client.get(f"/api/download/{unrelated_id}")

    assert response.status_code == 404


def test_download_rejects_symlinked_report(isolated_storage, monkeypatch):
    _, _, reports = isolated_storage
    report_id = str(uuid.uuid4())
    _persist_report(report_id)
    link = reports / f"{report_id}.md"
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda candidate: candidate == link or original_is_symlink(candidate),
    )

    response = client.get(f"/api/download/{report_id}")

    assert response.status_code == 400


def test_upload_resolver_rejects_symlinked_file(isolated_storage, monkeypatch):
    _, uploads, _ = isolated_storage
    file_id = str(uuid.uuid4())
    link = uploads / f"{file_id}.csv"
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda candidate: candidate == link or original_is_symlink(candidate),
    )

    with pytest.raises(HTTPException) as exc:
        resolve_upload_path(file_id)
    assert exc.value.status_code == 400


def test_report_resolver_rejects_similar_prefix_outside_directory(
    isolated_storage, tmp_path, monkeypatch
):
    _, _, reports = isolated_storage
    report_id = str(uuid.uuid4())
    sibling = tmp_path / "storage-reports-sibling"
    sibling.mkdir()
    outside = sibling / f"{report_id}.md"
    outside.write_text("# Outside\n", encoding="utf-8")
    candidate = reports / f"{report_id}.md"
    original_resolve = Path.resolve

    def resolve_with_external_target(path, strict=False):
        if path == candidate:
            return outside
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve_with_external_target)

    with pytest.raises(HTTPException) as exc:
        resolve_report_path(report_id)
    assert exc.value.status_code == 400


def test_execution_response_removes_paths_recursively(isolated_storage):
    plan_id, file_id, report_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    plan = WorkflowPlan(
        plan_id=plan_id,
        goal="Analyze dataset",
        steps=[
            WorkflowStep(step_number=1, tool="csv_inspector", description="Inspect", args={"file_id": file_id}),
            WorkflowStep(step_number=2, tool="csv_analyzer", description="Analyze", args={"file_id": file_id, "value_column": "Value"}),
            WorkflowStep(step_number=3, tool="report_generator", description="Report", args={"file_id": file_id, "title": "Report"}),
            WorkflowStep(step_number=4, tool="artifact_verifier", description="Verify", args={}),
        ],
    )
    store.save_plan(plan, file_id)
    store.approve_plan(plan_id)
    stored = store.get_plan(plan_id)
    response_result = _verified_result(plan_id, report_id)
    response_result.steps[2].output["nested"] = {
        "file_path": "C:\\private\\flowpilot\\reports\\secret.md",
        "message": "Could not read /private/storage/hidden.md",
    }

    with (
        patch("api.routes.execute.get_plan", return_value=stored),
        patch("api.routes.execute.claim_plan_for_execution", return_value=(True, stored, "claimed")),
        patch("api.routes.execute.execute_plan", return_value=response_result),
    ):
        response = client.post("/api/execute", json={"plan_id": plan_id})

    assert response.status_code == 200
    body = response.json()
    assert body["report_id"] == report_id
    assert body["steps"][2]["output"]["size_bytes"] == 25
    assert "path" not in body["steps"][2]["output"]
    assert "file_path" not in body["steps"][2]["output"]["nested"]
    assert "C:\\private" not in response.text
    assert "/private/storage" not in response.text
    persisted = store.get_plan(plan_id)
    assert persisted.result.steps[2].output["path"] == "C:/private/reports/report.md"


def test_public_diagnostic_is_disabled_by_default_and_never_returns_credentials(
    isolated_storage, monkeypatch
):
    monkeypatch.setattr(settings, "diagnostics_enabled", False)
    disabled = client.get("/health/diagnostic")
    assert disabled.status_code == 404

    monkeypatch.setattr(settings, "diagnostics_enabled", True)
    enabled = client.get("/health/diagnostic")
    assert enabled.status_code == 200
    body = enabled.json()
    assert body == {
        "status": "ok",
        "cleanup_enabled": settings.cleanup_enabled,
        "storage_writable": True,
    }
    assert "GEMINI" not in enabled.text
    assert "API_KEY" not in enabled.text
    assert str(settings.storage_root) not in enabled.text
