"""Tests for the Gemini-based planner with retries and fallback — Google Gen AI SDK is fully mocked."""
import asyncio
import json
import pytest
from unittest.mock import patch, MagicMock, call
from google.genai import errors

from api.models.plan import WorkflowPlan
from agent.planner import (
    create_plan,
    is_retryable_error,
    PlannerUnavailableError,
    PlannerPermanentError,
)
from agent.validator import validate_plan
from fastapi import HTTPException

_VALID_PLAN_JSON = {
    "goal": "Analyse top products by sales",
    "steps": [
        {
            "step_number": 1,
            "tool": "csv_inspector",
            "description": "Inspect the CSV schema",
            "args": {"file_id": "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"},
        },
        {
            "step_number": 2,
            "tool": "csv_analyzer",
            "description": "Analyse sales by product",
            "args": {
                "file_id": "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
                "value_column": "Sales",
                "category_column": "Product",
            },
        },
        {
            "step_number": 3,
            "tool": "report_generator",
            "description": "Generate Markdown report",
            "args": {
                "file_id": "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
                "title": "Sales Analysis Report",
            },
        },
        {
            "step_number": 4,
            "tool": "artifact_verifier",
            "description": "Verify the report exists",
            "args": {},
        },
    ],
}

_INSPECTION = {
    "file_id": "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
    "shape": {"rows": 7, "columns": 4},
    "columns": [
        {"name": "Product", "dtype": "object", "null_count": 0, "unique_count": 3, "sample_values": ["Widget A"]},
        {"name": "Sales", "dtype": "float64", "null_count": 0, "unique_count": 7, "sample_values": [1500.0]},
    ],
    "numeric_columns": ["Sales"],
    "date_columns": ["Date"],
    "category_columns": ["Product", "Region"],
    "sample_rows": [],
}


def _setup_mock_genai(mock_genai, text_content: str):
    mock_response = MagicMock()
    mock_response.text = text_content
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    mock_genai.Client.return_value = mock_client
    return mock_client


# ─── Baseline Planner Tests ───────────────────────────────────────────────────

@patch("agent.planner.genai")
def test_create_plan_returns_workflow_plan(mock_genai):
    _setup_mock_genai(mock_genai, json.dumps(_VALID_PLAN_JSON))

    plan = create_plan(
        file_id="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
        instruction="Analyse top products by sales",
        inspection_results=_INSPECTION,
    )
    assert isinstance(plan, WorkflowPlan)
    assert plan.goal == "Analyse top products by sales"


@patch("agent.planner.genai")
def test_plan_contains_four_steps(mock_genai):
    _setup_mock_genai(mock_genai, json.dumps(_VALID_PLAN_JSON))

    plan = create_plan(
        file_id="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
        instruction="Analyse top products by sales",
        inspection_results=_INSPECTION,
    )
    assert len(plan.steps) == 4


@patch("agent.planner.genai")
def test_plan_has_correct_tool_sequence(mock_genai):
    _setup_mock_genai(mock_genai, json.dumps(_VALID_PLAN_JSON))

    plan = create_plan(
        file_id="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
        instruction="Analyse top products by sales",
        inspection_results=_INSPECTION,
    )
    tools = [s.tool for s in sorted(plan.steps, key=lambda s: s.step_number)]
    assert tools == ["csv_inspector", "csv_analyzer", "report_generator", "artifact_verifier"]


@patch("agent.planner.genai")
def test_planner_raises_on_invalid_json(mock_genai):
    _setup_mock_genai(mock_genai, "this is not JSON at all }{{")

    with pytest.raises(ValueError, match="invalid JSON"):
        create_plan(
            file_id="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
            instruction="Analyse top products",
            inspection_results=_INSPECTION,
        )


@patch("agent.planner.genai")
def test_planner_handles_markdown_code_fences(mock_genai):
    fenced_json = f"```json\n{json.dumps(_VALID_PLAN_JSON)}\n```"
    _setup_mock_genai(mock_genai, fenced_json)

    plan = create_plan(
        file_id="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
        instruction="Analyse top products",
        inspection_results=_INSPECTION,
    )
    assert isinstance(plan, WorkflowPlan)
    assert len(plan.steps) == 4


