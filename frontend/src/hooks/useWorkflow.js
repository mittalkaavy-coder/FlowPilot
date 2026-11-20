import { useReducer, useCallback, useEffect } from 'react';
import { uploadFile, createPlan, approvePlan, executePlan, getWorkflowState } from '../api/client';
import { normalizeWorkflowPlan } from '../utils/planNormalizer.js';
import { approveAndExecutePlan } from '../utils/planApproval.js';
import { classifyExecutionOutcome } from '../utils/executionOutcome.js';
import {
  clearWorkflowId,
  readWorkflowId,
  recoverWorkflow,
  saveWorkflowId,
} from '../utils/workflowRecovery.js';

export { normalizeWorkflowPlan } from '../utils/planNormalizer.js';

const initialState = {
  status: 'restoring',
  file: null,
  fileInfo: null, // {file_id, filename, rows, columns, column_names}
  instruction: '',
  plan: null, // {plan_id, goal, steps[]}
  results: null, // {plan_id, goal, steps[], report_id, verified, summary}
  error: null,
  executionAttempted: false, // Tracks whether execution has been initiated for the current plan
};

function reducer(state, action) {
  switch (action.type) {
    case 'UPLOAD_START':
      return { ...state, status: 'uploading', error: null };
    case 'UPLOAD_SUCCESS':
      return { ...state, status: 'idle', fileInfo: action.payload, error: null };
    case 'SET_INSTRUCTION':
      return { ...state, instruction: action.payload };
    case 'PLAN_START':
      return { ...state, status: 'planning', error: null, plan: null, executionAttempted: false };
    case 'RESTORE_EMPTY':
      return { ...state, status: 'idle' };
    case 'RESTORE_SUCCESS':
      return { ...state, ...action.payload };
    case 'RESTORE_MISSING':
    case 'RESTORE_ERROR':
      return { ...state, status: 'error', plan: null, results: null, error: action.payload };
    case 'PLAN_SUCCESS': {
      const normalized = normalizeWorkflowPlan(action.payload);
      if (!normalized) {
        return {
          ...state,
          status: 'error',
          plan: null,
          executionAttempted: false,
          error:
            'Received malformed or incomplete workflow plan from server (missing valid plan_id, goal, or steps).',
        };
      }
      return {
        ...state,
        status: 'awaiting_approval',
        plan: normalized,
        executionAttempted: false,
        error: null,
      };
    }
    case 'APPROVAL_START':
      return { ...state, status: 'approving', error: null };
    case 'APPROVAL_FAILED':
      return { ...state, status: 'awaiting_approval', error: action.payload };
    case 'EXECUTE_START':
      return { ...state, status: 'executing', executionAttempted: true, error: null };
    case 'EXECUTE_RESULT': {
      const { results, outcome } = action.payload;
      const completed = outcome.status === 'completed_verified';
      return {
        ...state,
        status: completed ? 'complete' : 'error',
        results,
        error: completed ? null : outcome.message,
      };
    }
    case 'ERROR':
      return { ...state, status: 'error', error: action.payload };
    case 'DISMISS_ERROR': {
      // Approval did not consume the plan; keep it available for a retry.
      if (state.status === 'awaiting_approval' && !state.executionAttempted) {
        return { ...state, error: null };
      }

      // If execution was attempted for this plan, the server-side plan is permanently
      // claimed or rejected (e.g. 409 or failed status). Clear plan & results to prevent
      // re-executing a dead plan, while preserving fileInfo and instruction so the user
      // can generate a fresh plan immediately.
      if (state.executionAttempted) {
        return {
          ...state,
          status: 'idle',
          plan: null,
          results: null,
          executionAttempted: false,
          error: null,
        };
      }

      // If an error occurred before execution was attempted (e.g. planning failure or upload error),
      // return to idle while keeping fileInfo and user instruction intact.
      return {
        ...state,
        status: 'idle',
        plan: null,
        results: null,
        error: null,
      };
    }
    case 'RESET':
      // UI reset: Clears local UI session. Does NOT delete or reset server-side plans.
      return { ...initialState, status: 'idle' };
    default:
      return state;
  }
}

