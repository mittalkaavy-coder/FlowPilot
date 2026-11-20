import React, { useRef } from 'react';
import { useFileUpload } from '../hooks/useFileUpload';

export default function FileUpload({ onUpload, fileInfo, isLoading, onError }) {
  const inputRef = useRef(null);
  const { isDragging, handleDragOver, handleDragLeave, handleDrop, handleChange } =
    useFileUpload(onUpload, onError);

  if (isLoading) {
    return (
      <div className="bg-white rounded-xl border border-blue-200 shadow-sm p-10 flex flex-col items-center justify-center animate-pulse">
        <div className="animate-spin w-10 h-10 border-4 border-blue-600 border-t-transparent rounded-full mb-4" />
        <p className="text-gray-700 font-medium">Validating and uploading CSV…</p>
        <p className="text-xs text-gray-400 mt-1">Inspecting tabular structure and columns</p>
      </div>
    );
  }

  if (fileInfo) {
    return (
      <div className="bg-white rounded-xl border border-green-200 shadow-sm p-6 animate-fade-in">
        <div className="flex items-center gap-4">
          {/* Green checkmark icon */}
          <div className="w-12 h-12 rounded-full bg-green-100 flex items-center justify-center flex-shrink-0">
            <svg
              className="w-6 h-6 text-green-600"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2.5}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
          </div>
          <div className="flex-1 min-w-0">
            <p className="font-semibold text-gray-900 truncate">{fileInfo.filename}</p>
            <p className="text-sm text-gray-500 mt-0.5">
              {fileInfo.rows?.toLocaleString()} rows · {fileInfo.columns} columns
            </p>
            {fileInfo.column_names && fileInfo.column_names.length > 0 && (
              <p className="text-xs text-gray-400 mt-1 truncate">
                Columns: {fileInfo.column_names.join(', ')}
              </p>
            )}
          </div>
          <span className="px-3 py-1 rounded-full bg-green-100 text-green-700 text-xs font-medium">
            Uploaded
          </span>
        </div>
      </div>
    );
  }

  return (
    <div
      className={`bg-white rounded-xl border-2 border-dashed shadow-sm p-10 flex flex-col items-center justify-center cursor-pointer transition-colors ${
        isDragging
          ? 'border-blue-500 bg-blue-50'
          : 'border-gray-300 hover:border-blue-400 hover:bg-gray-50'
      }`}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      onClick={() => inputRef.current?.click()}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".csv,text/csv"
        className="hidden"
        onChange={handleChange}
        disabled={isLoading}
      />

      {/* Cloud upload icon */}
      <svg
        className={`w-14 h-14 mb-4 transition-colors ${isDragging ? 'text-blue-500' : 'text-gray-300'}`}
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
        strokeWidth={1.5}
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M12 16.5V9.75m0 0 3 3m-3-3-3 3M6.75 19.5a4.5 4.5 0 0 1-1.41-8.775 5.25 5.25 0 0 1 10.233-2.33 3 3 0 0 1 3.758 3.848A3.752 3.752 0 0 1 18 19.5H6.75Z"
        />
      </svg>

      <p className="text-lg font-semibold text-gray-700 mb-1">
        Drag &amp; drop your CSV file here
      </p>
      <p className="text-sm text-gray-400">
        or{' '}
        <span className="text-blue-600 font-medium underline underline-offset-2">
          click to browse
        </span>
      </p>
      <p className="text-xs text-gray-400 mt-3">Only .csv files are accepted · Max 10 MB</p>
    </div>
  );
}