@patch("agent.planner.genai")
def test_planner_uses_gemini_3_8_flash_model(mock_genai):
    """Confirm the planner calls Gemini with the configured gemini-3.8-flash model."""
    mock_client = _setup_mock_genai(mock_genai, json.dumps(_VALID_PLAN_JSON))

    from core.config import settings
    assert settings.gemini_model == "gemini-3.8-flash"

    create_plan(
        file_id="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
        instruction="Analyse top products by sales",
        inspection_results=_INSPECTION,
    )

    mock_client.models.generate_content.assert_called_once()
    call_kwargs = mock_client.models.generate_content.call_args.kwargs
    assert call_kwargs["model"] == "gemini-3.8-flash"


# ─── Objective 4: Reliability, Retries & Fallback Tests ────────────────────────

@patch("agent.planner.genai")
def test_primary_model_succeeds(mock_genai):
    """1. Primary model succeeds on first attempt without sleep or fallback."""
    mock_client = _setup_mock_genai(mock_genai, json.dumps(_VALID_PLAN_JSON))

    with patch("agent.planner.time.sleep") as mock_sleep:
        plan = create_plan(
            file_id="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
            instruction="Analyse top products",
            inspection_results=_INSPECTION,
        )

    assert isinstance(plan, WorkflowPlan)
    assert mock_client.models.generate_content.call_count == 1
    assert mock_client.models.generate_content.call_args.kwargs["model"] == "gemini-3.8-flash"
    mock_sleep.assert_not_called()


@patch("agent.planner.genai")
def test_primary_model_503_succeeds_on_retry(mock_genai):
    """2. Primary model returns 503 and succeeds on retry 1 with 2s exponential backoff."""
    mock_response = MagicMock()
    mock_response.text = json.dumps(_VALID_PLAN_JSON)

    mock_client = MagicMock()
    err_503 = errors.ServerError(503, {"error": {"code": 503, "message": "High demand, please retry"}})
    mock_client.models.generate_content.side_effect = [err_503, mock_response]
    mock_genai.Client.return_value = mock_client

    with patch("agent.planner.time.sleep") as mock_sleep:
        plan = create_plan(
            file_id="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
            instruction="Analyse top products",
            inspection_results=_INSPECTION,
        )

    assert isinstance(plan, WorkflowPlan)
    assert mock_client.models.generate_content.call_count == 2
    # Both calls target primary model
    for call_item in mock_client.models.generate_content.call_args_list:
        assert call_item.kwargs["model"] == "gemini-3.8-flash"
    # Backoff delay of 2.0s was used on first retry
    mock_sleep.assert_called_once_with(2.0)


@patch("agent.planner.genai")
def test_primary_model_504_once_then_succeeds_on_single_retry(mock_genai):
    mock_response = MagicMock(text=json.dumps(_VALID_PLAN_JSON))
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = [
        errors.ServerError(504, {"error": {"code": 504, "status": "DEADLINE_EXCEEDED"}}),
        mock_response,
    ]
    mock_genai.Client.return_value = mock_client

    with (
        patch("agent.planner.settings.gemini_model", "primary-test"),
        patch("agent.planner.settings.gemini_fallback_model", "fallback-test"),
        patch("agent.planner.time.sleep") as mock_sleep,
    ):
        plan = create_plan("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", "Analyze sales", _INSPECTION)

    assert isinstance(plan, WorkflowPlan)
    assert [c.kwargs["model"] for c in mock_client.models.generate_content.call_args_list] == [
        "primary-test", "primary-test"
    ]
    mock_sleep.assert_called_once_with(2.0)


@patch("agent.planner.genai")
def test_primary_model_504_twice_then_fallback_succeeds(mock_genai):
    mock_response = MagicMock(text=json.dumps(_VALID_PLAN_JSON))
    deadline_error = errors.ServerError(
        504, {"error": {"code": 504, "status": "DEADLINE_EXCEEDED"}}
    )
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = [deadline_error, deadline_error, mock_response]
    mock_genai.Client.return_value = mock_client

    with (
        patch("agent.planner.settings.gemini_model", "primary-test"),
        patch("agent.planner.settings.gemini_fallback_model", "fallback-test"),
        patch("agent.planner.time.sleep") as mock_sleep,
    ):
        plan = create_plan("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", "Analyze sales", _INSPECTION)

    assert isinstance(plan, WorkflowPlan)
    assert [c.kwargs["model"] for c in mock_client.models.generate_content.call_args_list] == [
        "primary-test", "primary-test", "fallback-test"
    ]
    mock_sleep.assert_called_once_with(2.0)


