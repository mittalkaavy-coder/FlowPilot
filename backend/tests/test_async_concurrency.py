"""Targeted tests for non-blocking health checks, concurrent safety, and threadpool offloading."""
import asyncio
import time
import uuid
from unittest.mock import patch
import httpx
import pytest

from main import app
from api.models.plan import WorkflowPlan, WorkflowStep
from api.models.execute import ExecuteResponse, StepResult
from core.store import (
    save_plan,
    approve_plan,
    get_plan,
    claim_plan_for_execution,
    update_plan_status,
)


@pytest.mark.asyncio
async def test_health_responds_during_slow_planning():
    """1. Verify GET /health responds while a simulated slow Gemini planning call is running."""
    slow_done = False

    def slow_create_plan(*args, **kwargs):
        nonlocal slow_done
        time.sleep(1.0)  # Blocking sync call
        slow_done = True
        return WorkflowPlan(
            plan_id=str(uuid.uuid4()),
            goal="Test plan",
            steps=[WorkflowStep(step_number=1, tool="csv_inspector", description="Inspect", args={})],
        )

    file_id = "11111111-1111-4111-8111-111111111111"

    with (
        patch("api.routes.plan.inspect_csv", return_value={"shape": {"rows": 10, "columns": 2}}),
        patch("api.routes.plan.create_plan", side_effect=slow_create_plan),
        patch("api.routes.plan.validate_plan"),
        patch("api.routes.plan.save_plan"),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            # Start planning in background task
            plan_task = asyncio.create_task(
                ac.post("/api/plan", json={"file_id": file_id, "instruction": "Do slow analysis"})
            )

            # Wait briefly to ensure plan request has begun running on threadpool
            await asyncio.sleep(0.1)

            # Hit /health while planning is actively in progress
            t0 = time.time()
            health_resp = await ac.get("/health")
            duration = time.time() - t0

            # Health check must respond immediately (not blocked by the 1.0s planning task)
            assert health_resp.status_code == 200
            assert health_resp.json() == {"status": "ok", "version": "1.0.0"}
            assert duration < 0.5, f"Health check took {duration:.2f}s, event loop was blocked!"

            plan_resp = await plan_task
            assert plan_resp.status_code == 200
            assert slow_done is True


@pytest.mark.asyncio
async def test_health_responds_during_slow_execution():
    """2. Verify GET /health responds while a simulated slow execution operation is running."""
    plan_id = str(uuid.uuid4())
    mock_plan = WorkflowPlan(
        plan_id=plan_id,
        goal="Slow execution goal",
        steps=[WorkflowStep(step_number=1, tool="csv_inspector", description="Inspect", args={})],
    )
    save_plan(mock_plan, "11111111-1111-4111-8111-111111111111")
    assert approve_plan(plan_id)[0] is True

    def slow_execute_plan(*args, **kwargs):
        time.sleep(1.0)  # Blocking sync call
        return ExecuteResponse(
            plan_id=plan_id,
            goal="Slow execution goal",
            steps=[StepResult(step_number=1, tool="csv_inspector", description="Inspect", status="success", output={})],
            report_id=str(uuid.uuid4()),
            verified=True,
            summary="Completed",
        )

    with (
        patch("api.routes.execute.validate_plan"),
        patch("api.routes.execute.execute_plan", side_effect=slow_execute_plan),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            # Start execution task
            exec_task = asyncio.create_task(
                ac.post("/api/execute", json={"plan_id": plan_id})
            )

            await asyncio.sleep(0.1)

            # Hit /health while execution is in progress
            t0 = time.time()
            health_resp = await ac.get("/health")
            duration = time.time() - t0

            assert health_resp.status_code == 200
            assert health_resp.json()["status"] == "ok"
            assert duration < 0.5, f"Health check took {duration:.2f}s, event loop was blocked!"

            exec_resp = await exec_task
            assert exec_resp.status_code == 200
            assert exec_resp.json()["verified"] is True


@pytest.mark.asyncio
async def test_api_plan_returns_503_on_async_timeout():
    """Verify that /api/plan cleanly returns HTTP 503 if planning exceeds the deadline."""
    def block_long(*args, **kwargs):
        time.sleep(1.0)
        return None

    async def timeout_before_start(awaitable, timeout):
        # The mocked wait_for intentionally does not start the operation. Close
        # the coroutine it receives, as real wait_for owns and cancels it when
        # a timeout occurs, so the test does not leave an unawaited coroutine.
        awaitable.close()
        raise asyncio.TimeoutError()

    with (
        patch("api.routes.plan.inspect_csv", return_value={"shape": {"rows": 5, "columns": 2}}),
        patch("api.routes.plan.create_plan", side_effect=block_long),
        patch("api.routes.plan.asyncio.wait_for", new=timeout_before_start),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                "/api/plan",
                json={"file_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "instruction": "Test timeout"},
            )
            assert r.status_code == 503
            assert "Workflow planning timed out" in r.json()["detail"]


@pytest.mark.asyncio
async def test_concurrent_sqlite_safety_and_replay_protection():
    """4 & 5. Verify SQLite operations remain safe under concurrent requests and replay protection holds."""
    plan_id = str(uuid.uuid4())
    mock_plan = WorkflowPlan(
        plan_id=plan_id,
        goal="Concurrent claim test",
        steps=[WorkflowStep(step_number=1, tool="csv_inspector", description="Inspect", args={})],
    )
    save_plan(mock_plan, "22222222-2222-4222-8222-222222222222")
    assert approve_plan(plan_id)[0] is True

    # Launch 5 concurrent claims for the same plan_id
    loop = asyncio.get_running_loop()
    tasks = [
        loop.run_in_executor(None, claim_plan_for_execution, plan_id)
        for _ in range(5)
    ]
    results = await asyncio.gather(*tasks)

    # Exactly ONE claim must succeed; the other 4 must fail with already executing status
    claimed_flags = [res[0] for res in results]
    assert claimed_flags.count(True) == 1, f"Expected exactly 1 claim, got {claimed_flags.count(True)}"
    assert claimed_flags.count(False) == 4

    # Verify plan status in SQLite is now 'executing'
    stored = get_plan(plan_id)
    assert stored.status == "executing"

    # Verify duplicate execution returns 409
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        dup_resp = await ac.post("/api/execute", json={"plan_id": plan_id})
        assert dup_resp.status_code == 409
        assert "cannot be executed because its current status is 'executing'" in dup_resp.json()["detail"]
