import React from 'react';
import { getDownloadUrl } from '../api/client';
import { classifyExecutionOutcome } from '../utils/executionOutcome.js';

function VerificationDetails({ output }) {
  const integrity = output.integrity || {};
  const completeness = output.completeness || {};
  const checked = completeness.checked_requirements || [];
  const missing = completeness.missing || [];

  const verdictBadge = (value, passed, failed) => {
    const label = value === true ? passed : value === false ? failed : 'Not reported';
    const colors = value === true
      ? 'bg-green-100 text-green-700'
      : value === false
      ? 'bg-red-100 text-red-700'
      : 'bg-gray-100 text-gray-600';
    return <span className={`text-xs px-2 py-0.5 rounded-full font-semibold ${colors}`}>{label}</span>;
  };

  return (
    <div className="space-y-4">
      <div className="border border-gray-200 rounded-lg p-4 bg-gray-50/50">
        <div className="flex items-center justify-between mb-2">
          <p className="text-xs font-bold text-gray-600 uppercase tracking-wide">File Integrity Verification</p>
          {verdictBadge(integrity.passed, 'Passed', 'Failed')}
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
          <div className="bg-white p-2 rounded border border-gray-100">
            <span className="text-gray-400 block">File Size</span>
            <span className="font-semibold text-gray-800">{output.size_bytes ? `${output.size_bytes.toLocaleString()} B` : 'N/A'}</span>
          </div>
          <div className="bg-white p-2 rounded border border-gray-100">
            <span className="text-gray-400 block">Lines</span>
            <span className="font-semibold text-gray-800">{output.line_count?.toLocaleString() || 'N/A'}</span>
          </div>
          <div className="bg-white p-2 rounded border border-gray-100">
            <span className="text-gray-400 block">Format</span>
            <span className="font-semibold text-gray-800">
              {integrity.readable_utf8 === true ? 'UTF-8 Text' : integrity.readable_utf8 === false ? 'Corrupt' : 'Not reported'}
            </span>
          </div>
          <div className="bg-white p-2 rounded border border-gray-100">
            <span className="text-gray-400 block">Markdown Tables</span>
            <span className="font-semibold text-gray-800">{output.has_tables ? 'Formatted (|)' : 'None'}</span>
          </div>
        </div>
      </div>

      <div className="border border-gray-200 rounded-lg p-4 bg-gray-50/50">
        <div className="flex items-center justify-between mb-2">
          <p className="text-xs font-bold text-gray-600 uppercase tracking-wide">Content Completeness Verification</p>
          {verdictBadge(completeness.passed, 'Passed', 'Incomplete')}
        </div>
        {checked.length > 0 && (
          <div className="mb-2">
            <span className="text-xs text-gray-500 block mb-1">Verified Requirements:</span>
            <div className="flex flex-wrap gap-1.5">
              {checked.map((req, idx) => (
                <span key={idx} className="inline-flex items-center px-2 py-0.5 bg-blue-50 text-blue-700 border border-blue-200 rounded text-xs">
                  {req}
                </span>
              ))}
            </div>
          </div>
        )}
        {missing.length > 0 && (
          <div className="mt-2 bg-red-50 border border-red-200 rounded p-3">
            <span className="text-xs font-semibold text-red-700 block mb-1">Missing Required Elements:</span>
            <ul className="list-disc list-inside text-xs text-red-600 space-y-0.5">
              {missing.map((item, idx) => <li key={idx}>{item}</li>)}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}

export default function ResultPanel({ results }) {
  const outcome = classifyExecutionOutcome(results);
  const verifierStep = results.steps?.find((step) => step.tool === 'artifact_verifier');
  const verifierOutput = verifierStep?.output || {};
  const previewContent = verifierOutput.preview || '';

  const banners = {
    completed_verified: {
      title: 'Workflow Complete & Verified',
      description: 'All required steps succeeded and the report passed integrity and completeness verification.',
      classes: 'bg-green-50 border-green-200 text-green-800',
    },
    failed: {
      title: 'Workflow Failed',
      description: outcome.message || 'One or more workflow steps failed.',
      classes: 'bg-red-50 border-red-200 text-red-800',
    },
    verification_unsuccessful: {
      title: 'Verification Unsuccessful',
      description: outcome.message,
      classes: 'bg-amber-50 border-amber-200 text-amber-800',
    },
    verification_not_performed: {
      title: 'Verification Not Performed',
      description: outcome.message,
      classes: 'bg-gray-50 border-gray-200 text-gray-800',
    },
  };
  const banner = banners[outcome.status];

  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden animate-fade-in">
      <div className={`${banner.classes} border-b px-6 py-5`}>
        <h2 className="text-lg font-bold">{banner.title}</h2>
        <p className="text-sm">{banner.description}</p>
      </div>

      <div className="p-6 space-y-5">
        {results.summary && (
          <div>
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1.5">Summary</p>
            <p className="text-sm text-gray-800 leading-relaxed bg-gray-50 p-3 rounded-lg border border-gray-100">{results.summary}</p>
          </div>
        )}

        {verifierStep && <VerificationDetails output={verifierOutput} />}

        {previewContent && (
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Report Markdown Preview</p>
              {results.report_id && <span className="text-xs font-mono text-gray-400">ID: {results.report_id.slice(0, 8)}…</span>}
            </div>
            <pre className="bg-gray-900 text-gray-100 rounded-lg p-4 text-xs font-mono whitespace-pre-wrap break-words max-h-56 overflow-y-auto border border-gray-800 shadow-inner">{previewContent}</pre>
          </div>
        )}

        {outcome.status === 'completed_verified' && (
          <a
            href={getDownloadUrl(results.report_id)}
            download
            className="inline-flex items-center gap-2 px-6 py-3 bg-blue-600 hover:bg-blue-700 active:bg-blue-800 text-white text-sm font-semibold rounded-lg transition w-full justify-center shadow-sm"
          >
            Download Verified Markdown Report (.md)
          </a>
        )}
      </div>
    </div>
  );
}
