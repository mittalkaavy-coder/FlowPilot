from pydantic import BaseModel, Field
from typing import Any
import uuid


class WorkflowStep(BaseModel):
    step_number: int
    tool: str
    description: str
    args: dict[str, Any] = Field(default_factory=dict)


class WorkflowPlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    goal: str
    steps: list[WorkflowStep]


class PlanRequest(BaseModel):
    file_id: str
    instruction: str


class PlanResponse(BaseModel):
    plan_id: str
    goal: str
    steps: list[WorkflowStep]


class PlanApprovalRequest(BaseModel):
    plan_id: str


class PlanApprovalResponse(BaseModel):
    plan_id: str
    status: str


class PlanRecoveryResponse(BaseModel):
    plan_id: str
    file_id: str
    goal: str
    steps: list[WorkflowStep]
    status: str
    result: Any | None = None
    error: str | None = None
