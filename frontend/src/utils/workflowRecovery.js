import { normalizeWorkflowPlan } from './planNormalizer.js';
import { classifyExecutionOutcome } from './executionOutcome.js';

export const ACTIVE_WORKFLOW_KEY = 'flowpilot.workflowId';

export function readWorkflowId(storage) {
  try {
    const id = (storage ?? globalThis.sessionStorage)?.getItem(ACTIVE_WORKFLOW_KEY);
    return typeof id === 'string' && id.trim() ? id.trim() : null;
  } catch {
    return null;
  }
}

export function saveWorkflowId(planId, storage) {
  try {
    if (typeof planId === 'string' && planId.trim()) {
      (storage ?? globalThis.sessionStorage)?.setItem(ACTIVE_WORKFLOW_KEY, planId.trim());
    }
  } catch {
    // Recovery is a convenience; persisted backend state remains authoritative.
  }
}

export function clearWorkflowId(storage) {
  try {
    (storage ?? globalThis.sessionStorage)?.removeItem(ACTIVE_WORKFLOW_KEY);
  } catch {
    // The UI can still reset when browser storage is unavailable.
  }
}

export function buildRecoveredWorkflow(workflow) {
  if (!workflow || typeof workflow !== 'object') {
    throw new Error('The backend returned an invalid workflow recovery response.');
  }

  const plan = normalizeWorkflowPlan({
    plan_id: workflow.plan_id,
    goal: workflow.goal,
    steps: workflow.steps,
  });
  if (!plan || typeof workflow.file_id !== 'string' || !workflow.file_id.trim()) {
    throw new Error('The backend workflow record is missing required plan or file information.');
  }

  const restored = {
    status: 'error',
    fileInfo: { file_id: workflow.file_id },
    plan,
    results: workflow.result || null,
    error: null,
    executionAttempted: false,
  };

  if (workflow.status === 'created') {
    restored.status = 'awaiting_approval';
  } else if (workflow.status === 'approved') {
    restored.status = 'awaiting_execution';
  } else if (workflow.status === 'executing') {
    restored.status = 'executing';
    restored.error = 'This workflow is already marked as executing. It may still be running or may have been interrupted; FlowPilot will not restart it automatically.';
    restored.executionAttempted = true;
  } else if (workflow.status === 'completed') {
    if (!workflow.result) {
      restored.status = 'error';
      restored.error = 'The backend marks this workflow completed, but no saved execution result is available. It has not been marked successful.';
      restored.executionAttempted = true;
    } else {
      const outcome = classifyExecutionOutcome(workflow.result);
      restored.status = outcome.status === 'completed_verified' ? 'complete' : 'error';
      restored.error = outcome.status === 'completed_verified'
        ? null
        : `The backend marks this workflow completed, but verification could not be confirmed: ${outcome.message}`;
      restored.executionAttempted = true;
    }
  } else if (workflow.status === 'failed') {
    restored.status = 'error';
    restored.executionAttempted = true;
    if (workflow.result) {
      restored.results = { ...workflow.result, status: 'failed' };
      const outcome = classifyExecutionOutcome(restored.results);
      restored.error = outcome.status === 'verification_unsuccessful'
        ? outcome.message
        : workflow.error || outcome.message || 'This workflow failed.';
    } else {
      restored.error = workflow.error || 'This workflow failed before an execution result was saved.';
    }
  } else {
    throw new Error('The backend returned an unsupported workflow state.');
  }

  return {
    ...restored,
    instruction: '',
  };
}

export async function recoverWorkflow(readId, fetchWorkflow, clearId) {
  const planId = readId();
  if (!planId) return { kind: 'empty' };

  try {
    return { kind: 'restored', workflow: buildRecoveredWorkflow(await fetchWorkflow(planId)) };
  } catch (error) {
    if (error?.status === 404) {
      clearId();
      return {
        kind: 'missing',
        message: 'The saved workflow is no longer available. Start a new workflow to continue.',
      };
    }
    return {
      kind: 'error',
      message: `Could not restore the saved workflow: ${error?.message || 'The backend could not be reached.'}`,
    };
  }
}
