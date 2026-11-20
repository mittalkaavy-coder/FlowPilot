import { useState, useEffect, useCallback, useRef } from 'react';
import { checkHealth } from '../api/client';

export function useBackendHealth(pollIntervalMs = 15000) {
  const [health, setHealth] = useState({
    status: 'checking', // 'checking' | 'online' | 'offline'
    version: null,
    lastChecked: null,
    error: null,
  });

  const failureCountRef = useRef(0);

  const ping = useCallback(async () => {
    try {
      const data = await checkHealth();
      failureCountRef.current = 0; // Reset consecutive failures on success
      setHealth({
        status: data?.status === 'ok' ? 'online' : 'online',
        version: data?.version || '1.0.0',
        lastChecked: new Date(),
        error: null,
      });
    } catch (err) {
      failureCountRef.current += 1;
      // Require 2 consecutive failed health pings before marking Offline.
      // This absorbs transient network glitches or server busy states during heavy planning/execution.
      if (failureCountRef.current >= 2) {
        setHealth({
          status: 'offline',
          version: null,
          lastChecked: new Date(),
          error: err.message || 'Cannot connect to backend (port 8000)',
        });
      }
    }
  }, []);

  useEffect(() => {
    // Initial ping on mount
    ping();

    // Periodic health check every 15 seconds
    const timer = setInterval(ping, pollIntervalMs);

    // Clean up timer on unmount
    return () => clearInterval(timer);
  }, [ping, pollIntervalMs]);

  return { ...health, ping };
}
