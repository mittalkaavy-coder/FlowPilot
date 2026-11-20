import { useState, useCallback } from 'react';

const MAX_UPLOAD_BYTES = 10 * 1024 * 1024; // 10 MB

export function useFileUpload(onFile, onError) {
  const [isDragging, setIsDragging] = useState(false);

  const validateAndProcess = useCallback(
    (file) => {
      if (!file) return;

      if (!file.name.toLowerCase().endsWith('.csv')) {
        const msg = 'Invalid file type. Only .csv files are supported.';
        if (onError) onError(msg);
        return;
      }

      if (file.size === 0) {
        const msg = 'The selected CSV file is empty (0 bytes). Please upload a valid CSV.';
        if (onError) onError(msg);
        return;
      }

      if (file.size > MAX_UPLOAD_BYTES) {
        const msg = 'File exceeds the 10 MB maximum upload limit.';
        if (onError) onError(msg);
        return;
      }

      onFile(file);
    },
    [onFile, onError]
  );

  const handleDragOver = useCallback((e) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback(() => setIsDragging(false), []);

  const handleDrop = useCallback(
    (e) => {
      e.preventDefault();
      setIsDragging(false);
      const file = e.dataTransfer.files?.[0];
      if (file) validateAndProcess(file);
    },
    [validateAndProcess]
  );

  const handleChange = useCallback(
    (e) => {
      const file = e.target.files?.[0];
      if (file) validateAndProcess(file);
      // Reset input value so selecting the same file again still fires change
      e.target.value = '';
    },
    [validateAndProcess]
  );

  return { isDragging, handleDragOver, handleDragLeave, handleDrop, handleChange };
}
