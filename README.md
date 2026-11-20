# FlowPilot

> **FlowPilot** is an autonomous agentic workflow automation system for **PS01** that converts natural-language instructions and uploaded CSV data into validated multi-step workflows. Powered by Google Gemini AI via the official Google Gen AI Python SDK (`google-genai`).

---

## ✨ Core Highlights

- **Dynamic AI Workflow Planning** — Gemini translates user goals into structured, dependency-ordered plans with bounded exponential retry (2s, 4s, 8s backoff) and automated model fallback for high-availability.
- **Strict Tool Allowlist** — Only 4 pre-approved tools: `csv_inspector`, `csv_analyzer` (with Pearson correlation), `report_generator`, `artifact_verifier`.
- **Persistent Server-Side Execution Security** — Plans and results persist in SQLite with atomic `BEGIN IMMEDIATE` status transitions; client sends only `plan_id`. Replay attacks and duplicate requests are rejected with HTTP 409 Conflict across backend restarts.
- **Robust File Security** — Enforces UUIDv4 identifiers, 10 MB upload ceilings, CSV content sniffing (UTF-8, tabular check), and canonical path traversal protection.
- **Requirement-Aware Artifact Verification** — Validates report existence, size, UTF-8 integrity, and deterministically asserts content completeness (actual metrics, Pearson coefficient, and top performer).
- **7-State Workflow Engine** — `idle` → `uploading` → `planning` → `awaiting_approval` → `executing` → `complete` | `error`.

---

## 🛠 Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Python 3.11+, FastAPI, Uvicorn, SQLite (WAL mode) |
| **AI** | Google Gemini 3.8 Flash (with verified Gemini 3.1 Flash-Lite fallback & bounded exponential retry) via `google-genai` SDK |
| **Data Processing** | Pandas 2.x / 3.x (Bivariate Pearson correlation, top-N, trends), Pydantic 2.x |
| **Frontend** | React, Vite, Tailwind CSS, Axios, JavaScript |
| **Testing** | Pytest, HTTPX, Pytest-Asyncio (66 automated tests) |

---

## 📁 Repository Structure

```
FlowPilot/
├── PRD.md
├── README.md
├── .gitignore
├── pytest.ini
│
├── backend/
│   ├── .env.example               # GEMINI_API_KEY, GEMINI_MODEL, MAX_UPLOAD_MB
│   ├── requirements.txt           # Python dependencies
│   ├── main.py                    # FastAPI application & middleware
│   │
│   ├── core/
│   │   ├── config.py              # Environment configuration & directories
│   │   ├── security.py            # UUID checks, path traversal guards, CSV content validation
│   │   ├── store.py               # In-memory plan store with status tracking & idempotency locks
│   │   └── cleanup.py             # Temporary and stale file cleanup utility
│   │
│   ├── tools/
│   │   ├── registry.py            # Allowlist registry and schemas
│   │   ├── csv_inspector.py       # Schema, types, row count, nulls, sample rows
│   │   ├── csv_analyzer.py        # Top-N, monthly trends, descriptive statistics
│   │   ├── report_generator.py    # Formatted Markdown report builder
│   │   └── artifact_verifier.py   # Existence, size, re-read validation
│   │
│   ├── agent/
│   │   ├── planner.py             # google-genai structured JSON planning
│   │   ├── validator.py           # Tool allowlist & argument enforcement
│   │   └── executor.py            # Step-by-step runner with context piping
│   │
│   ├── api/
│   │   ├── models/                # Pydantic schemas (plan.py, execute.py)
│   │   └── routes/                # Endpoints: /upload, /download, /plan, /execute
│   │
│   └── tests/                     # 45 Pytest unit, integration, and security tests
│       ├── test_tools.py          # Unit tests for all 4 tools
│       ├── test_planner.py        # Gemini planning and malformed response recovery
│       ├── test_executor.py       # Sequential execution and context injection
│       ├── test_plan_lifecycle.py # Replay protection, idempotency (409), tamper-proofing
│       ├── test_security.py       # Traversal attacks, bad UUIDs, corrupt CSVs, cleanup
│       └── test_api.py            # FastAPI endpoint integration tests
│
└── frontend/                      # React + Vite application
    ├── package.json
    ├── vite.config.js
    ├── tailwind.config.js
    └── src/
        ├── api/client.js          # Centralized Axios API client
        ├── hooks/useWorkflow.js   # 7-state workflow machine
        ├── components/            # FileUpload, PlanViewer, ApprovalBar, ExecutionLog, ResultPanel...
        └── App.jsx                # Responsive UI layout & state orchestration
```

---

## ⚙️ Setup & Installation

### 1. Backend Setup

From the repository root (`FlowPilot/`):

```bash
# Create and activate Python virtual environment
python -m venv venv
# On Windows:
venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install -r backend/requirements.txt

# Configure environment variables
cp backend/.env.example backend/.env
```

Edit `backend/.env`:
```env
GEMINI_API_KEY=your_actual_gemini_api_key_here
GEMINI_MODEL=gemini-3.8-flash
MAX_UPLOAD_MB=10
```

> Obtain a Gemini API key at [Google AI Studio](https://aistudio.google.com/app/apikey).

### 2. Frontend Setup

From the repository root:

```bash
cd frontend
pnpm install   # or: npm install
```

---

## 🚀 Running the Application

### Start the Backend Server (Terminal 1)
```bash
# Navigate to backend directory and start Uvicorn:
cd backend
uvicorn main:app --reload --port 8000
```
- API is live at: `http://localhost:8000`
- Interactive OpenAPI Docs: `http://localhost:8000/docs`

### Start the Frontend Dev Server (Terminal 2)
```bash
cd frontend
pnpm run dev   # or: npm run dev
```
- Web UI is live at: `http://localhost:5173`

---

## 🧪 Running the Test Suite

FlowPilot includes 46 comprehensive automated tests covering tools, planning, execution, state lifecycles, and security boundaries.

From the project root:

```bash
# Run full test suite with verbose output
pytest -v
```

All 45 tests verify:
- Unit behavior of each allowlisted tool.
- AI planning structure and recovery from malformed JSON.
- Step-by-step executor context piping and abort-on-error behavior.
- Replay attack rejection and duplicate execution guards (HTTP 409 Conflict).
- Tamper resistance (server executes only stored plans).
- Path traversal prevention in uploads and report downloads.
- File upload restrictions (10 MB ceiling, `.csv` extension, UTF-8 content validation).
- End-to-end API route integrations.

---

## 🔒 Security Architecture

1. **No Arbitrary Code Execution**: FlowPilot never executes LLM-generated code or shell scripts. Operations run through hard-coded Python functions in `backend/tools/`.
2. **Server-Side Plan Authorization**: The client cannot alter tool parameters or add steps. The backend executes only the validated server-stored plan.
3. **Execution Idempotency**: Once a plan executes or begins executing, any subsequent execution request is blocked with HTTP 409 Conflict.
4. **Filesystem Isolation**: File IDs are strictly checked against UUIDv4 formatting, and target paths are resolved canonical paths restricted to `uploads/` and `reports/`.
5. **Credential Safety**: All API keys remain strictly server-side in `.env` and are never exposed to the frontend or version control.
