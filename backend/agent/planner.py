import json
import logging
import time
from typing import List, Optional
from google import genai
from google.genai import types

from core.config import settings
from tools.registry import TOOL_DESCRIPTIONS, REQUIRED_ARGS
from api.models.plan import WorkflowPlan

logger = logging.getLogger(__name__)

DEFAULT_BACKOFF_DELAYS = [2.0, 4.0, 8.0]
DEFAULT_TOTAL_DEADLINE = 55.0  # Finishes comfortably before frontend 90s Axios timeout


class PlannerUnavailableError(Exception):
    """Raised when all configured models are unavailable after retries (HTTP 503 candidate)."""
    pass


class PlannerPermanentError(ValueError):
    """Raised when a non-retryable error occurs (auth, invalid model, malformed request, invalid plan JSON)."""
    pass


def is_retryable_error(exc: Exception) -> bool:
    """
    Determine if an exception represents a temporary error eligible for retry.
    Temporary errors: HTTP 503 (Unavailable), 429 (Resource Exhausted/Rate Limit), 500 (Internal).
    Permanent errors: HTTP 400 (Invalid Argument / Bad Key), 401, 403 (Forbidden), 404 (Model Not Found),
    and local ValueError/JSONDecodeError are NEVER retried.
    """
    if isinstance(exc, (ValueError, json.JSONDecodeError)):
        return False

    code = getattr(exc, "code", None)
    if code is None:
        code = getattr(exc, "status_code", None)
    if code is None and hasattr(exc, "response"):
        code = getattr(exc.response, "status_code", None)

    if code is not None:
        try:
            code_int = int(code)
            if code_int in (504, 503, 429, 500):
                return True
            if code_int in (400, 401, 403, 404):
                return False
        except (ValueError, TypeError):
            pass

    err_str = str(exc).upper()

    # Check for permanent markers first
    permanent_markers = [
        "API_KEY_INVALID",
        "API KEY NOT VALID",
        "NOT ALLOWED BY POLICY",
        "PERMISSION_DENIED",
        "FORBIDDEN",
        "UNAUTHENTICATED",
        "NOT_FOUND",
        "404",
        "INVALID_ARGUMENT",
        "400",
        "401",
        "403",
    ]
    for marker in permanent_markers:
        if marker in err_str:
            return False

    # Check for temporary markers
    temporary_markers = [
        "503",
        "UNAVAILABLE",
        "HIGH DEMAND",
        "OVERLOADED",
        "429",
        "RESOURCE_EXHAUSTED",
        "RATE LIMIT",
        "TOO MANY REQUESTS",
        "500",
        "INTERNAL",
        "TIMEOUT",
        "TIMED OUT",
        "DEADLINE_EXCEEDED",
        "CONNECTION ERROR",
        "CONNECTTIMEOUT",
        "READTIMEOUT",
    ]
    for marker in temporary_markers:
        if marker in err_str:
            return True

    return False


def _is_provider_deadline_error(exc: Exception) -> bool:
    """Identify Gemini HTTP 504 and equivalent DEADLINE_EXCEEDED errors."""
    if isinstance(exc, (ValueError, json.JSONDecodeError)):
        return False

    code = getattr(exc, "code", None)
    if code is None:
        code = getattr(exc, "status_code", None)
    if code is None and hasattr(exc, "response"):
        code = getattr(exc.response, "status_code", None)

    try:
        if int(code) == 504:
            return True
    except (TypeError, ValueError):
        pass

    return "DEADLINE_EXCEEDED" in str(exc).upper()


_SYSTEM_PROMPT = """You are FlowPilot's workflow planner. Given a CSV inspection result and a user instruction, you generate a JSON workflow plan.

RULES:
1. Step 1 MUST always be csv_inspector with the provided file_id.
2. Use REAL column names from the inspection result — never invent column names.
3. The last step MUST always be artifact_verifier.
4. Use only these tools: csv_inspector, csv_analyzer, report_generator, artifact_verifier.
5. csv_analyzer requires: file_id, value_column. Optionally: category_column, date_column, top_n, correlation_column (supply correlation_column whenever correlation, relationship, or dependency between two numeric columns is requested).
6. report_generator requires: file_id, title.
7. artifact_verifier requires no args (report_id is injected automatically).

Return ONLY valid JSON matching this exact schema — no markdown fences, no commentary:
{
  "goal": "string describing what this workflow achieves",
  "steps": [
    {
      "step_number": 1,
      "tool": "csv_inspector",
      "description": "string",
      "args": {"file_id": "the-uuid"}
    },
    {
      "step_number": 2,
      "tool": "csv_analyzer",
      "description": "string",
      "args": {"file_id": "the-uuid", "value_column": "real_column_name"}
    },
    {
      "step_number": 3,
      "tool": "report_generator",
      "description": "string",
      "args": {"file_id": "the-uuid", "title": "Report Title"}
    },
    {
      "step_number": 4,
      "tool": "artifact_verifier",
      "description": "string",
      "args": {}
    }
  ]
}
"""


