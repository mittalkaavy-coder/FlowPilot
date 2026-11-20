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

const WORKFLOW_STAGES = [
  ['csv_inspector', 'CSV Inspector', 'Understand your file'],
  ['csv_analyzer', 'CSV Analyzer', 'Find useful patterns'],
  ['report_generator', 'Report Generator', 'Create your report'],
  ['artifact_verifier', 'Artifact Verifier', 'Check every detail'],
];

function WorkflowOverview({ status, steps }) {
  const activeTool = steps.find((step) => step.status === 'running')?.tool;
  const doneTools = new Set(steps.filter((step) => step.status === 'success').map((step) => step.tool));
  const isStarted = ['executing', 'complete', 'error'].includes(status) || steps.some((step) => step.status === 'success' || step.status === 'running');
  return (
    <section className="workflow-overview" aria-label="AI workflow stages">
      <div className="workflow-overview-heading"><div><p className="eyebrow">HOW IT WORKS</p><h2>Your data, thoughtfully processed</h2></div><span className="workflow-count">4 stages</span></div>
      <ol className="workflow-stages">
        {WORKFLOW_STAGES.map(([tool, title, description], index) => {
          const complete = doneTools.has(tool);
          const current = activeTool === tool || (!isStarted && index === 0 && Boolean(steps.length));
          return <li key={tool} className={`workflow-stage${complete ? ' is-complete' : ''}${current ? ' is-current' : ''}`}>
            <span className="stage-number" aria-hidden="true">{complete ? '✓' : `0${index + 1}`}</span>
            <span className="stage-copy"><strong>{title}</strong><small>{description}</small></span>
          </li>;
        })}
      </ol>
    </section>
  );
}

export default function App() {
  const workflow = useWorkflow();
  useBackendHealth(15000); // Keep health checks active in the background.
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
    <div className="app-shell min-h-screen bg-gray-50 flex flex-col justify-between">
      <div>
        {/* ── Header ── */}
        <header className="bg-white border-b border-gray-200 shadow-sm sticky top-0 z-20">
          <div className="topbar-inner max-w-5xl mx-auto px-4 py-3.5 flex items-center justify-between">
            <h1 className="brand-name">FlowPilot</h1>
          </div>
        </header>

        {/* ── Main content ── */}
        <main className="dashboard-main max-w-5xl mx-auto px-4 py-8 space-y-6 pb-24">
          <section className="dashboard-intro">
            <div><p className="eyebrow">FLOWPILOT WORKSPACE</p><h1>Turn your data into decisions.</h1><p>Upload a CSV, describe what you need, and let your AI workflow take it from there.</p></div>
            <div className="intro-mark" aria-hidden="true"><span>✦</span></div>
          </section>
          <WorkflowOverview status={status} steps={executionSteps} />
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
