"""Tests for the step executor — all tool functions are mocked."""
import pytest
from unittest.mock import patch, MagicMock
from fastapi import HTTPException

from api.models.plan import WorkflowPlan, WorkflowStep
from api.models.execute import ExecuteResponse

_FILE_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_REPORT_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def _make_plan(steps=None) -> WorkflowPlan:
    if steps is None:
        steps = [
            WorkflowStep(
                step_number=1,
                tool="csv_inspector",
                description="Inspect",
                args={"file_id": _FILE_ID},
            ),
            WorkflowStep(
                step_number=2,
                tool="csv_analyzer",
                description="Analyze",
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
                description="Verify",
                args={},
            ),
        ]
    return WorkflowPlan(goal="Test workflow", steps=steps)


_MOCK_INSPECTOR_OUT = {"shape": {"rows": 7, "columns": 4}, "columns": []}
_MOCK_ANALYZER_OUT = {"statistics": {"total": 10450.0}, "value_column": "Sales"}
_MOCK_REPORT_OUT = {"report_id": _REPORT_ID, "path": f"reports/{_REPORT_ID}.md", "size_bytes": 1024}
_MOCK_VERIFIER_OUT = {"verified": True, "report_id": _REPORT_ID}


@patch("agent.executor.TOOL_REGISTRY", {
    "csv_inspector": MagicMock(return_value=_MOCK_INSPECTOR_OUT),
    "csv_analyzer": MagicMock(return_value=_MOCK_ANALYZER_OUT),
    "report_generator": MagicMock(return_value=_MOCK_REPORT_OUT),
    "artifact_verifier": MagicMock(return_value=_MOCK_VERIFIER_OUT),
})
def test_execute_plan_success():
    from agent.executor import execute_plan
    plan = _make_plan()
    result = execute_plan(plan)
    assert isinstance(result, ExecuteResponse)
    assert result.plan_id == plan.plan_id
    assert result.verified is True
    assert result.report_id == _REPORT_ID
    assert all(s.status == "success" for s in result.steps)


def test_executor_chains_analysis_results():
    """report_generator must receive analysis_results injected from context."""
    captured_args = {}

    def mock_report(**kwargs):
        captured_args.update(kwargs)
        return _MOCK_REPORT_OUT

    registry = {
        "csv_inspector": MagicMock(return_value=_MOCK_INSPECTOR_OUT),
        "csv_analyzer": MagicMock(return_value=_MOCK_ANALYZER_OUT),
        "report_generator": mock_report,
        "artifact_verifier": MagicMock(return_value=_MOCK_VERIFIER_OUT),
    }

    with patch("agent.executor.TOOL_REGISTRY", registry):
        from agent.executor import execute_plan
        execute_plan(_make_plan())

    assert "analysis_results" in captured_args
    assert captured_args["analysis_results"] == _MOCK_ANALYZER_OUT


def test_executor_chains_report_id():
    """artifact_verifier must receive report_id injected from context."""
    captured_args = {}

    def mock_verifier(**kwargs):
        captured_args.update(kwargs)
        return _MOCK_VERIFIER_OUT

    registry = {
        "csv_inspector": MagicMock(return_value=_MOCK_INSPECTOR_OUT),
        "csv_analyzer": MagicMock(return_value=_MOCK_ANALYZER_OUT),
        "report_generator": MagicMock(return_value=_MOCK_REPORT_OUT),
        "artifact_verifier": mock_verifier,
    }

    with patch("agent.executor.TOOL_REGISTRY", registry):
        from agent.executor import execute_plan
        execute_plan(_make_plan())

    assert captured_args.get("report_id") == _REPORT_ID


def test_executor_stops_on_tool_failure():
    """If a step fails the remaining steps must not execute."""
    inspector_calls = []
    analyzer_calls = []

    def failing_inspector(**kwargs):
        inspector_calls.append(kwargs)
        raise RuntimeError("CSV is corrupt")

    def should_not_run(**kwargs):
        analyzer_calls.append(kwargs)
        return _MOCK_ANALYZER_OUT

    registry = {
        "csv_inspector": failing_inspector,
        "csv_analyzer": should_not_run,
        "report_generator": MagicMock(return_value=_MOCK_REPORT_OUT),
        "artifact_verifier": MagicMock(return_value=_MOCK_VERIFIER_OUT),
    }

    with patch("agent.executor.TOOL_REGISTRY", registry):
        from agent.executor import execute_plan
        result = execute_plan(_make_plan())

    assert result.steps[0].status == "error"
    assert len(result.steps) == 1  # halted after first failure
    assert len(analyzer_calls) == 0


def test_executor_summary_on_success():
    registry = {
        "csv_inspector": MagicMock(return_value=_MOCK_INSPECTOR_OUT),
        "csv_analyzer": MagicMock(return_value=_MOCK_ANALYZER_OUT),
        "report_generator": MagicMock(return_value=_MOCK_REPORT_OUT),
        "artifact_verifier": MagicMock(return_value=_MOCK_VERIFIER_OUT),
    }

    with patch("agent.executor.TOOL_REGISTRY", registry):
        from agent.executor import execute_plan
        result = execute_plan(_make_plan())

    assert "successfully" in result.summary.lower()
    assert "4" in result.summary


