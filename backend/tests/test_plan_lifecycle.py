"""Tests for server-side plan lifecycle, security, idempotency, and tamper-resistance."""
import uuid
import asyncio
import threading
import time
import pytest
import httpx
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from main import app
from api.models.plan import WorkflowPlan, WorkflowStep
from api.models.execute import ExecuteResponse, StepResult
from core.store import save_plan, get_plan, approve_plan, update_plan_status, clear_store

client = TestClient(app)

_FILE_ID = "11111111-1111-4111-8111-111111111111"
_PLAN_ID = "22222222-2222-4222-8222-222222222222"
_REPORT_ID = "33333333-3333-4333-8333-333333333333"


def _build_test_plan(plan_id: str = _PLAN_ID) -> WorkflowPlan:
    return WorkflowPlan(
        plan_id=plan_id,
        goal="Analyze sales dataset",
        steps=[
            WorkflowStep(
                step_number=1,
                tool="csv_inspector",
                description="Inspect CSV",
                args={"file_id": _FILE_ID},
            ),
            WorkflowStep(
                step_number=2,
                tool="csv_analyzer",
                description="Analyze metrics",
                args={"file_id": _FILE_ID, "value_column": "Sales"},
            ),
            WorkflowStep(
                step_number=3,
                tool="report_generator",
                description="Generate report",
                args={"file_id": _FILE_ID, "title": "Report"},
            ),
            WorkflowStep(
                step_number=4,
                tool="artifact_verifier",
                description="Verify report",
                args={},
            ),
        ],
    )


def test_stored_plan_initial_status_is_created():
    clear_store()
    plan = _build_test_plan()
    stored = save_plan(plan, _FILE_ID)
    assert stored.status == "created"
    assert stored.plan_id == _PLAN_ID


