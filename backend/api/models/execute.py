from pydantic import BaseModel
from typing import Any, Optional


class StepResult(BaseModel):
    step_number: int
    tool: str
    description: str
    status: str  # "success" | "error"
    output: dict[str, Any] = {}
    error: Optional[str] = None


class ExecuteRequest(BaseModel):
    plan_id: str


class ExecuteResponse(BaseModel):
    plan_id: str
    goal: str
    steps: list[StepResult]
    report_id: Optional[str] = None
    verified: bool = False
    summary: str