@patch("agent.planner.genai")
def test_both_models_returning_504_are_bounded_and_report_temporary_unavailability(mock_genai):
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = errors.ServerError(
        504,
        {"error": {"code": 504, "status": "DEADLINE_EXCEEDED", "message": "provider detail"}},
    )
    mock_genai.Client.return_value = mock_client

    with (
        patch("agent.planner.settings.gemini_model", "primary-test"),
        patch("agent.planner.settings.gemini_fallback_model", "fallback-test"),
        patch("agent.planner.time.sleep") as mock_sleep,
        pytest.raises(PlannerUnavailableError) as exc_info,
    ):
        create_plan("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", "Analyze sales", _INSPECTION)

    assert [c.kwargs["model"] for c in mock_client.models.generate_content.call_args_list] == [
        "primary-test", "primary-test", "fallback-test", "fallback-test"
    ]
    assert mock_sleep.call_count == 2
    assert "temporarily unavailable" in str(exc_info.value)
    assert "HTTP 504 DEADLINE_EXCEEDED" in str(exc_info.value)
    assert "provider detail" not in str(exc_info.value)


@patch("agent.planner.genai")
def test_504_does_not_call_an_unconfigured_fallback(mock_genai):
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = errors.ServerError(
        504, {"error": {"code": 504, "status": "DEADLINE_EXCEEDED"}}
    )
    mock_genai.Client.return_value = mock_client

    with (
        patch("agent.planner.settings.gemini_model", "primary-test"),
        patch("agent.planner.settings.gemini_fallback_model", ""),
        patch("agent.planner.time.sleep"),
        pytest.raises(PlannerUnavailableError, match="HTTP 504 DEADLINE_EXCEEDED"),
    ):
        create_plan("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", "Analyze sales", _INSPECTION)

    assert mock_client.models.generate_content.call_count == 2
    assert all(
        c.kwargs["model"] == "primary-test"
        for c in mock_client.models.generate_content.call_args_list
    )


@patch("agent.planner.genai")
def test_non_504_retryable_errors_keep_the_existing_retry_budget(mock_genai):
    mock_response = MagicMock(text=json.dumps(_VALID_PLAN_JSON))
    mock_client = MagicMock()
    error_503 = errors.ServerError(503, {"error": {"code": 503, "message": "Unavailable"}})
    mock_client.models.generate_content.side_effect = [error_503, error_503, error_503, mock_response]
    mock_genai.Client.return_value = mock_client

    with (
        patch("agent.planner.settings.gemini_model", "primary-test"),
        patch("agent.planner.settings.gemini_fallback_model", "fallback-test"),
        patch("agent.planner.time.sleep") as mock_sleep,
    ):
        plan = create_plan("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", "Analyze sales", _INSPECTION)

    assert isinstance(plan, WorkflowPlan)
    assert mock_client.models.generate_content.call_count == 4
    assert [call.args[0] for call in mock_sleep.call_args_list] == [2.0, 4.0, 8.0]


@patch("agent.planner.genai")
def test_504_retry_stops_when_overall_deadline_leaves_no_request_budget(mock_genai):
    mock_client = MagicMock()
    deadline_error = errors.ServerError(
        504, {"error": {"code": 504, "status": "DEADLINE_EXCEEDED"}}
    )
    clock = [0.0]
    calls = [0]

    def generate(*args, **kwargs):
        calls[0] += 1
        clock[0] += 40.0 if calls[0] == 1 else 10.0
        raise deadline_error

    def sleep(seconds):
        clock[0] += seconds

    mock_client.models.generate_content.side_effect = generate
    mock_genai.Client.return_value = mock_client

    with (
        patch("agent.planner.settings.gemini_model", "primary-test"),
        patch("agent.planner.settings.gemini_fallback_model", "fallback-test"),
        patch("agent.planner.time.time", side_effect=lambda: clock[0]),
        patch("agent.planner.time.sleep", side_effect=sleep) as mock_sleep,
        pytest.raises(PlannerUnavailableError),
    ):
        create_plan(
            "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
            "Analyze sales",
            _INSPECTION,
            total_deadline_seconds=55.0,
        )

    assert mock_client.models.generate_content.call_count == 2
    assert mock_sleep.call_args_list == [call(2.0)]
    assert clock[0] < 55.0


