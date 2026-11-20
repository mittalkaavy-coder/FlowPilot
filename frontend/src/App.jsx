import React from 'react';
import { useWorkflow, normalizeWorkflowPlan } from './hooks/useWorkflow';
import { useBackendHealth } from './hooks/useBackendHealth';
import FileUpload from './components/FileUpload';
import InstructionInput from './components/InstructionInput';
import PlanViewer from './components/PlanViewer';
import ApprovalBar from './components/ApprovalBar';
import ExecutionLog from './components/ExecutionLog';
import ResultPanel from './components/ResultPanel';
import ErrorBanner from './components/ErrorBanner';

const STATUS_BADGES = {
  restoring: { label: 'Restoring Workflow…', class: 'bg-blue-100 text-blue-700' },
  idle: { label: 'Ready', class: 'bg-gray-100 text-gray-600' },
  uploading: { label: 'Uploading…', class: 'bg-blue-100 text-blue-700' },
  planning: { label: 'Planning…', class: 'bg-purple-100 text-purple-700' },
  awaiting_approval: { label: 'Awaiting Approval', class: 'bg-amber-100 text-amber-700' },
  awaiting_execution: { label: 'Approved · Awaiting Execution', class: 'bg-blue-100 text-blue-700' },
  approving: { label: 'Approving…', class: 'bg-amber-100 text-amber-700' },
  executing: { label: 'Executing…', class: 'bg-blue-100 text-blue-700 animate-pulse' },
  complete: { label: 'Complete', class: 'bg-green-100 text-green-700' },
  error: { label: 'Error', class: 'bg-red-100 text-red-700' },
};

function StatusBadge({ status }) {
  const badge = STATUS_BADGES[status] || STATUS_BADGES.idle;
  return (
    <span className={`inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold ${badge.class}`}>
      {badge.label}
    </span>
  );
}

