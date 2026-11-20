import React from 'react';

export default function ApprovalBar({ onApprove, onCancel, disabled, isExecuting, isApproving, canApprove = true }) {
  const isActionDisabled = disabled || isExecuting || isApproving;
  const isApproveDisabled = disabled || isExecuting || isApproving || !canApprove;

  return (
    <div className="approval-bar sticky bottom-0 z-10 bg-white border-t border-gray-200 shadow-lg rounded-b-xl">
      <div className="max-w-3xl mx-auto px-4 py-4 flex items-center justify-between gap-4">
        {/* Warning text */}
        <p className="text-sm text-amber-700 flex items-center gap-2 flex-1 min-w-0">
          <svg
            className="w-4 h-4 text-amber-500 flex-shrink-0"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z"
            />
          </svg>
          <span className="truncate">
            ⚠️ Review the plan above carefully. Approving will execute these operations.
          </span>
        </p>

        {/* Action Buttons */}
        <div className="flex items-center gap-3 flex-shrink-0">
          <button
            onClick={onCancel}
            disabled={isActionDisabled}
            className="px-4 py-2 rounded-lg border border-gray-300 text-sm font-medium text-gray-700 hover:bg-gray-100 transition disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Cancel
          </button>
          <button
            onClick={onApprove}
            disabled={isApproveDisabled}
            title={!canApprove ? "A valid plan with steps must be generated and approved before executing" : undefined}
            className="inline-flex items-center gap-2 px-5 py-2 bg-green-600 hover:bg-green-700 text-white text-sm font-semibold rounded-lg transition disabled:opacity-50 disabled:cursor-not-allowed shadow-sm"
          >
            {isApproving ? (
              <>
                <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                Approving…
              </>
            ) : isExecuting ? (
              <>
                <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                Executing…
              </>
            ) : (
              <>
                Execute Workflow
                <svg
                  className="w-4 h-4"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={2}
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 4.5 21 12m0 0-7.5 7.5M21 12H3" />
                </svg>
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