@patch("agent.planner.genai")
def test_cancellation_during_504_backoff_stops_retries_and_fallback(mock_genai):
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = errors.ServerError(
        504, {"error": {"code": 504, "status": "DEADLINE_EXCEEDED"}}
    )
    mock_genai.Client.return_value = mock_client

    with (
        patch("agent.planner.settings.gemini_model", "primary-test"),
        patch("agent.planner.settings.gemini_fallback_model", "fallback-test"),
        patch("agent.planner.time.sleep", side_effect=asyncio.CancelledError),
        pytest.raises(asyncio.CancelledError),
    ):
        create_plan("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", "Analyze sales", _INSPECTION)

    assert mock_client.models.generate_content.call_count == 1


@patch("agent.planner.genai")
def test_primary_model_unavailable_fallback_succeeds(mock_genai):
    """3. Primary model exhausts 3 retries (503), then fallback model succeeds."""
    mock_response = MagicMock()
    mock_response.text = json.dumps(_VALID_PLAN_JSON)

    mock_client = MagicMock()

    def generate_side_effect(model, contents, config):
        if model == "gemini-3.8-flash":
            raise errors.ServerError(503, {"error": {"code": 503, "message": "High demand"}})
        elif model == "gemini-3.1-flash-lite":
            return mock_response
        raise ValueError(f"Unexpected model: {model}")

    mock_client.models.generate_content.side_effect = generate_side_effect
    mock_genai.Client.return_value = mock_client

    with patch("agent.planner.time.sleep") as mock_sleep:
        plan = create_plan(
            file_id="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
            instruction="Analyse top products",
            inspection_results=_INSPECTION,
        )

    assert isinstance(plan, WorkflowPlan)
    # Primary called 4 times (1 initial + 3 retries), fallback called 1 time
    calls = mock_client.models.generate_content.call_args_list
    assert len(calls) == 5
    primary_calls = [c for c in calls if c.kwargs["model"] == "gemini-3.8-flash"]
    fallback_calls = [c for c in calls if c.kwargs["model"] == "gemini-3.1-flash-lite"]
    assert len(primary_calls) == 4
    assert len(fallback_calls) == 1

    # Backoff delays 2.0, 4.0, 8.0 seconds for primary model
    mock_sleep.assert_has_calls([call(2.0), call(4.0), call(8.0)])


@patch("agent.planner.genai")
def test_both_models_fail(mock_genai):
    """4. Both primary and fallback models fail with 503, raising PlannerUnavailableError."""
    mock_client = MagicMock()
    err_503 = errors.ServerError(503, {"error": {"code": 503, "message": "Service unavailable"}})
    mock_client.models.generate_content.side_effect = err_503
    mock_genai.Client.return_value = mock_client

    with patch("agent.planner.time.sleep") as mock_sleep:
        with pytest.raises(PlannerUnavailableError) as exc_info:
            create_plan(
                file_id="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
                instruction="Analyse top products",
                inspection_results=_INSPECTION,
            )

    assert "failed after retries" in str(exc_info.value)
    # Both models attempted (4 calls each = 8 total)
    assert mock_client.models.generate_content.call_count == 8
    # 3 sleeps for primary + 3 sleeps for fallback = 6 sleeps
    assert mock_sleep.call_count == 6


@patch("agent.planner.genai")
def test_invalid_api_key_does_not_trigger_retries(mock_genai):
    """5. Invalid API key fails immediately without retrying or calling fallback."""
    mock_client = MagicMock()
    err_auth = errors.ClientError(400, {"error": {"code": 400, "message": "API_KEY_INVALID"}})
    mock_client.models.generate_content.side_effect = err_auth
    mock_genai.Client.return_value = mock_client

    with patch("agent.planner.time.sleep") as mock_sleep:
        with pytest.raises(PlannerPermanentError) as exc_info:
            create_plan(
                file_id="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
                instruction="Analyse top products",
                inspection_results=_INSPECTION,
            )

    assert "Permanent error" in str(exc_info.value)
    assert mock_client.models.generate_content.call_count == 1
    mock_sleep.assert_not_called()


