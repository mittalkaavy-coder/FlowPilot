# FlowPilot — Product Requirements Document

**Problem Statement:** PS01 — Agentic Workflow Automation<br>
**Version:** 1.0<br>
**Development mode:** Individual<br>
**Target submission:** 18 November 2026

## 1. Product Overview

FlowPilot is an autonomous AI workflow assistant that converts natural-language instructions into executable multi-step workflows.

It understands user objectives, creates execution plans, selects appropriate tools, executes operations and verifies their completion.

Unlike a conventional chatbot, FlowPilot performs actual operations and provides an execution history.

## 2. Problem Statement

People spend considerable time performing repetitive tasks involving data inspection, analysis, reporting and file management.

These tasks often require switching between applications and manually completing multiple steps.

FlowPilot aims to reduce this effort through intelligent workflow planning and controlled execution.

## 3. Target Audience

- Students working with datasets and assignments.
- Data analysts handling repetitive reporting.
- Small businesses managing operational data.
- Individuals who want to automate routine file-based tasks.

## 4. Core Use Case

A user uploads a sales CSV and requests:

"Analyze the sales data, identify the top three products, calculate monthly trends, create a report and verify that it has been saved."

FlowPilot should independently plan and execute the supported operations.

## 5. Core Features

### A. Intelligent Task Understanding
- Accept natural-language instructions.
- Identify the desired outcome.
- Consider uploaded file metadata.
- Identify suitable tools.

### B. Dynamic Workflow Planning
- Divide the objective into logical steps.
- Identify dependencies.
- Select tools.
- Generate a structured execution plan.

### C. Tool Integration
Initial supported tools:

1. CSV Inspector
2. CSV Analyzer
3. Markdown Report Generator
4. Artifact Verification Tool

### D. Controlled Execution
- Execute approved plans.
- Track individual steps.
- Pass actual outputs between steps.
- Handle execution failures.

### E. Completion Verification
- Check generated artifact existence.
- Validate file size.
- Read the artifact again.
- Display actual completion status.


## 6. Technical Architecture

Frontend:
- React
- Vite
- JavaScript
- CSS / Tailwind CSS

Backend:
- Python
- FastAPI

AI:
- Gemini API
- Structured LLM planning
- Custom agent execution engine

Data Processing:
- Pandas
- Pydantic

Testing:
- Pytest
- React component testing where practical

Version Control:
- Git
- GitHub

Frontend and backend must be maintained separately.

React communicates with FastAPI through HTTP APIs.

All API credentials must remain on the backend.

### Workflow

User Request → Context Extraction → AI Planner → Plan Validation → User Approval → Tool Execution → Result Aggregation → Verification → Final Response

## 7. Agent Logic

The LLM is responsible for planning and selecting from available tools.

The backend is responsible for:
- Validating tool names.
- Validating arguments.
- Enforcing execution permissions.
- Executing tools.
- Tracking execution state.
- Verifying outputs.

The LLM must never directly execute arbitrary code or access unrestricted filesystem paths.

## 8. Security

- API keys stored in environment variables.
- Strict file upload validation.
- File size restrictions.
- Tool allowlisting.
- Path traversal protection.
- No arbitrary shell or Python execution.
- Safe error handling.
- Human approval before workflow execution.

## 9. Evaluation Alignment

| Parameter | Implementation |
|---|---|
| Code Quality | Modular architecture |
| Security | Validated tools and restricted file access |
| Efficiency | Focused execution pipeline |
| Testing | Unit and integration tests |
| Accessibility | Responsive, keyboard-friendly interface |
| Problem Alignment | Planning, execution and verification |

## 10. MVP Success Criteria

FlowPilot is considered functional when:

1. A user can upload a CSV.
2. A user can describe a task.
3. The agent generates a valid plan.
4. The user can review and approve the plan.
5. The agent executes actual operations.
6. A useful report is generated.
7. The report is independently verified.
8. The user can download the report.
9. Failures are handled safely.
10. Tests pass.

## 11. Assumptions and Limitations

- The initial version works with CSV files.
- The user supplies the input file.
- The agent has access only to predefined tools.
- External account integrations are outside the initial scope.
- Generated reports are based on actual data, not invented information.
- The exact challenge vertical must match the official event dropdown.

## 12. Future Scope

- Google Drive integration.
- Gmail integration.
- More file formats.
- Workflow templates.
- Persistent execution history.
- Human approval gates for external operations.
- Resumable workflows and intelligent retries.

**Product principle:** FlowPilot must demonstrate real planning, real execution and real verification—not merely AI-generated recommendations.
