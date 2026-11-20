import React, { useState } from 'react';

const TOOL_COLORS = {
  csv_inspector: 'bg-blue-100 text-blue-700',
  csv_analyzer: 'bg-purple-100 text-purple-700',
  report_generator: 'bg-green-100 text-green-700',
  artifact_verifier: 'bg-orange-100 text-orange-700',
};

function StatusIcon({ status }) {
  if (status === 'running') {
    return (
      <div className="w-5 h-5 border-2 border-blue-500 border-t-transparent rounded-full animate-spin flex-shrink-0" />
    );
  }
  if (status === 'pending') {
    return (
      <div className="w-5 h-5 flex items-center justify-center flex-shrink-0">
        <span className="block w-3 h-0.5 bg-gray-400 rounded" />
      </div>
    );
  }
  if (status === 'success') {
    return (
      <div className="w-5 h-5 rounded-full bg-green-100 flex items-center justify-center flex-shrink-0">
        <svg
          className="w-3 h-3 text-green-600"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={3}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
        </svg>
      </div>
    );
  }
  if (status === 'error') {
    return (
      <div className="w-5 h-5 rounded-full bg-red-100 flex items-center justify-center flex-shrink-0">
        <svg
          className="w-3 h-3 text-red-600"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={3}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
        </svg>
      </div>
    );
  }
  return (
    <div className="w-5 h-5 flex items-center justify-center flex-shrink-0">
      <span className="block w-3 h-0.5 bg-gray-300 rounded" />
    </div>
  );
}

function OutputSummary({ tool, output }) {
  if (!output || typeof output !== 'object') return null;
  let summary = null;

  if (tool === 'csv_inspector') {
    const rows = output.shape?.rows ?? output.rows;
    const cols = output.shape?.columns ?? output.columns;
    if (rows !== undefined) {
      summary = `${rows} rows, ${cols} columns`;
    }
  } else if (tool === 'csv_analyzer') {
    const parts = [];
    if (output.correlation && output.correlation.coefficient !== undefined && output.correlation.coefficient !== null) {
      parts.push(`r = ${output.correlation.coefficient} (${output.correlation.interpretation || 'Correlation'})`);
    }
    if (output.statistics?.total !== undefined) {
      parts.push(`Total: ${output.statistics.total.toLocaleString()} (Mean: ${output.statistics.mean?.toFixed(2)})`);
    }
    if (output.top_items && output.top_items.length > 0) {
      const top = output.top_items[0];
      parts.push(`Top: ${top.name} (${top.value})`);
    }
    if (parts.length > 0) {
      summary = parts.join(' · ');
    }
  } else if (tool === 'report_generator') {
    if (output.size_bytes !== undefined) {
      summary = `Report generated (${output.size_bytes.toLocaleString()} bytes, ${output.line_count || 0} lines)`;
    }
  } else if (tool === 'artifact_verifier') {
    if (output.verified !== undefined) {
      summary = output.verified
        ? '✓ Verified (Integrity & Completeness passed)'
        : `⚠ Verification failed: ${output.reason || 'Requirements not met'}`;
    }
  } else if (output.message) {
    summary = String(output.message);
  }

  if (!summary) return null;
  return <span className="text-xs text-gray-500 ml-2 italic truncate max-w-xs">{summary}</span>;
}

function StepRow({ step }) {
  const [expanded, setExpanded] = useState(false);
  const toolColor = TOOL_COLORS[step.tool] || 'bg-gray-100 text-gray-600';
  const st = step.status || null;

  return (
    <div className={`border-b border-gray-100 last:border-0 py-3 px-4 ${st === 'error' ? 'bg-red-50' : ''}`}>
      <div className="flex items-center gap-3">
        <StatusIcon status={st} />
        <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold ${toolColor}`}>
          {step.tool}
        </span>
        <span className="text-sm text-gray-700 flex-1 min-w-0 truncate">{step.description}</span>

        {!st && (
          <span className="text-xs text-gray-400 flex-shrink-0">Pending</span>
        )}

        {st === 'success' && step.output && (
          <OutputSummary tool={step.tool} output={step.output} />
        )}

        {(step.output || step.error) && (
          <button
            onClick={() => setExpanded((v) => !v)}
            className="text-xs text-blue-600 hover:text-blue-800 flex-shrink-0 ml-2"
          >
            {expanded ? 'Collapse' : 'Expand'}
          </button>
        )}
      </div>

      {st === 'error' && step.error && (
        <p className="mt-2 ml-8 text-xs text-red-600 font-medium">{step.error}</p>
      )}

      {expanded && (step.output || step.error) && (
        <pre className="mt-2 ml-8 bg-gray-900 text-gray-100 text-xs rounded p-3 overflow-x-auto whitespace-pre-wrap break-words">
          {JSON.stringify(step.output || { error: step.error }, null, 2)}
        </pre>
      )}
    </div>
  );
}

export default function ExecutionLog({ steps }) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm animate-fade-in overflow-hidden">
      <div className="px-6 py-4 border-b border-gray-100 flex items-center justify-between">
        <div className="flex items-center gap-2">
          {/* Terminal icon */}
          <svg
            className="w-5 h-5 text-gray-500"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="m6.75 7.5 3 2.25-3 2.25m4.5 0h3m-9 8.25h13.5A2.25 2.25 0 0 0 21 18V6a2.25 2.25 0 0 0-2.25-2.25H5.25A2.25 2.25 0 0 0 3 6v12a2.25 2.25 0 0 0 2.25 2.25Z"
            />
          </svg>
          <h2 className="text-base font-semibold text-gray-800">Execution Log</h2>
        </div>
        <span className="text-xs text-gray-400">{steps.length} steps</span>
      </div>

      <div>
        {steps.length === 0 ? (
          <p className="text-sm text-gray-400 text-center py-8">No steps to display.</p>
        ) : (
          steps.map((step, idx) => {
            const stepNum = step.step_number ?? idx + 1;
            const toolName = step.tool || 'unknown_tool';
            const stableKey = `${toolName}-${stepNum}`;
            return <StepRow key={stableKey} step={step} />;
          })
        )}
      </div>
    </div>
  );
}