@patch("agent.planner.genai")
def test_invalid_model_does_not_trigger_retries(mock_genai):
    """6. Invalid model (404) fails immediately without retrying."""
    mock_client = MagicMock()
    err_404 = errors.ClientError(404, {"error": {"code": 404, "message": "models/foo is not found"}})
    mock_client.models.generate_content.side_effect = err_404
    mock_genai.Client.return_value = mock_client

    with patch("agent.planner.time.sleep") as mock_sleep:
        with pytest.raises(PlannerPermanentError) as exc_info:
            create_plan(
                file_id="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
                instruction="Analyse top products",
                inspection_results=_INSPECTION,
            )

    assert "Permanent error" in str(exc_info.value)
    assert mock_client.models.generate_content.call_count == 1
    mock_sleep.assert_not_called()


@patch("agent.planner.genai")
def test_invalid_generated_plans_are_rejected(mock_genai):
    """7. Server-side validation rejects invalid generated plans."""
    # Case A: Model returns malformed JSON
    mock_client = _setup_mock_genai(mock_genai, "bad json response")
    with patch("agent.planner.time.sleep") as mock_sleep:
        with pytest.raises(PlannerPermanentError, match="invalid JSON"):
            create_plan(
                file_id="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
                instruction="Analyse top products",
                inspection_results=_INSPECTION,
            )
    assert mock_client.models.generate_content.call_count == 1
    mock_sleep.assert_not_called()

    # Case B: Model returns unallowed tool name
    bad_tool_plan = WorkflowPlan(
        goal="Exploit system",
        steps=[
            {
                "step_number": 1,
                "tool": "malicious_eval_tool",
                "description": "Execute code",
                "args": {"cmd": "rm -rf"},
            }
        ],
    )
    with pytest.raises(HTTPException) as exc_info:
        validate_plan(bad_tool_plan)
    assert exc_info.value.status_code == 400
    assert "not in the allowlist" in exc_info.value.detail

    # Case C: Model returns tool missing required args
    missing_args_plan = WorkflowPlan(
        goal="Missing args",
        steps=[
            {
                "step_number": 1,
                "tool": "csv_analyzer",
                "description": "Analyze without required value_column",
                "args": {"file_id": "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"},
            }
        ],
    )
    with pytest.raises(HTTPException) as exc_info:
        validate_plan(missing_args_plan)
    assert exc_info.value.status_code == 400
    assert "missing required args" in exc_info.value.detail


@patch("agent.planner.genai")
def test_planner_generates_correlation_step(mock_genai):
    """Phase 2: Planner explicitly configures correlation_column in csv_analyzer step."""
    plan_with_corr = {
        "goal": "Analyze correlation between Study_Hours and Marks",
        "steps": [
            {
                "step_number": 1,
                "tool": "csv_inspector",
                "description": "Inspect schema",
                "args": {"file_id": "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"},
            },
            {
                "step_number": 2,
                "tool": "csv_analyzer",
                "description": "Analyze marks and correlation with study hours",
                "args": {
                    "file_id": "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
                    "value_column": "Marks",
                    "correlation_column": "Study_Hours",
                },
            },
            {
                "step_number": 3,
                "tool": "report_generator",
                "description": "Generate report with correlation",
                "args": {"file_id": "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", "title": "Correlation Report"},
            },
            {
                "step_number": 4,
                "tool": "artifact_verifier",
                "description": "Verify report completeness",
                "args": {},
            },
        ],
    }
    _setup_mock_genai(mock_genai, json.dumps(plan_with_corr))

    plan = create_plan(
        file_id="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
        instruction="Calculate Pearson correlation between Study_Hours and Marks",
        inspection_results=_INSPECTION,
    )
    assert plan.steps[1].tool == "csv_analyzer"
    assert plan.steps[1].args["correlation_column"] == "Study_Hours"
    assert plan.steps[1].args["value_column"] == "Marks"


@patch("agent.planner.genai")
def test_timeout_is_recognized_as_retryable_and_falls_back(mock_genai):
    """Network/request timeouts are classified as temporary and trigger retries and fallback."""
    mock_client = MagicMock()
    # Primary model times out, fallback model succeeds
    mock_response = MagicMock()
    mock_response.text = json.dumps(_VALID_PLAN_JSON)

    mock_client.models.generate_content.side_effect = [
        Exception("Request timed out (read timeout=20)"),
        Exception("Request timed out (read timeout=20)"),
        mock_response,
    ]
    mock_genai.Client.return_value = mock_client

    with patch("agent.planner.time.sleep") as mock_sleep:
        plan = create_plan(
            file_id="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
            instruction="Analyse top products",
            inspection_results=_INSPECTION,
            backoff_delays=[0.01],
        )

    assert isinstance(plan, WorkflowPlan)
    assert mock_client.models.generate_content.call_count == 3
    mock_sleep.assert_called()


