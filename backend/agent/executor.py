from tools.registry import TOOL_REGISTRY
from api.models.plan import WorkflowPlan
from api.models.execute import StepResult, ExecuteResponse
from agent.validator import validate_plan


def execute_plan(plan: WorkflowPlan) -> ExecuteResponse:
    validate_plan(plan)
    context: dict = {}
    step_results: list[StepResult] = []

    for step in sorted(plan.steps, key=lambda s: s.step_number):
        args = dict(step.args)
        args = _inject_context(step.tool, args, context, plan)

        try:
            output = TOOL_REGISTRY[step.tool](**args)
            if step.tool == "artifact_verifier" and (
                not isinstance(output, dict) or output.get("verified") is not True
            ):
                status = "error"
                error = (
                    output.get("reason", "Artifact verification failed")
                    if isinstance(output, dict)
                    else "Artifact verifier returned an invalid result"
                )
            else:
                status = "success"
                error = None
        except Exception as e:
            output = {}
            status = "error"
            error = str(e)

        result = StepResult(
            step_number=step.step_number,
            tool=step.tool,
            description=step.description,
            status=status,
            output=output,
            error=error,
        )
        step_results.append(result)

        if status == "success":
            context[f"step_{step.step_number}"] = output
            _update_context(step.tool, output, context)
        else:
            break  # Abort on failure; subsequent steps depend on prior output

    report_id = context.get("report_id")
    verification = context.get("verification", {})
    success = (
        all(s.status == "success" for s in step_results)
        and verification.get("verified") is True
    )

    return ExecuteResponse(
        plan_id=plan.plan_id,
        goal=plan.goal,
        steps=step_results,
        report_id=report_id,
        verified=verification.get("verified", False),
        summary=_summary(step_results, success),
    )


def _inject_context(
    tool: str, args: dict, context: dict, plan: WorkflowPlan = None
) -> dict:
    if tool == "report_generator":
        if "analysis_results" not in args and "analysis_results" in context:
            args["analysis_results"] = context["analysis_results"]
        if "inspection_results" not in args and "inspection_results" in context:
            args["inspection_results"] = context["inspection_results"]

    if tool == "artifact_verifier":
        if "report_id" not in args and "report_id" in context:
            args["report_id"] = context["report_id"]

        if "expected_requirements" not in args:
            expected: dict = {
                "require_dataset_structure": True,
                "require_missing_values": True,
                "require_statistics": True,
            }
            if "analysis_results" in context:
                ar = context["analysis_results"]
                val_col = ar.get("value_column")
                if val_col:
                    expected["metrics"] = [val_col]
                if ar.get("top_items"):
                    top = ar["top_items"][0]
                    expected["top_performer"] = {
                        "name": top["name"],
                        "value": top["value"],
                    }
                if ar.get("correlation"):
                    corr = ar["correlation"]
                    expected["require_correlation"] = True
                    expected["correlation_columns"] = [
                        corr["column_1"],
                        corr["column_2"],
                    ]
                    if corr.get("coefficient") is not None:
                        expected["correlation_coefficient"] = corr["coefficient"]
                    expected["correlation_interpretation"] = corr.get(
                        "interpretation"
                    )

            if plan and plan.goal:
                goal_lower = plan.goal.lower()
                if "correlation" in goal_lower or "pearson" in goal_lower:
                    expected["require_correlation"] = True
                expected["goal"] = plan.goal

            args["expected_requirements"] = expected

    return args


def _update_context(tool: str, output: dict, context: dict) -> None:
    if tool == "csv_inspector":
        context["inspection_results"] = output
    elif tool == "csv_analyzer":
        context["analysis_results"] = output
    elif tool == "report_generator":
        if "report_id" in output:
            context["report_id"] = output["report_id"]
    elif tool == "artifact_verifier":
        context["verification"] = output


def _summary(steps: list, success: bool) -> str:
    n = len(steps)
    done = sum(1 for s in steps if s.status == "success")
    if success:
        return f"Workflow completed successfully. All {n} steps executed."
    if n and done == n:
        return "Workflow failed because artifact verification did not succeed."
    return f"Workflow failed at step {done + 1}/{n}. See step details for the error."
