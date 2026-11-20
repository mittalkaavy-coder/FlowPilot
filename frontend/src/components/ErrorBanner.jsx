import React from 'react';

export default function ErrorBanner({ error, onDismiss, onReset, dismissLabel }) {
  return (
    <div className="bg-white border-l-4 border-red-500 rounded-r-xl shadow-sm p-5 animate-fade-in flex gap-4">
      {/* Error icon */}
      <div className="w-9 h-9 rounded-full bg-red-100 flex items-center justify-center flex-shrink-0 mt-0.5">
        <svg
          className="w-5 h-5 text-red-600"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M12 9v3.75m9-.75a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 3.75h.008v.008H12v-.008Z"
          />
        </svg>
      </div>

      <div className="flex-1 min-w-0">
        <h3 className="text-sm font-semibold text-red-800 mb-1">Something went wrong</h3>
        <p className="text-sm text-red-700 leading-relaxed break-words">{error}</p>
        <div className="mt-3 flex items-center gap-4">
          {onDismiss && (
            <button
              onClick={onDismiss}
              className="inline-flex items-center gap-1.5 text-sm font-medium text-blue-600 hover:text-blue-800 transition"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 15 3 9m0 0 6-6M3 9h12a6 6 0 0 1 0 12h-3" />
              </svg>
              {dismissLabel || 'Dismiss & Retry'}
            </button>
          )}
          {onReset && (
            <button
              onClick={onReset}
              className="inline-flex items-center gap-1.5 text-sm font-medium text-gray-500 hover:text-gray-700 transition"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99" />
              </svg>
              Start Over
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
