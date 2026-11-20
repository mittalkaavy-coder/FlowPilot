from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from api.models.execute import ExecuteRequest, ExecuteResponse
from agent.executor import execute_plan
from agent.validator import validate_plan
from core.store import get_plan, claim_plan_for_execution, update_plan_status
from core.security import sanitize_public_data, sanitize_public_text, validate_uuid

router = APIRouter(prefix="/api", tags=["execute"])


@router.post("/execute", response_model=ExecuteResponse)
async def execute_workflow(request: ExecuteRequest):
    """
    Execute a previously approved workflow plan by plan_id.

    Security & Idempotency:
    - Accepts ONLY plan_id from client.
    - Validates plan_id UUID format upfront.
    - Requires persisted approval before execution.
    - Atomically claims an approved plan using SQLite BEGIN IMMEDIATE.
    - Revalidates the plan against the tool allowlist.
    - Enforces single-execution across backend restarts.
    - Rejects duplicate / replay execution requests with 409 Conflict.
    - Dispatches heavy tool execution to threadpool so the asyncio event loop
      remains free to respond to /health checks without lag or timeouts.
    """
    validate_uuid(request.plan_id, "plan_id")

    # Check plan existence and validate initial status
    stored_check = await run_in_threadpool(get_plan, request.plan_id)
    if stored_check is None:
        raise HTTPException(
            status_code=404,
            detail=f"Plan '{request.plan_id}' not found. It may have expired or is invalid.",
        )

    if stored_check.status != "approved":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Plan '{request.plan_id}' cannot be executed because its current status "
                f"is '{stored_check.status}'. Plans must be explicitly approved before execution."
            ),
        )

    # Perform atomic claim using SQLite transaction with BEGIN IMMEDIATE
    claimed, stored_claimed, status = await run_in_threadpool(
        claim_plan_for_execution, request.plan_id
    )
    if not claimed or stored_claimed is None:
        if status == "not_found":
            raise HTTPException(status_code=404, detail="Approved plan was not found")
        raise HTTPException(
            status_code=409,
            detail=(
                f"Plan '{request.plan_id}' cannot be executed because its current status "
                f"is '{status}'. Duplicate execution requests are rejected."
            ),
        )
    stored = stored_claimed

    # Revalidate stored plan before execution
    try:
        await run_in_threadpool(validate_plan, stored.plan)
    except Exception as exc:
        await run_in_threadpool(update_plan_status, request.plan_id, "failed", error=str(exc))
        raise HTTPException(
            status_code=400,
            detail=f"Plan revalidation failed: {sanitize_public_text(exc)}",
        )

    try:
        result = await run_in_threadpool(execute_plan, stored.plan)
        success = all(s.status == "success" for s in result.steps)
        final_status = "completed" if success else "failed"
        await run_in_threadpool(update_plan_status, request.plan_id, final_status, result=result)
        return sanitize_public_data(result.model_dump())
    except Exception as exc:
        await run_in_threadpool(update_plan_status, request.plan_id, "failed", error=str(exc))
        raise HTTPException(
            status_code=500,
            detail="Execution engine failure. Please contact the service operator.",
        )