def _build_user_prompt(file_id: str, instruction: str, inspection_results: dict) -> str:
    tool_info = "\n".join(
        f"- {name}: {desc} | Required args: {REQUIRED_ARGS[name] or 'none'}"
        for name, desc in TOOL_DESCRIPTIONS.items()
    )
    return (
        f"FILE ID: {file_id}\n\n"
        f"USER INSTRUCTION: {instruction}\n\n"
        f"CSV INSPECTION RESULTS:\n{json.dumps(inspection_results, indent=2)}\n\n"
        f"AVAILABLE TOOLS:\n{tool_info}\n\n"
        "Generate the workflow plan JSON now."
    )


def _parse_and_validate_plan(raw_text: str) -> WorkflowPlan:
    """Parse raw model text into a validated WorkflowPlan."""
    clean_text = raw_text.strip() if raw_text else ""
    if clean_text.startswith("```"):
        lines = clean_text.splitlines()
        clean_text = "\n".join(
            line for line in lines if not line.startswith("```")
        ).strip()

    try:
        data = json.loads(clean_text)
    except json.JSONDecodeError as exc:
        raise PlannerPermanentError(
            f"Gemini returned invalid JSON: {exc}\nRaw response:\n{clean_text[:500]}"
        ) from exc

    try:
        plan = WorkflowPlan(**data)
    except Exception as exc:
        raise PlannerPermanentError(
            f"Gemini plan failed Pydantic validation: {exc}\nParsed data:\n{data}"
        ) from exc

    return plan


