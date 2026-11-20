import React from 'react';

const MAX_CHARS = 500;

export default function InstructionInput({ value, onChange, onSubmit, isLoading, disabled }) {
  const remaining = MAX_CHARS - value.length;

  return (
    <div className="surface-card instruction-card bg-white rounded-xl border border-gray-200 shadow-sm p-6 animate-fade-in">
      <label className="block text-base font-semibold text-gray-800 mb-3">
        Describe your workflow
      </label>

      <div className="relative">
        <textarea
          className="w-full border border-gray-300 rounded-lg px-4 py-3 text-sm text-gray-800 placeholder-gray-400 resize-none focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:bg-gray-50 disabled:cursor-not-allowed transition"
          rows={3}
          placeholder="e.g. Analyze the sales data, identify the top 3 products, calculate monthly trends, and create a report."
          value={value}
          maxLength={MAX_CHARS}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
        />
        <span
          className={`absolute bottom-2 right-3 text-xs ${
            remaining < 50 ? 'text-amber-500' : 'text-gray-400'
          }`}
        >
          {remaining}/{MAX_CHARS}
        </span>
      </div>

      <div className="mt-4 flex justify-end">
        <button
          onClick={onSubmit}
          disabled={disabled || isLoading || !value.trim()}
          className="inline-flex items-center gap-2 px-5 py-2.5 bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold rounded-lg transition disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {isLoading ? (
            <>
              <svg
                className="animate-spin w-4 h-4"
                fill="none"
                viewBox="0 0 24 24"
              >
                <circle
                  className="opacity-25"
                  cx="12"
                  cy="12"
                  r="10"
                  stroke="currentColor"
                  strokeWidth="4"
                />
                <path
                  className="opacity-75"
                  fill="currentColor"
                  d="M4 12a8 8 0 018-8v8H4z"
                />
              </svg>
              Planning…
            </>
          ) : (
            <>
              Generate Workflow Plan
              {/* Arrow right icon */}
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
  );
}
