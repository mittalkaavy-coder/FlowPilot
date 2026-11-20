from fastapi import HTTPException
from tools.registry import TOOL_REGISTRY, REQUIRED_ARGS
from api.models.plan import WorkflowPlan
from core.security import validate_uuid


def validate_plan(plan: WorkflowPlan) -> None:
    """Validate plan fields, allowlisted tools, args, and required workflow order."""
    if not plan or not isinstance(plan, WorkflowPlan):
        raise HTTPException(400, "Invalid plan object")

    if not plan.plan_id or not str(plan.plan_id).strip() or str(plan.plan_id).strip().upper() == "N/A":
        raise HTTPException(400, "Plan must have a valid plan_id")

    validate_uuid(str(plan.plan_id).strip(), "plan_id")

    if not plan.goal or not str(plan.goal).strip() or str(plan.goal).strip().lower() == "no goal description provided":
        raise HTTPException(400, "Plan must have a non-empty goal description")

    if not plan.steps or len(plan.steps) == 0:
        raise HTTPException(400, "Plan has no steps")

    seen_numbers: set = set()
    ordered_steps = sorted(plan.steps, key=lambda step: step.step_number)
    for step in ordered_steps:
        # Tool allowlist check
        if step.tool not in TOOL_REGISTRY:
            raise HTTPException(
                400,
                f"Step {step.step_number}: Tool '{step.tool}' is not in the allowlist. "
                f"Allowed tools: {list(TOOL_REGISTRY.keys())}",
            )

        # Required args check
        required = REQUIRED_ARGS.get(step.tool, set())
        missing = required - set(step.args.keys())
        if missing:
            raise HTTPException(
                400,
                f"Step {step.step_number}: Tool '{step.tool}' missing required args: {missing}",
            )

        # Duplicate step numbers
        if step.step_number in seen_numbers:
            raise HTTPException(400, f"Duplicate step number: {step.step_number}")
        seen_numbers.add(step.step_number)

        # Validate step_number is positive
        if step.step_number < 1:
            raise HTTPException(400, f"Step number must be >= 1, got {step.step_number}")

    required_sequence = [
        "csv_inspector",
        "csv_analyzer",
        "report_generator",
        "artifact_verifier",
    ]
    actual_sequence = [step.tool for step in ordered_steps]
    if actual_sequence != required_sequence:
        raise HTTPException(
            400,
            "Workflow must contain exactly one step of each required tool in this order: "
            "csv_inspector, csv_analyzer, report_generator, artifact_verifier.",
        )