export function useWorkflow() {
  const [state, dispatch] = useReducer(reducer, initialState);

  useEffect(() => {
    let cancelled = false;
    recoverWorkflow(readWorkflowId, getWorkflowState, clearWorkflowId).then((recovery) => {
      if (cancelled) return;
      if (recovery.kind === 'empty') dispatch({ type: 'RESTORE_EMPTY' });
      else if (recovery.kind === 'restored') dispatch({ type: 'RESTORE_SUCCESS', payload: recovery.workflow });
      else if (recovery.kind === 'missing') dispatch({ type: 'RESTORE_MISSING', payload: recovery.message });
      else dispatch({ type: 'RESTORE_ERROR', payload: recovery.message });
    });

    return () => { cancelled = true; };
  }, []);

  const setError = useCallback((msg) => {
    dispatch({ type: 'ERROR', payload: msg });
  }, []);

  const upload = useCallback(async (file) => {
    if (state.status === 'uploading') return; // Prevent duplicate uploads
    clearWorkflowId();
    dispatch({ type: 'UPLOAD_START' });
    try {
      const info = await uploadFile(file);
      dispatch({ type: 'UPLOAD_SUCCESS', payload: info });
    } catch (err) {
      dispatch({ type: 'ERROR', payload: err.message || 'File upload failed' });
    }
  }, [state.status]);

  const setInstruction = useCallback((val) => {
    dispatch({ type: 'SET_INSTRUCTION', payload: val });
  }, []);

  const generatePlan = useCallback(async () => {
    if (!state.fileInfo || !state.instruction.trim()) return;
    if (state.status === 'planning') return;
    clearWorkflowId();
    dispatch({ type: 'PLAN_START' });
    try {
      const planData = await createPlan(state.fileInfo.file_id, state.instruction);
      const normalized = normalizeWorkflowPlan(planData);
      if (normalized) saveWorkflowId(normalized.plan_id);
      dispatch({ type: 'PLAN_SUCCESS', payload: planData });
    } catch (err) {
      dispatch({ type: 'ERROR', payload: err.message || 'Planning request failed' });
    }
  }, [state.fileInfo, state.instruction, state.status]);

  const execute = useCallback(async () => {
    const validPlan = normalizeWorkflowPlan(state.plan);
    if (!validPlan || !['awaiting_approval', 'awaiting_execution'].includes(state.status)) return;
    const needsApproval = state.status === 'awaiting_approval';
    dispatch({ type: needsApproval ? 'APPROVAL_START' : 'EXECUTE_START' });
    let executionStarted = false;
    try {
      let results;
      if (needsApproval) {
        results = await approveAndExecutePlan(
          validPlan.plan_id,
          approvePlan,
          executePlan,
          () => {
            executionStarted = true;
            dispatch({ type: 'EXECUTE_START' });
          }
        );
      } else {
        executionStarted = true;
        results = await executePlan(validPlan.plan_id);
      }
      dispatch({
        type: 'EXECUTE_RESULT',
        payload: { results, outcome: classifyExecutionOutcome(results) },
      });
    } catch (err) {
      if (!executionStarted) {
        dispatch({
          type: 'APPROVAL_FAILED',
          payload: `Workflow approval failed: ${err.message || 'The backend did not approve this plan.'}`,
        });
        return;
      }

      let message = err.message || 'Workflow execution failed';
      if (err.status === 409 || message.includes('409') || message.includes('current status is')) {
        message = `Execution Conflict (HTTP 409): ${message}. Server-side security prevents duplicate or replay execution requests for this plan.`;
      }
      dispatch({ type: 'ERROR', payload: message });
    }
  }, [state.plan, state.status]);

  const reset = useCallback(() => {
    clearWorkflowId();
    dispatch({ type: 'RESET' });
  }, []);
  const dismissError = useCallback(() => {
    if (!(state.status === 'awaiting_approval' && !state.executionAttempted)) clearWorkflowId();
    dispatch({ type: 'DISMISS_ERROR' });
  }, [state.status, state.executionAttempted]);

  return { ...state, upload, setError, setInstruction, generatePlan, execute, reset, dismissError };
}
