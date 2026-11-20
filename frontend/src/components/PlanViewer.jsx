import React, { useState } from 'react';
import { normalizeWorkflowPlan } from '../hooks/useWorkflow';

const TOOL_COLORS = {
  csv_inspector:    'bg-blue-100 text-blue-700',
  csv_analyzer:     'bg-purple-100 text-purple-700',
  report_generator: 'bg-green-100 text-green-700',
  artifact_verifier:'bg-orange-100 text-orange-700',
};

function ToolPill({ tool }) {
  const color = TOOL_COLORS[tool] || 'bg-gray-100 text-gray-700';
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold ${color}`}>
      {tool}
    </span>
  );
}

function StepCard({ step }) {
  const [expanded, setExpanded] = useState(false);
  const hasArgs = step.args && Object.keys(step.args).length > 0;
  const stepNum = String(step.step_number).padStart(2, '0');

  return (
    <div className="bg-gray-50 border border-gray-200 rounded-lg p-4 animate-fade-in">
      <div className="flex items-start gap-3">
        {/* Step number badge */}
        <div className="w-8 h-8 rounded-full bg-gray-200 text-gray-600 flex items-center justify-center text-xs font-bold flex-shrink-0 mt-0.5">
          {stepNum}
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap mb-1">
            <ToolPill tool={step.tool} />
          </div>
          <p className="text-sm text-gray-700 leading-relaxed">{step.description}</p>

          {hasArgs && (
            <button
              onClick={() => setExpanded((v) => !v)}
              className="mt-2 text-xs text-blue-600 hover:text-blue-800 font-medium flex items-center gap-1 transition"
            >
              <svg
                className={`w-3 h-3 transition-transform ${expanded ? 'rotate-90' : ''}`}
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2.5}
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="m8.25 4.5 7.5 7.5-7.5 7.5" />
              </svg>
              {expanded ? 'Hide args' : 'Show args'}
            </button>
          )}

          {expanded && hasArgs && (
            <div className="mt-2 bg-white border border-gray-200 rounded p-3 font-mono text-xs text-gray-600 space-y-1">
              {Object.entries(step.args).map(([k, v]) => (
                <div key={k}>
                  <span className="text-gray-400">{k}:</span>{' '}
                  <span>{typeof v === 'object' ? JSON.stringify(v) : String(v)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function PlanViewer({ plan }) {
  if (!plan) return null;

  const normalized = normalizeWorkflowPlan(plan);
  if (!normalized) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-xl p-6 text-center animate-fade-in">
        <p className="text-sm font-semibold text-red-700">Invalid Workflow Plan</p>
        <p className="text-xs text-red-500 mt-1">
          The workflow plan is missing required fields (valid plan ID, goal, or steps) and cannot be approved or executed.
        </p>
      </div>
    );
  }

  const { plan_id: planId, goal, steps } = normalized;

  return (
    <div className="surface-card plan-card bg-white rounded-xl border border-gray-200 shadow-sm p-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-center gap-3 mb-5">
        {/* Brain icon */}
        <div className="w-10 h-10 rounded-lg bg-blue-100 flex items-center justify-center flex-shrink-0">
          <svg
            className="w-5 h-5 text-blue-600"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={1.8}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M9.75 3.104v5.714a2.25 2.25 0 0 1-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 0 1 4.5 0m0 0v5.714c0 .597.237 1.17.659 1.591L19.8 15.3M14.25 3.104c.251.023.501.05.75.082M19.8 15.3l-1.57.393A9.065 9.065 0 0 1 12 15a9.065 9.065 0 0 0-6.23-.693L5 14.5m14.8.8 1.402 1.402c1.232 1.232.65 3.318-1.067 3.611A48.309 48.309 0 0 1 12 21c-2.773 0-5.491-.235-8.135-.687-1.718-.293-2.3-2.379-1.067-3.61L5 14.5"
            />
          </svg>
        </div>
        <div>
          <h2 className="text-lg font-bold text-gray-900">AI-Generated Workflow Plan</h2>
          <p className="text-xs text-gray-400">
            {steps.length} {steps.length === 1 ? 'step' : 'steps'} · Plan ID: {planId.slice(0, 8)}…
          </p>
        </div>
      </div>

      {/* Goal info box */}
      <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-5">
        <p className="text-xs font-semibold text-blue-600 uppercase tracking-wide mb-1">Goal</p>
        <p className="text-sm text-blue-900 leading-relaxed">{goal}</p>
      </div>

      {/* Steps */}
      <ol className="plan-steps space-y-3">
        {steps.map((step, idx) => (
          <li key={`${step.tool || 'step'}-${step.step_number || idx + 1}`}><StepCard step={step} /></li>
        ))}
      </ol>
    </div>
  );
}
