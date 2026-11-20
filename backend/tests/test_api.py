"""Integration tests using FastAPI TestClient."""
import io
import uuid
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from main import app
from api.models.plan import WorkflowPlan, WorkflowStep
from api.models.execute import ExecuteResponse, StepResult
from core.store import StoredPlan

client = TestClient(app)

_FILE_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_PLAN_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
_REPORT_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"

_CSV_BYTES = b"Product,Sales,Date,Region\nWidget A,1500.00,2024-01-15,North\nWidget B,2300.00,2024-01-20,South\n"


# ─── health ──────────────────────────────────────────────────────────────────

def test_health_endpoint():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ─── upload ──────────────────────────────────────────────────────────────────

def test_upload_valid_csv(tmp_path):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(exist_ok=True)

    with (
        patch("core.config.settings.upload_dir", upload_dir),
        patch("api.routes.files.inspect_csv") as mock_inspect,
    ):
        mock_inspect.return_value = {
            "shape": {"rows": 2, "columns": 4},
            "columns": [
                {"name": "Product"},
                {"name": "Sales"},
                {"name": "Date"},
                {"name": "Region"},
            ],
        }

        r = client.post(
            "/api/upload",
            files={"file": ("sales.csv", io.BytesIO(_CSV_BYTES), "text/csv")},
        )
    assert r.status_code == 200
    body = r.json()
    assert "file_id" in body
    assert body["rows"] == 2
    assert body["columns"] == 4
    assert "Product" in body["column_names"]


def test_upload_rejects_non_csv(tmp_path):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(exist_ok=True)

    with patch("core.config.settings.upload_dir", upload_dir):
        r = client.post(
            "/api/upload",
            files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")},
        )
    assert r.status_code == 415


def test_upload_rejects_oversized(tmp_path):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(exist_ok=True)

    with (
        patch("core.config.settings.upload_dir", upload_dir),
        patch("core.config.settings.max_upload_bytes", 10),
    ):
        big_data = b"a,b\n" + b"x,y\n" * 100
        r = client.post(
            "/api/upload",
            files={"file": ("big.csv", io.BytesIO(big_data), "text/csv")},
        )
    assert r.status_code == 413


# ─── plan ────────────────────────────────────────────────────────────────────

def test_plan_endpoint():
    mock_plan = WorkflowPlan(
        plan_id=_PLAN_ID,
        goal="Analyse sales",
        steps=[
            WorkflowStep(step_number=1, tool="csv_inspector", description="Inspect", args={"file_id": _FILE_ID}),
            WorkflowStep(step_number=2, tool="csv_analyzer", description="Analyze", args={"file_id": _FILE_ID, "value_column": "Sales"}),
            WorkflowStep(step_number=3, tool="report_generator", description="Report", args={"file_id": _FILE_ID, "title": "Report"}),
            WorkflowStep(step_number=4, tool="artifact_verifier", description="Verify", args={}),
        ],
    )

    with (
        patch("api.routes.plan.inspect_csv", return_value={"shape": {"rows": 2, "columns": 4}, "columns": []}),
        patch("api.routes.plan.create_plan", return_value=mock_plan),
        patch("api.routes.plan.save_plan"),
    ):
        r = client.post("/api/plan", json={"file_id": _FILE_ID, "instruction": "Analyse top products"})

    assert r.status_code == 200
    body = r.json()
    assert body["plan_id"] == _PLAN_ID
    assert "steps" in body
    assert len(body["steps"]) == 4


def test_plan_endpoint_503_on_service_unavailable():
    from agent.planner import PlannerUnavailableError
    provider_error = PlannerUnavailableError(
        "Gemini provider remained temporarily unavailable after the bounded "
        "HTTP 504 DEADLINE_EXCEEDED retry policy."
    )
    with (
        patch("api.routes.plan.inspect_csv", return_value={"shape": {"rows": 2, "columns": 4}, "columns": []}),
        patch("api.routes.plan.create_plan", side_effect=provider_error),
    ):
        r = client.post("/api/plan", json={"file_id": _FILE_ID, "instruction": "Analyse top products"})

    assert r.status_code == 503
    assert "Gemini service unavailable" in r.json()["detail"]
    assert "HTTP 504 DEADLINE_EXCEEDED" in r.json()["detail"]
    assert "API_KEY" not in r.json()["detail"]


def test_plan_endpoint_400_on_permanent_error():
    from agent.planner import PlannerPermanentError
    with (
        patch("api.routes.plan.inspect_csv", return_value={"shape": {"rows": 2, "columns": 4}, "columns": []}),
        patch("api.routes.plan.create_plan", side_effect=PlannerPermanentError("API_KEY_INVALID")),
    ):
        r = client.post("/api/plan", json={"file_id": _FILE_ID, "instruction": "Analyse top products"})

    assert r.status_code == 400
    assert "Planning request rejected" in r.json()["detail"]


# ─── execute ─────────────────────────────────────────────────────────────────

def test_execute_endpoint():
    mock_plan = WorkflowPlan(
        plan_id=_PLAN_ID,
        goal="Analyse sales",
        steps=[
            WorkflowStep(step_number=1, tool="csv_inspector", description="Inspect", args={"file_id": _FILE_ID}),
            WorkflowStep(step_number=2, tool="csv_analyzer", description="Analyze", args={"file_id": _FILE_ID, "value_column": "Sales"}),
            WorkflowStep(step_number=3, tool="report_generator", description="Report", args={"file_id": _FILE_ID, "title": "Report"}),
            WorkflowStep(step_number=4, tool="artifact_verifier", description="Verify", args={}),
        ],
    )
    mock_stored = StoredPlan(
        plan_id=_PLAN_ID,
        file_id=_FILE_ID,
        goal="Analyse sales",
        plan=mock_plan,
        status="approved",
    )
    mock_response = ExecuteResponse(
        plan_id=_PLAN_ID,
        goal="Analyse sales",
        steps=[
            StepResult(step_number=1, tool="csv_inspector", description="Inspect", status="success", output={}),
            StepResult(step_number=2, tool="csv_analyzer", description="Analyze", status="success", output={}),
            StepResult(step_number=3, tool="report_generator", description="Report", status="success", output={}),
            StepResult(step_number=4, tool="artifact_verifier", description="Verify", status="success", output={"verified": True}),
        ],
        report_id=_REPORT_ID,
        verified=True,
        summary="Workflow completed successfully. All 4 steps executed.",
    )

    with (
        patch("api.routes.execute.get_plan", return_value=mock_stored),
        patch("api.routes.execute.claim_plan_for_execution", return_value=(True, mock_stored, "claimed")),
        patch("api.routes.execute.execute_plan", return_value=mock_response),
    ):
        r = client.post("/api/execute", json={"plan_id": _PLAN_ID})

    assert r.status_code == 200
    body = r.json()
    assert body["plan_id"] == _PLAN_ID
    assert body["verified"] is True
    assert body["report_id"] == _REPORT_ID
    assert "summary" in body


def test_execute_unknown_plan_id():
    with patch("api.routes.execute.get_plan", return_value=None):
        r = client.post("/api/execute", json={"plan_id": str(uuid.uuid4())})
    assert r.status_code == 404


# ─── download ────────────────────────────────────────────────────────────────

def test_download_missing_report(tmp_path):
    missing_id = str(uuid.uuid4())
    report_dir = tmp_path / "reports"
    report_dir.mkdir(exist_ok=True)

    with patch("core.config.settings.report_dir", report_dir):
        r = client.get(f"/api/download/{missing_id}")
    assert r.status_code == 404
