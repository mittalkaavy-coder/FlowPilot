import axios from 'axios';

// In development, Vite proxies /api and /health to localhost:8000.
// In production, set VITE_API_URL in .env.
const BASE_URL = import.meta.env.VITE_API_URL || '';

const http = axios.create({
  baseURL: BASE_URL,
  timeout: 90000, // 90s — AI planning can take time
});

// Normalize error messages from FastAPI detail field while retaining status code
http.interceptors.response.use(
  (res) => res.data,
  (err) => {
    const status = err.response?.status;
    const detail = err.response?.data?.detail;
    let msg =
      typeof detail === 'string'
        ? detail
        : Array.isArray(detail)
        ? detail.map((d) => d.msg).join('; ')
        : err.message || 'An unexpected error occurred';

    // If backend returned a structured error response (e.g. 503 or 400), prioritize that exact detail message
    if (detail) {
      // Keep detail as msg
    } else if (err.code === 'ECONNABORTED' || msg.includes('timeout')) {
      msg = 'Planning request timed out after 90s. The AI model or server did not respond in time. Please verify that your backend and Gemini API are responsive and try again.';
    } else if (err.code === 'ERR_NETWORK' || (!err.response && msg.includes('Network Error'))) {
      msg = 'Network connection to backend failed. Please ensure the backend server is running on port 8000.';
    }

    const error = new Error(msg);
    error.status = status;
    error.code = err.code;
    return Promise.reject(error);
  }
);

export const checkHealth = () =>
  http.get('/health', { timeout: 5000 });

export const uploadFile = (file) => {
  const form = new FormData();
  form.append('file', file);
  return http.post('/api/upload', form);
};

export const createPlan = (fileId, instruction) =>
  http.post('/api/plan', { file_id: fileId, instruction });

export const approvePlan = (planId) =>
  http.post('/api/approve', { plan_id: planId });

export const executePlan = (planId) =>
  http.post('/api/execute', { plan_id: planId });

export const getWorkflowState = (planId) =>
  http.get(`/api/workflows/${encodeURIComponent(planId)}`);

export const getDownloadUrl = (reportId) =>
  `${BASE_URL}/api/download/${reportId}`;