@pytest.mark.parametrize("status", ["created", "approved", "completed", "failed"])
def test_recovery_endpoint_returns_current_persisted_workflow(status):
    plan_id = str(uuid.uuid4())
    plan = _build_test_plan(plan_id)
    save_plan(plan, _FILE_ID)
    result = None
    error = None

    if status == "approved":
        assert approve_plan(plan_id)[0] is True
    elif status == "completed":
        assert approve_plan(plan_id)[0] is True
        result = ExecuteResponse(
            plan_id=plan_id,
            goal=plan.goal,
            steps=[
                StepResult(step_number=1, tool="csv_inspector", description="Inspect", status="success"),
                StepResult(step_number=2, tool="csv_analyzer", description="Analyze", status="success"),
                StepResult(
                    step_number=3, tool="report_generator", description="Report", status="success",
                    output={"report_id": _REPORT_ID, "path": "C:/private/flowpilot/reports/report.md"},
                ),
                StepResult(
                    step_number=4, tool="artifact_verifier", description="Verify", status="success",
                    output={
                        "verified": True,
                        "report_id": _REPORT_ID,
                        "integrity": {"passed": True},
                        "completeness": {"passed": True},
                    },
                ),
            ],
            report_id=_REPORT_ID,
            verified=True,
            summary="Workflow completed successfully.",
        )
        update_plan_status(plan_id, "completed", result=result)
    elif status == "failed":
        error = "Analysis failed with useful details"
        update_plan_status(plan_id, "failed", error=error)

    response = client.get(f"/api/workflows/{plan_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["plan_id"] == plan_id
    assert body["file_id"] == _FILE_ID
    assert body["goal"] == plan.goal
    assert [step["tool"] for step in body["steps"]] == [step.tool for step in plan.steps]
    assert body["status"] == status
    if status == "completed":
        assert body["result"]["verified"] is True
        assert body["result"]["report_id"] == _REPORT_ID
        assert body["result"]["summary"] == "Workflow completed successfully."
        assert "path" not in body["result"]["steps"][2]["output"]
        assert "C:/private/flowpilot" not in response.text
    if status == "failed":
        assert body["error"] == error


def test_recovery_endpoint_returns_404_for_unknown_plan():
    response = client.get(f"/api/workflows/{uuid.uuid4()}")
    assert response.status_code == 404


@pytest.mark.parametrize("status", ["executing", "completed"])
def test_recovering_terminal_or_executing_plan_does_not_enable_duplicate_execution(status):
    plan_id = str(uuid.uuid4())
    plan = _build_test_plan(plan_id)
    save_plan(plan, _FILE_ID)
    if status == "completed":
        update_plan_status(plan_id, "completed")
    else:
        assert approve_plan(plan_id)[0] is True
        update_plan_status(plan_id, "executing")

    recovery = client.get(f"/api/workflows/{plan_id}")
    assert recovery.status_code == 200
    assert recovery.json()["status"] == status
    execute = client.post("/api/execute", json={"plan_id": plan_id})
    assert execute.status_code == 409


def test_repeated_execution_request_fails_with_409():
    """Attempting to execute an already completed plan must return 409 Conflict."""
    clear_store()
    plan = _build_test_plan()
    save_plan(plan, _FILE_ID)

    # A created plan cannot bypass the approval transition.
    with patch("api.routes.execute.execute_plan") as execute_spy:
        unapproved = client.post("/api/execute", json={"plan_id": _PLAN_ID})
        execute_spy.assert_not_called()
    assert unapproved.status_code == 409
    assert get_plan(_PLAN_ID).status == "created"
    assert client.post("/api/approve", json={"plan_id": _PLAN_ID}).status_code == 200

    mock_response = ExecuteResponse(
        plan_id=_PLAN_ID,
        goal=plan.goal,
        steps=[
            StepResult(step_number=1, tool="csv_inspector", description="Inspect", status="success", output={}),
            StepResult(step_number=2, tool="csv_analyzer", description="Analyze", status="success", output={}),
        ],
        report_id=_REPORT_ID,
        verified=True,
        summary="Done",
    )

    with patch("api.routes.execute.execute_plan", return_value=mock_response):
        # First execution should succeed
        r1 = client.post("/api/execute", json={"plan_id": _PLAN_ID})
        assert r1.status_code == 200

        # Second execution (duplicate/replay) must fail with 409 Conflict
        r2 = client.post("/api/execute", json={"plan_id": _PLAN_ID})
        assert r2.status_code == 409
        assert "cannot be executed" in r2.json()["detail"]


def test_execution_blocked_when_already_executing():
    """If a plan status is 'executing', concurrent requests must be rejected with 409 Conflict."""
    clear_store()
    plan = _build_test_plan()
    save_plan(plan, _FILE_ID)
    client.post("/api/approve", json={"plan_id": _PLAN_ID})
    update_plan_status(_PLAN_ID, "executing")

    r = client.post("/api/execute", json={"plan_id": _PLAN_ID})
    assert r.status_code == 409
    assert "cannot be executed" in r.json()["detail"]


def test_invalid_plan_id_returns_404():
    clear_store()
    fake_id = str(uuid.uuid4())
    r = client.post("/api/execute", json={"plan_id": fake_id})
    assert r.status_code == 404
    assert f"Plan '{fake_id}' not found" in r.json()["detail"]


def test_tampered_request_body_cannot_inject_steps():
    """
    Even if an attacker passes 'steps' or malicious tool parameters in the request,
    the execute endpoint only parses plan_id and retrieves the server-stored plan.
    """
    clear_store()
    plan = _build_test_plan()
    save_plan(plan, _FILE_ID)
    client.post("/api/approve", json={"plan_id": _PLAN_ID})

    executed_plans = []

    def fake_execute(p):
        executed_plans.append(p)
        return ExecuteResponse(
            plan_id=p.plan_id,
            goal=p.goal,
            steps=[StepResult(step_number=1, tool="csv_inspector", description="", status="success")],
            report_id=None,
            verified=False,
            summary="Done",
        )

    with patch("api.routes.execute.execute_plan", side_effect=fake_execute):
        # Client tries to send tampered steps
        malicious_payload = {
            "plan_id": _PLAN_ID,
            "steps": [
                {"step_number": 1, "tool": "malicious_tool", "description": "hack", "args": {}}
            ],
        }
        r = client.post("/api/execute", json=malicious_payload)
        assert r.status_code == 200

        # Verify that the executor was passed the original server-side plan, NOT the tampered one
        assert len(executed_plans) == 1
        executed_tools = [s.tool for s in executed_plans[0].steps]
        assert "malicious_tool" not in executed_tools
        assert "csv_inspector" in executed_tools


# ─── Phase 2: SQLite Persistence & Atomic Execution Claim Tests ──────────────

def test_sqlite_persistence_across_simulated_restart(tmp_path):
    """Phase 2: Stored plan and execution result persist across simulated backend restart."""
    from core.config import settings
    from core.store import claim_plan_for_execution

    test_db = tmp_path / "persistence_test.db"
    orig_db = settings.db_path
    settings.db_path = test_db
    try:
        clear_store()
        plan_id = str(uuid.uuid4())
        plan = _build_test_plan(plan_id)
        stored = save_plan(plan, _FILE_ID)
        assert stored.status == "created"

        # Update status to completed with a result
        mock_result = ExecuteResponse(
            plan_id=plan_id,
            goal="Analyze",
            steps=[StepResult(step_number=1, tool="csv_inspector", description="", status="success")],
            report_id=_REPORT_ID,
            verified=True,
            summary="All steps executed.",
        )
        update_plan_status(plan_id, "completed", result=mock_result)

        # Simulate backend restart by querying fresh via get_plan()
        recovered = get_plan(plan_id)
        assert recovered is not None
        assert recovered.plan_id == plan_id
        assert recovered.status == "completed"
        assert recovered.result is not None
        assert recovered.result.report_id == _REPORT_ID
        assert recovered.result.verified is True
    finally:
        settings.db_path = orig_db


def test_duplicate_execution_blocked_after_restart(tmp_path):
    """Phase 2: Completed plan cannot be re-executed after restart; returns 409 Conflict."""
    from core.config import settings

    test_db = tmp_path / "replay_restart_test.db"
    orig_db = settings.db_path
    settings.db_path = test_db
    try:
        clear_store()
        plan_id = str(uuid.uuid4())
        plan = _build_test_plan(plan_id)
        save_plan(plan, _FILE_ID)
        assert approve_plan(plan_id)[0] is True

        mock_resp = ExecuteResponse(
            plan_id=plan_id,
            goal="Analyze",
            steps=[StepResult(step_number=1, tool="csv_inspector", description="", status="success")],
            report_id=_REPORT_ID,
            verified=True,
            summary="Done",
        )
        with patch("api.routes.execute.execute_plan", return_value=mock_resp):
            r1 = client.post("/api/execute", json={"plan_id": plan_id})
            assert r1.status_code == 200

        # Simulate restart: new request to /api/execute
        r2 = client.post("/api/execute", json={"plan_id": plan_id})
        assert r2.status_code == 409
        assert "cannot be executed" in r2.json()["detail"]
        assert "completed" in r2.json()["detail"]
    finally:
        settings.db_path = orig_db


def test_atomic_claim_concurrency(tmp_path):
    """Phase 2: claim_plan_for_execution uses BEGIN IMMEDIATE for atomic check-and-set."""
    from core.config import settings
    from core.store import claim_plan_for_execution

    test_db = tmp_path / "claim_test.db"
    orig_db = settings.db_path
    settings.db_path = test_db
    try:
        clear_store()
        plan_id = str(uuid.uuid4())
        plan = _build_test_plan(plan_id)
        save_plan(plan, _FILE_ID)
        assert approve_plan(plan_id)[0] is True

        # Claim 1: succeeds and transitions to 'executing'
        claimed_1, stored_1, status_1 = claim_plan_for_execution(plan_id)
        assert claimed_1 is True
        assert status_1 == "claimed"
        assert stored_1.status == "executing"

        # Claim 2: concurrent attempt fails because status is already 'executing'
        claimed_2, stored_2, status_2 = claim_plan_for_execution(plan_id)
        assert claimed_2 is False
        assert status_2 == "executing"

        # After completion: claim 3 returns completed
        update_plan_status(plan_id, "completed")
        claimed_3, stored_3, status_3 = claim_plan_for_execution(plan_id)
        assert claimed_3 is False
        assert status_3 == "completed"
    finally:
        settings.db_path = orig_db


def test_approval_state_transition_and_terminal_rejections():
    clear_store()
    plan = _build_test_plan()
    save_plan(plan, _FILE_ID)
    response = client.post("/api/approve", json={"plan_id": _PLAN_ID})
    assert response.status_code == 200
    assert response.json() == {"plan_id": _PLAN_ID, "status": "approved"}
    assert get_plan(_PLAN_ID).status == "approved"

    # Repeating approval for the same pending plan is idempotent.
    assert client.post("/api/approve", json={"plan_id": _PLAN_ID}).status_code == 200

    for status in ("completed", "failed", "executing"):
        plan_id = str(uuid.uuid4())
        save_plan(_build_test_plan(plan_id), _FILE_ID)
        update_plan_status(plan_id, status)
        response = client.post("/api/approve", json={"plan_id": plan_id})
        assert response.status_code == 409
        assert status in response.json()["detail"]


@pytest.mark.asyncio
async def test_concurrent_execute_requests_start_executor_once():
    plan_id = str(uuid.uuid4())
    save_plan(_build_test_plan(plan_id), _FILE_ID)
    assert approve_plan(plan_id)[0] is True
    started = 0
    lock = threading.Lock()

    def slow_executor(plan):
        nonlocal started
        with lock:
            started += 1
        time.sleep(0.15)
        return ExecuteResponse(
            plan_id=plan.plan_id,
            goal=plan.goal,
            steps=[StepResult(step_number=1, tool="csv_inspector", description="Inspect", status="success")],
            report_id=_REPORT_ID,
            verified=True,
            summary="Done",
        )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        with patch("api.routes.execute.execute_plan", side_effect=slow_executor):
            responses = await asyncio.gather(
                ac.post("/api/execute", json={"plan_id": plan_id}),
                ac.post("/api/execute", json={"plan_id": plan_id}),
            )
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert started == 1


def test_execute_rejects_missing_or_malformed_plan_id():
    """Ensure invalid plan_ids (missing, empty, N/A, non-UUID) are rejected with HTTP 400."""
    clear_store()
    for bad_id in ["", "   ", "N/A", "not-a-uuid", "12345"]:
        r = client.post("/api/execute", json={"plan_id": bad_id})
        assert r.status_code == 400
        assert "must be a valid UUID" in r.json()["detail"]


def test_validate_plan_enforces_required_fields():
    """validate_plan must reject plans with missing plan_id, empty goal, or empty steps."""
    from agent.validator import validate_plan
    from fastapi import HTTPException

    valid_id = str(uuid.uuid4())
    valid_step = WorkflowStep(step_number=1, tool="csv_inspector", description="Inspect", args={"file_id": _FILE_ID})

    # Missing/empty plan_id
    p1 = WorkflowPlan(plan_id="", goal="Valid goal", steps=[valid_step])
    with pytest.raises(HTTPException) as exc1:
        validate_plan(p1)
    assert exc1.value.status_code == 400
    assert "plan_id" in exc1.value.detail

    # N/A plan_id
    p2 = WorkflowPlan(plan_id="N/A", goal="Valid goal", steps=[valid_step])
    with pytest.raises(HTTPException) as exc2:
        validate_plan(p2)
    assert exc2.value.status_code == 400

    # Missing goal
    p3 = WorkflowPlan(plan_id=valid_id, goal="", steps=[valid_step])
    with pytest.raises(HTTPException) as exc3:
        validate_plan(p3)
    assert exc3.value.status_code == 400
    assert "goal" in exc3.value.detail

    # Default placeholder goal
    p4 = WorkflowPlan(plan_id=valid_id, goal="No goal description provided", steps=[valid_step])
    with pytest.raises(HTTPException) as exc4:
        validate_plan(p4)
    assert exc4.value.status_code == 400
    assert "goal" in exc4.value.detail

    # Empty steps
    p5 = WorkflowPlan(plan_id=valid_id, goal="Valid goal", steps=[])
    with pytest.raises(HTTPException) as exc5:
        validate_plan(p5)
    assert exc5.value.status_code == 400
    assert "steps" in exc5.value.detail

    # Valid four-step plan succeeds
    four_step_plan = WorkflowPlan(
        plan_id=valid_id,
        goal="Comprehensive student analysis",
        steps=[
            WorkflowStep(step_number=1, tool="csv_inspector", description="Step 1", args={"file_id": _FILE_ID}),
            WorkflowStep(step_number=2, tool="csv_analyzer", description="Step 2", args={"file_id": _FILE_ID, "value_column": "Marks"}),
            WorkflowStep(step_number=3, tool="report_generator", description="Step 3", args={"file_id": _FILE_ID, "title": "Report"}),
            WorkflowStep(step_number=4, tool="artifact_verifier", description="Step 4", args={}),
        ],
    )
    validate_plan(four_step_plan)  # Does not raise