function BackendHealthBadge({ status, version }) {
  if (status === 'online') {
    return (
      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-emerald-50 text-emerald-700 border border-emerald-200" title={`Connected to FlowPilot Backend v${version || '1.0.0'}`}>
        <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
        Backend Online
      </span>
    );
  }
  if (status === 'checking') {
    return (
      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-amber-50 text-amber-700 border border-amber-200">
        <span className="w-2 h-2 rounded-full bg-amber-400 animate-ping" />
        Connecting…
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-rose-50 text-rose-700 border border-rose-200" title="Cannot reach backend on port 8000">
      <span className="w-2 h-2 rounded-full bg-rose-500" />
      Backend Offline
    </span>
  );
}

export default function App() {
  const workflow = useWorkflow();
  const backendHealth = useBackendHealth(15000); // Poll health every 15 seconds
  const { status, fileInfo, instruction, plan, results, error } = workflow;

  const validPlan = normalizeWorkflowPlan(plan);
  const resolvedPlanSteps = validPlan ? validPlan.steps : [];

  const executionSteps =
    Array.isArray(results?.steps) && results.steps.length > 0
      ? results.steps
      : resolvedPlanSteps.map((s, idx) => ({
          step_number: s.step_number || idx + 1,
          tool: s.tool,
          description: s.description,
          args: s.args || {},
          status: 'pending',
        }));

  const canApprove = ['awaiting_approval', 'awaiting_execution'].includes(status) && Boolean(validPlan);

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col justify-between">
      <div>
        {/* ── Header ── */}
        <header className="bg-white border-b border-gray-200 shadow-sm sticky top-0 z-20">
          <div className="max-w-3xl mx-auto px-4 py-3.5 flex items-center justify-between">
            <div className="flex items-center gap-3">
              {/* FP brand logo */}
              <div className="w-9 h-9 rounded-lg bg-blue-600 flex items-center justify-center flex-shrink-0 shadow-sm">
                <span className="text-white text-sm font-black tracking-tight">FP</span>
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <h1 className="text-lg font-bold text-gray-900 leading-none">FlowPilot</h1>
                  <span className="text-[10px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded bg-blue-50 text-blue-700 border border-blue-200">
                    PS01
                  </span>
                </div>
                <p className="text-xs text-gray-400 leading-none mt-1">Agentic Workflow Automation</p>
              </div>
            </div>

            <div className="flex items-center gap-2.5">
              <BackendHealthBadge status={backendHealth.status} version={backendHealth.version} />
              <StatusBadge status={status} />
            </div>
          </div>
        </header>

        {/* ── Main content ── */}
        <main className="max-w-3xl mx-auto px-4 py-8 space-y-6 pb-24">
          {status === 'restoring' && (
            <div role="status" className="bg-blue-50 border border-blue-200 rounded-lg p-4 text-sm text-blue-800">
              Restoring workflow state from FlowPilot…
            </div>
          )}

          {(plan || ['error', 'executing', 'complete'].includes(status)) && (
            <div className="flex justify-end">
              <button
                type="button"
                onClick={workflow.reset}
                className="px-3 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50"
              >
                Start New Workflow
              </button>
            </div>
          )}

          {/* Error banner — always visible if error */}
          {error && (
            <ErrorBanner
              error={error}
              dismissLabel={
                workflow.executionAttempted
                  ? 'Dismiss & Re-plan Workflow'
                  : status === 'awaiting_approval'
                  ? 'Dismiss & Retry Approval'
                  : 'Dismiss & Retry'
              }
              onDismiss={workflow.dismissError}
              onReset={workflow.reset}
            />
          )}

          {/* File upload — visible until complete */}
          {!['complete', 'restoring'].includes(status) && (
            <FileUpload
              onUpload={workflow.upload}
              fileInfo={fileInfo}
              isLoading={status === 'uploading'}
              onError={workflow.setError}
            />
          )}

          {/* Instruction input — shown when file is uploaded and not yet executing/complete */}
          {fileInfo && !['executing', 'complete', 'restoring'].includes(status) && (
            <InstructionInput
              value={instruction}
              onChange={workflow.setInstruction}
              onSubmit={workflow.generatePlan}
              isLoading={status === 'planning'}
              disabled={status !== 'idle' && status !== 'error'}
            />
          )}

          {/* Planning spinner */}
          {status === 'planning' && (
            <div className="bg-white rounded-xl border border-gray-200 p-10 text-center shadow-sm animate-fade-in">
              <div className="animate-spin w-10 h-10 border-4 border-blue-600 border-t-transparent rounded-full mx-auto mb-4" />
              <p className="font-semibold text-gray-700 text-lg">Analyzing data and generating plan…</p>
              <p className="text-gray-400 text-sm mt-1">
                The Gemini planner is inspecting your CSV schema and designing the workflow
              </p>
            </div>
          )}

          {/* Plan viewer + approval bar */}
          {['awaiting_approval', 'awaiting_execution', 'approving', 'executing', 'error'].includes(status) && validPlan && (
            <div className="space-y-4">
              <PlanViewer plan={validPlan} />
              <ApprovalBar
                onApprove={workflow.execute}
                onCancel={workflow.reset}
                disabled={!['awaiting_approval', 'awaiting_execution'].includes(status)}
                isExecuting={status === 'executing'}
                isApproving={status === 'approving'}
                canApprove={canApprove}
              />
            </div>
          )}

          {/* Execution log */}
          {status === 'executing' || results ? (
            <ExecutionLog steps={executionSteps} />
          ) : null}

          {/* Executing — show live progress indicator */}
          {status === 'executing' && !error && (
            <div className="text-center py-6 bg-white border border-gray-200 rounded-xl p-6 shadow-sm">
              <div className="animate-spin w-8 h-8 border-4 border-blue-600 border-t-transparent rounded-full mx-auto mb-3" />
              <p className="text-sm font-semibold text-gray-700">
                Executing workflow steps sequentially…
              </p>
              <p className="text-xs text-gray-400 mt-1">
                Running allowlisted tools and generating verified report artifact
              </p>
            </div>
          )}

          {/* Result panel */}
          {results && (
            <ResultPanel results={results} />
          )}
        </main>
      </div>

      {/* Footer */}
      <footer className="text-center py-4 text-xs text-gray-400 border-t border-gray-200 bg-white">
        FlowPilot · Autonomous Agentic System with Strict Allowlist &amp; Server-Side Execution Security
      </footer>
    </div>
  );
}