def create_plan(
    file_id: str,
    instruction: str,
    inspection_results: dict,
    backoff_delays: Optional[List[float]] = None,
    total_deadline_seconds: Optional[float] = None,
) -> WorkflowPlan:
    """
    Call Gemini using the google-genai SDK to create a WorkflowPlan, with bounded
    exponential retries and verified model fallback.

    Args:
        file_id: UUID of the uploaded CSV.
        instruction: Natural-language user instruction.
        inspection_results: Output from csv_inspector.
        backoff_delays: Optional list of backoff sleep durations (defaults to [2.0, 4.0, 8.0]).
        total_deadline_seconds: Maximum wall-clock seconds for the entire planning operation.

    Returns:
        Validated WorkflowPlan instance.

    Raises:
        PlannerPermanentError: If permanent errors occur (auth, invalid model, invalid plan JSON).
        PlannerUnavailableError: If all candidate models remain unavailable after retries.
    """
    delays = backoff_delays if backoff_delays is not None else DEFAULT_BACKOFF_DELAYS
    deadline = (
        total_deadline_seconds
        if total_deadline_seconds is not None
        else getattr(settings, "planner_total_deadline_seconds", DEFAULT_TOTAL_DEADLINE)
    )
    start_time = time.time()

    MIN_REQUEST_TIMEOUT_SEC = 10.0
    MIN_REQUEST_TIMEOUT_MS = 10000

    per_call_timeout_sec = max(
        MIN_REQUEST_TIMEOUT_SEC,
        float(getattr(settings, "gemini_timeout_seconds", 10)),
    )
    timeout_ms = int(per_call_timeout_sec * 1000)
    http_opts = {"timeout": timeout_ms}
    client = genai.Client(api_key=settings.gemini_api_key, http_options=http_opts)
    user_prompt = _build_user_prompt(file_id, instruction, inspection_results)
    generation_config = types.GenerateContentConfig(
        system_instruction=_SYSTEM_PROMPT,
        response_mime_type="application/json",
        temperature=0.2,
        http_options={"timeout": timeout_ms},
    )

    models_to_try: list[str] = [settings.gemini_model]
    if (
        settings.gemini_fallback_model
        and settings.gemini_fallback_model != settings.gemini_model
        and settings.gemini_fallback_model.lower() not in ("none", "")
    ):
        models_to_try.append(settings.gemini_fallback_model)

    last_error: Optional[Exception] = None

    for model_idx, model_id in enumerate(models_to_try):
        provider_deadline_failures = 0
        is_fallback = model_idx > 0
        if is_fallback:
            logger.warning(
                "Primary model unavailable; attempting fallback model '%s'", model_id
            )

        # 1 initial attempt + len(delays) retries
        for attempt in range(1 + len(delays)):
            elapsed = time.time() - start_time
            remaining = deadline - elapsed

            # If insufficient time remains for another valid request (minimum 10s allowed by Gemini),
            # stop retrying immediately and return a clear timeout error.
            if remaining < MIN_REQUEST_TIMEOUT_SEC:
                logger.warning(
                    "Insufficient planning budget remaining (%.1fs < %.1fs minimum request timeout). Stopping attempts on model '%s'.",
                    remaining,
                    MIN_REQUEST_TIMEOUT_SEC,
                    model_id,
                )
                break

            # The call timeout is bounded by timeout_ms and the remaining overall deadline,
            # but NEVER clamped below the 10-second (10000ms) minimum allowed by Gemini.
            effective_call_timeout_ms = max(
                MIN_REQUEST_TIMEOUT_MS,
                min(timeout_ms, int(remaining * 1000)),
            )

            call_generation_config = types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0.2,
                http_options={"timeout": effective_call_timeout_ms},
            )

            try:
                response = client.models.generate_content(
                    model=model_id,
                    contents=user_prompt,
                    config=call_generation_config,
                )
                raw_text = response.text.strip() if response.text else ""
                plan = _parse_and_validate_plan(raw_text)
                return plan
            except Exception as exc:
                last_error = exc
                provider_deadline_error = _is_provider_deadline_error(exc)

                if provider_deadline_error:
                    provider_deadline_failures += 1
                    logger.warning(
                        "Gemini provider returned HTTP 504 for model '%s' "
                        "(deadline response %d/2).",
                        model_id,
                        provider_deadline_failures,
                    )
                    if provider_deadline_failures >= 2:
                        logger.warning(
                            "Provider deadline retry limit reached for model '%s'; "
                            "continuing to the configured fallback if available.",
                            model_id,
                        )
                        break

                if not is_retryable_error(exc):
                    logger.error(
                        "Permanent error encountered with model '%s' (not retrying): %s",
                        model_id,
                        exc,
                    )
                    if isinstance(exc, PlannerPermanentError):
                        raise
                    raise PlannerPermanentError(
                        f"Permanent error from model '{model_id}': {exc}"
                    ) from exc

                # Check deadline before sleeping/retrying
                elapsed = time.time() - start_time
                remaining = deadline - elapsed
                if remaining < MIN_REQUEST_TIMEOUT_SEC:
                    logger.warning(
                        "Planning deadline budget remaining (%.1fs) is below minimum allowed request timeout (%.1fs). Aborting retries on model '%s'.",
                        remaining,
                        MIN_REQUEST_TIMEOUT_SEC,
                        model_id,
                    )
                    break

                # Retryable temporary error. Provider 504s use only the initial
                # backoff before their single allowed same-model retry.
                if attempt < len(delays):
                    delay_index = 0 if provider_deadline_error else attempt
                    delay = delays[delay_index]
                    # If remaining budget cannot support both sleep and a subsequent >= 10s request, stop retrying.
                    if remaining <= MIN_REQUEST_TIMEOUT_SEC:
                        logger.warning(
                            "Remaining budget (%.1fs) is insufficient for backoff sleep and a >=10s request. Aborting retries on model '%s'.",
                            remaining,
                            model_id,
                        )
                        break

                    # Clamp sleep so that at least MIN_REQUEST_TIMEOUT_SEC remains after sleeping
                    max_sleep = remaining - MIN_REQUEST_TIMEOUT_SEC
                    actual_sleep = min(delay, max_sleep)
                    if actual_sleep > 0:
                        if provider_deadline_error:
                            logger.warning(
                                "Retrying Gemini model '%s' once after provider "
                                "deadline response; backing off for %.1fs.",
                                model_id,
                                actual_sleep,
                            )
                        else:
                            logger.warning(
                                "Temporary error on model '%s' (attempt %d/%d): %s. Backing off for %.1fs (budget remaining: %.1fs)...",
                                model_id,
                                attempt + 1,
                                1 + len(delays),
                                exc,
                                actual_sleep,
                                remaining,
                            )
                        time.sleep(actual_sleep)
                else:
                    logger.warning(
                        "Exhausted %d retries for model '%s'. Last error: %s",
                        len(delays),
                        model_id,
                        exc,
                    )
                    break
                    logger.warning(
                        "Exhausted %d retries for model '%s'. Last error: %s",
                        len(delays),
                        model_id,
                        exc,
                    )
                    break

    if last_error is not None and _is_provider_deadline_error(last_error):
        raise PlannerUnavailableError(
            "Gemini provider remained temporarily unavailable after the bounded "
            "HTTP 504 DEADLINE_EXCEEDED retry policy."
        ) from None

    raise PlannerUnavailableError(
        f"All configured models ({models_to_try}) failed after retries. Last error: {last_error}"
    )