def test_executor_pipes_expected_requirements_to_verifier():
    """Phase 2: executor automatically injects expected_requirements into artifact_verifier."""
    captured_args = {}

    def mock_verifier(**kwargs):
        captured_args.update(kwargs)
        return _MOCK_VERIFIER_OUT

    analyzer_with_corr = {
        "value_column": "Marks",
        "top_items": [{"name": "Karan", "value": 92.0}],
        "correlation": {
            "column_1": "Marks",
            "column_2": "Study_Hours",
            "coefficient": 0.9721,
            "interpretation": "Strong positive linear correlation",
        },
    }

    registry = {
        "csv_inspector": MagicMock(return_value=_MOCK_INSPECTOR_OUT),
        "csv_analyzer": MagicMock(return_value=analyzer_with_corr),
        "report_generator": MagicMock(return_value=_MOCK_REPORT_OUT),
        "artifact_verifier": mock_verifier,
    }

    with patch("agent.executor.TOOL_REGISTRY", registry):
        from agent.executor import execute_plan
        plan = _make_plan()
        plan.goal = "Analyze student marks and study hours correlation"
        execute_plan(plan)

    assert "expected_requirements" in captured_args
    reqs = captured_args["expected_requirements"]
    assert "Marks" in reqs["metrics"]
    assert reqs["top_performer"]["name"] == "Karan"
    assert reqs["require_correlation"] is True
    assert reqs["correlation_coefficient"] == 0.9721


def test_executor_fails_when_artifact_verifier_returns_unverified():
    """Phase 2: If artifact_verifier fails completeness, step status is error and workflow is failed."""
    verifier_fail_out = {
        "verified": False,
        "reason": "Missing expected correlation metrics",
        "integrity": {"passed": True},
        "completeness": {"passed": False},
    }

    registry = {
        "csv_inspector": MagicMock(return_value=_MOCK_INSPECTOR_OUT),
        "csv_analyzer": MagicMock(return_value=_MOCK_ANALYZER_OUT),
        "report_generator": MagicMock(return_value=_MOCK_REPORT_OUT),
        "artifact_verifier": MagicMock(return_value=verifier_fail_out),
    }

    with patch("agent.executor.TOOL_REGISTRY", registry):
        from agent.executor import execute_plan
        result = execute_plan(_make_plan())

    assert result.verified is False
    assert result.steps[-1].status == "error"
    assert "Missing expected correlation" in result.steps[-1].error
    assert "failed at step 4" in result.summary.lower()


@pytest.mark.parametrize(
    "tools",
    [
        ["csv_analyzer", "report_generator", "artifact_verifier"],
        ["csv_inspector", "csv_analyzer", "artifact_verifier"],
        ["csv_inspector", "csv_analyzer", "report_generator"],
        ["csv_inspector", "report_generator", "csv_analyzer", "artifact_verifier"],
    ],
)
def test_executor_rejects_incomplete_or_misordered_workflow(tools):
    """Invalid workflow sequences are rejected before any tool is executed."""
    args_by_tool = {
        "csv_inspector": {"file_id": _FILE_ID},
        "csv_analyzer": {"file_id": _FILE_ID, "value_column": "Sales"},
        "report_generator": {"file_id": _FILE_ID, "title": "Report"},
        "artifact_verifier": {},
    }
    plan = _make_plan([
        WorkflowStep(
            step_number=index,
            tool=tool,
            description=tool,
            args=args_by_tool[tool],
        )
            for index, tool in enumerate(tools, start=1)
    ])
    registry = {
        name: MagicMock(return_value=output)
        for name, output in {
            "csv_inspector": _MOCK_INSPECTOR_OUT,
            "csv_analyzer": _MOCK_ANALYZER_OUT,
            "report_generator": _MOCK_REPORT_OUT,
            "artifact_verifier": _MOCK_VERIFIER_OUT,
        }.items()
    }

    with patch("agent.executor.TOOL_REGISTRY", registry):
        from agent.executor import execute_plan
        with pytest.raises(HTTPException) as exc_info:
            execute_plan(plan)

    assert exc_info.value.status_code == 400
    assert all(tool.call_count == 0 for tool in registry.values())


def test_executor_does_not_report_success_when_verifier_result_is_missing():
    registry = {
        "csv_inspector": MagicMock(return_value=_MOCK_INSPECTOR_OUT),
        "csv_analyzer": MagicMock(return_value=_MOCK_ANALYZER_OUT),
        "report_generator": MagicMock(return_value=_MOCK_REPORT_OUT),
        "artifact_verifier": MagicMock(return_value={}),
    }

    with patch("agent.executor.TOOL_REGISTRY", registry):
        from agent.executor import execute_plan
        result = execute_plan(_make_plan())

    assert result.verified is False
    assert result.steps[-1].status == "error"
    assert "failed" in result.summary.lower()
    assert "successfully" not in result.summary.lower()