@patch("agent.planner.genai")
def test_maximum_retry_budget_remains_below_frontend_timeout(mock_genai):
    """
    Verify that the planner's total retry budget is bounded by deadline
    and finishes well below the frontend's 90-second Axios timeout.
    """
    mock_client = MagicMock()
    # Always raise a temporary 503 error
    mock_client.models.generate_content.side_effect = Exception("503 Service Unavailable")
    mock_genai.Client.return_value = mock_client

    # Test with simulated time progression to verify deadline enforcement
    simulated_times = [0.0, 15.0, 30.0, 45.0, 60.0, 66.0, 70.0, 80.0]
    time_idx = 0

    def mock_time():
        nonlocal time_idx
        t = simulated_times[min(time_idx, len(simulated_times) - 1)]
        time_idx += 1
        return t

    with patch("agent.planner.time.time", side_effect=mock_time):
        with patch("agent.planner.time.sleep") as mock_sleep:
            with pytest.raises(PlannerUnavailableError) as exc_info:
                create_plan(
                    file_id="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
                    instruction="Analyse top products",
                    inspection_results=_INSPECTION,
                    total_deadline_seconds=55.0,
                )

    assert "All configured models" in str(exc_info.value)
    # Ensure sleep did not exceed remaining budget
    for call_args in mock_sleep.call_args_list:
        delay = call_args[0][0]
        assert delay <= 55.0


@patch("agent.planner.genai")
def test_primary_and_fallback_model_both_timeout(mock_genai):
    """Verify that when both primary and fallback models timeout, it fails cleanly within deadline."""
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = Exception("DeadlineExceeded (timeout)")
    mock_genai.Client.return_value = mock_client

    with patch("agent.planner.time.sleep") as mock_sleep:
        with pytest.raises(PlannerUnavailableError) as exc_info:
            create_plan(
                file_id="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
                instruction="Analyse top products",
                inspection_results=_INSPECTION,
                backoff_delays=[0.01, 0.02],
                total_deadline_seconds=55.0,
            )

    assert "All configured models" in str(exc_info.value)
    # Both models should be attempted
    assert mock_client.models.generate_content.call_count >= 2


@patch("agent.planner.genai")
def test_gemini_timeout_never_sent_below_10_seconds(mock_genai):
    """
    Requirement: Ensure no Gemini API request is sent with a manually configured
    timeout below 10 seconds (10000ms), and if remaining deadline is < 10s,
    retries stop immediately without sending an invalid request.
    """
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = Exception("503 Service Unavailable")
    mock_genai.Client.return_value = mock_client

    # Simulate start_time = 0.0, first attempt at t=0, error at t=42 (remaining=13s >= 10s),
    # second attempt at t=48 (remaining=7s < 10s)
    simulated_times = [0.0, 42.0, 48.0, 49.0, 50.0]
    time_idx = 0

    def mock_time():
        nonlocal time_idx
        t = simulated_times[min(time_idx, len(simulated_times) - 1)]
        time_idx += 1
        return t

    with patch("agent.planner.time.time", side_effect=mock_time):
        with patch("agent.planner.time.sleep") as mock_sleep:
            with pytest.raises(PlannerUnavailableError):
                create_plan(
                    file_id="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
                    instruction="Analyse top products",
                    inspection_results=_INSPECTION,
                    backoff_delays=[1.0, 2.0],
                    total_deadline_seconds=55.0,
                )

    # Verify every call made to generate_content had http_options.timeout >= 10000ms
    assert mock_client.models.generate_content.call_count >= 1
    for call_item in mock_client.models.generate_content.call_args_list:
        cfg = call_item.kwargs.get("config")
        assert cfg is not None
        assert hasattr(cfg, "http_options")
        timeout = getattr(cfg.http_options, "timeout", None)
        if timeout is None and isinstance(cfg.http_options, dict):
            timeout = cfg.http_options.get("timeout")
        assert timeout is not None
        assert timeout >= 10000, f"Gemini request timeout was {timeout}ms, expected >= 10000ms"
