import asyncio
from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from api.models.plan import (
    PlanApprovalRequest,
    PlanApprovalResponse,
    PlanRecoveryResponse,
    PlanRequest,
    PlanResponse,
)
from agent.planner import create_plan, PlannerUnavailableError, PlannerPermanentError
from agent.validator import validate_plan
from tools.csv_inspector import run as inspect_csv
from core.store import approve_plan as approve_stored_plan, get_plan, save_plan
from core.security import sanitize_public_data, sanitize_public_text, validate_uuid
from core.config import settings

router = APIRouter(prefix="/api", tags=["plan"])


@router.get("/workflows/{plan_id}", response_model=PlanRecoveryResponse)
async def get_workflow_state(plan_id: str):
    """Return the persisted state for one plan so its owning tab can recover its UI."""
    validate_uuid(plan_id, "plan_id")
    stored = await run_in_threadpool(get_plan, plan_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="Workflow not found or no longer available.")

    result = stored.result
    if hasattr(result, "model_dump"):
        result = result.model_dump()
    elif hasattr(result, "dict"):
        result = result.dict()
    result = sanitize_public_data(result)

    return PlanRecoveryResponse(
        plan_id=stored.plan_id,
        file_id=stored.file_id,
        goal=stored.goal,
        steps=stored.plan.steps,
        status=stored.status,
        result=result,
        error=sanitize_public_data(stored.error),
    )


@router.post("/approve", response_model=PlanApprovalResponse)
async def approve_workflow(request: PlanApprovalRequest):
    """Persist explicit approval for a plan that is still in the created state."""
    validate_uuid(request.plan_id, "plan_id")
    approved, stored, status = await run_in_threadpool(
        approve_stored_plan, request.plan_id
    )
    if status == "not_found":
        raise HTTPException(
            status_code=404,
            detail=f"Plan '{request.plan_id}' not found. It may have expired or is invalid.",
        )
    if not approved or stored is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Plan '{request.plan_id}' cannot be approved because its current status "
                f"is '{status}'. Only created plans may be approved."
            ),
        )
    return PlanApprovalResponse(plan_id=stored.plan_id, status=stored.status)


@router.post("/plan", response_model=PlanResponse)
async def plan_workflow(request: PlanRequest):
    validate_uuid(request.file_id, "file_id")

    if not request.instruction or not request.instruction.strip():
        raise HTTPException(status_code=400, detail="Instruction cannot be empty")

    # Run inspector in threadpool to avoid blocking event loop
    try:
        inspection = await run_in_threadpool(inspect_csv, request.file_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Could not inspect CSV: {sanitize_public_text(e)}",
        )

    # Run blocking Gemini SDK calls and sleep backoffs in threadpool.
    # Enforce a strict asyncio timeout ceiling of planner_total_deadline_seconds + 3.0s buffer
    # so the HTTP request is guaranteed to return HTTP 503 rather than waiting indefinitely.
    deadline_sec = float(getattr(settings, "planner_total_deadline_seconds", 55.0))
    async_ceiling = deadline_sec + 3.0

    try:
        plan = await asyncio.wait_for(
            run_in_threadpool(
                create_plan, request.file_id, request.instruction.strip(), inspection
            ),
            timeout=async_ceiling,
        )
    except (asyncio.TimeoutError, TimeoutError):
        raise HTTPException(
            status_code=503,
            detail=(
                f"Workflow planning timed out after {int(deadline_sec)}s. All configured models "
                f"({settings.gemini_model}, {settings.gemini_fallback_model}) were unavailable or slow to respond. "
                "Please retry with 'Dismiss & Retry'."
            ),
        )
    except PlannerUnavailableError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Gemini service unavailable: {sanitize_public_text(e)}",
        )
    except PlannerPermanentError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Planning request rejected: {sanitize_public_text(e)}",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"AI planning failed: {sanitize_public_text(e)}"
        )

    await run_in_threadpool(validate_plan, plan)
    await run_in_threadpool(save_plan, plan, request.file_id)

    return PlanResponse(plan_id=plan.plan_id, goal=plan.goal, steps=plan.steps)
