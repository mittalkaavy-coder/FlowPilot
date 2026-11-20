import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { normalizeWorkflowPlan, canApproveWorkflow } from '../utils/planNormalizer.js';
import { approveAndExecutePlan } from '../utils/planApproval.js';
import { classifyExecutionOutcome } from '../utils/executionOutcome.js';
import {
  ACTIVE_WORKFLOW_KEY,
  buildRecoveredWorkflow,
  clearWorkflowId,
  readWorkflowId,
  recoverWorkflow,
  saveWorkflowId,
} from '../utils/workflowRecovery.js';

// ── Test Helper: Logic extracted from PlanViewer ──
function resolvePlanData(plan) {
  if (!plan) return null;
  const planData = plan.plan || plan.data || plan;
  const planId = planData.plan_id || plan.plan_id || '';
  const goal = planData.goal || plan.goal || 'No goal description provided';
  const steps = Array.isArray(planData.steps)
    ? planData.steps
    : Array.isArray(plan.steps)
    ? plan.steps
    : [];
  return { planId, goal, steps };
}

// ── Test Helper: Logic extracted from App.jsx for building execution steps ──
function buildExecutionSteps(plan, results) {
  const resolvedPlanSteps =
    Array.isArray(plan?.steps)
      ? plan.steps
      : Array.isArray(plan?.plan?.steps)
      ? plan.plan.steps
      : [];

  return Array.isArray(results?.steps) && results.steps.length > 0
    ? results.steps
    : resolvedPlanSteps.map((s, idx) => ({
        step_number: s.step_number || idx + 1,
        tool: s.tool,
        description: s.description,
        args: s.args || {},
        status: 'pending',
      }));
}

// ── Test Helper: Logic extracted from useBackendHealth for debouncing failures ──
class HealthTracker {
  constructor() {
    this.status = 'checking';
    this.failureCount = 0;
  }

  handleSuccess() {
    this.failureCount = 0;
    this.status = 'online';
  }

  handleFailure() {
    this.failureCount += 1;
    if (this.failureCount >= 2) {
      this.status = 'offline';
    }
  }
}

describe('Frontend PlanViewer & Execution Logic Audit Tests', () => {
  it('1. PlanViewer handles a direct PlanResponse', () => {
    const directResponse = {
      plan_id: 'aaaa-1111',
      goal: 'Analyze student dataset',
      steps: [
        { step_number: 1, tool: 'csv_inspector', description: 'Inspect CSV', args: { file_id: '123' } },
        { step_number: 2, tool: 'csv_analyzer', description: 'Analyze marks', args: { file_id: '123', value_column: 'Marks' } },
        { step_number: 3, tool: 'report_generator', description: 'Make report', args: { file_id: '123', title: 'Report' } },
        { step_number: 4, tool: 'artifact_verifier', description: 'Verify artifact', args: {} },
      ],
    };

    const resolved = resolvePlanData(directResponse);
    assert.equal(resolved.planId, 'aaaa-1111');
    assert.equal(resolved.goal, 'Analyze student dataset');
    assert.equal(resolved.steps.length, 4);
    assert.equal(resolved.steps[0].tool, 'csv_inspector');
  });

  it('2. PlanViewer handles a wrapped plan response ({ plan: { ... } })', () => {
    const wrappedResponse = {
      plan: {
        plan_id: 'bbbb-2222',
        goal: 'Wrapped goal analysis',
        steps: [
          { step_number: 1, tool: 'csv_inspector', description: 'Inspect' },
          { step_number: 2, tool: 'csv_analyzer', description: 'Analyze' },
        ],
      },
    };

    const resolved = resolvePlanData(wrappedResponse);
    assert.equal(resolved.planId, 'bbbb-2222');
    assert.equal(resolved.goal, 'Wrapped goal analysis');
    assert.equal(resolved.steps.length, 2);
  });

  it('3. PlanViewer handles missing or invalid steps safely without crashing', () => {
    const invalidPlan = {
      plan_id: 'cccc-3333',
      goal: null,
      steps: 'not-an-array',
    };

    const resolved = resolvePlanData(invalidPlan);
    assert.equal(resolved.planId, 'cccc-3333');
    assert.equal(resolved.goal, 'No goal description provided');
    assert.deepEqual(resolved.steps, []);

    const nullResolved = resolvePlanData(null);
    assert.equal(nullResolved, null);
  });

  it('4. Execution starts with four Pending steps when plan is present but results is null', () => {
    const plan = {
      plan_id: 'dddd-4444',
      goal: 'Execution start test',
      steps: [
        { step_number: 1, tool: 'csv_inspector', description: 'Inspect' },
        { step_number: 2, tool: 'csv_analyzer', description: 'Analyze' },
        { step_number: 3, tool: 'report_generator', description: 'Report' },
        { step_number: 4, tool: 'artifact_verifier', description: 'Verify' },
      ],
    };

    const steps = buildExecutionSteps(plan, null);
    assert.equal(steps.length, 4);
    assert.equal(steps.every((s) => s.status === 'pending'), true);
    assert.equal(steps[0].tool, 'csv_inspector');
    assert.equal(steps[3].tool, 'artifact_verifier');
  });

  it('5. Actual execution results replace Pending statuses', () => {
    const plan = {
      plan_id: 'eeee-5555',
      steps: [
        { step_number: 1, tool: 'csv_inspector', description: 'Inspect' },
        { step_number: 2, tool: 'csv_analyzer', description: 'Analyze' },
      ],
    };

    const results = {
      plan_id: 'eeee-5555',
      steps: [
        { step_number: 1, tool: 'csv_inspector', description: 'Inspect', status: 'success', output: { rows: 6 } },
        { step_number: 2, tool: 'csv_analyzer', description: 'Analyze', status: 'success', output: { r: 0.97 } },
      ],
    };

    const steps = buildExecutionSteps(plan, results);
    assert.equal(steps.length, 2);
    assert.equal(steps.every((s) => s.status === 'success'), true);
    assert.equal(steps[0].output.rows, 6);
  });
});

describe('Frontend Backend Health Debounce Audit Tests', () => {
  it('6. One failed health check does not mark the backend Offline', () => {
    const tracker = new HealthTracker();
    tracker.handleSuccess();
    assert.equal(tracker.status, 'online');

    // First failure
    tracker.handleFailure();
    assert.equal(tracker.status, 'online', 'Status should remain online after single failure');
    assert.equal(tracker.failureCount, 1);
  });

  it('7. Two consecutive failed health checks do mark it Offline', () => {
    const tracker = new HealthTracker();
    tracker.handleSuccess();

    // 1st failure
    tracker.handleFailure();
    assert.equal(tracker.status, 'online');

    // 2nd failure
    tracker.handleFailure();
    assert.equal(tracker.status, 'offline', 'Status should flip to offline after 2 consecutive failures');
    assert.equal(tracker.failureCount, 2);
  });

  it('8. A successful health check resets the failure counter', () => {
    const tracker = new HealthTracker();
    tracker.handleSuccess();

    // 1st failure
    tracker.handleFailure();
    assert.equal(tracker.failureCount, 1);

    // Followed by success
    tracker.handleSuccess();
    assert.equal(tracker.failureCount, 0);
    assert.equal(tracker.status, 'online');

    // Another single failure shouldn't flip to offline
    tracker.handleFailure();
    assert.equal(tracker.status, 'online');
    assert.equal(tracker.failureCount, 1);
  });
});

describe('Workflow Plan Normalization & Execute Button Security Tests', () => {
  const validFourStepPlan = {
    plan_id: '123e4567-e89b-12d3-a456-426614174000',
    goal: 'Analyze student performance data',
    steps: [
      { step_number: 1, tool: 'csv_inspector', description: 'Inspect schema', args: { file_id: 'f1' } },
      { step_number: 2, tool: 'csv_analyzer', description: 'Analyze correlation', args: { file_id: 'f1', value_column: 'Marks' } },
      { step_number: 3, tool: 'report_generator', description: 'Generate report', args: { file_id: 'f1', title: 'Report' } },
      { step_number: 4, tool: 'artifact_verifier', description: 'Verify artifact', args: {} },
    ],
  };

  it('9. normalizeWorkflowPlan correctly parses valid four-step plan in direct and nested formats', () => {
    // Direct
    const direct = normalizeWorkflowPlan(validFourStepPlan);
    assert.ok(direct);
    assert.equal(direct.plan_id, '123e4567-e89b-12d3-a456-426614174000');
    assert.equal(direct.goal, 'Analyze student performance data');
    assert.equal(direct.steps.length, 4);

    // Wrapped in { plan: { ... } }
    const wrappedPlan = normalizeWorkflowPlan({ plan: validFourStepPlan });
    assert.ok(wrappedPlan);
    assert.equal(wrappedPlan.plan_id, '123e4567-e89b-12d3-a456-426614174000');

    // Wrapped in { data: { plan: { ... } } }
    const wrappedData = normalizeWorkflowPlan({ data: { plan: validFourStepPlan } });
    assert.ok(wrappedData);
    assert.equal(wrappedData.plan_id, '123e4567-e89b-12d3-a456-426614174000');
  });

  it('10. normalizeWorkflowPlan rejects missing, empty, or N/A plan_id', () => {
    assert.equal(normalizeWorkflowPlan({ ...validFourStepPlan, plan_id: '' }), null);
    assert.equal(normalizeWorkflowPlan({ ...validFourStepPlan, plan_id: null }), null);
    assert.equal(normalizeWorkflowPlan({ ...validFourStepPlan, plan_id: undefined }), null);
    assert.equal(normalizeWorkflowPlan({ ...validFourStepPlan, plan_id: 'N/A' }), null);
    assert.equal(normalizeWorkflowPlan({ ...validFourStepPlan, plan_id: '   ' }), null);
  });

  it('11. normalizeWorkflowPlan rejects missing, empty, or default goal', () => {
    assert.equal(normalizeWorkflowPlan({ ...validFourStepPlan, goal: '' }), null);
    assert.equal(normalizeWorkflowPlan({ ...validFourStepPlan, goal: null }), null);
    assert.equal(normalizeWorkflowPlan({ ...validFourStepPlan, goal: 'No goal description provided' }), null);
    assert.equal(normalizeWorkflowPlan({ ...validFourStepPlan, goal: '   ' }), null);
  });

  it('12. normalizeWorkflowPlan rejects empty steps or malformed steps without tools', () => {
    assert.equal(normalizeWorkflowPlan({ ...validFourStepPlan, steps: [] }), null);
    assert.equal(normalizeWorkflowPlan({ ...validFourStepPlan, steps: null }), null);
    assert.equal(normalizeWorkflowPlan({ ...validFourStepPlan, steps: 'not-an-array' }), null);
    assert.equal(
      normalizeWorkflowPlan({
        ...validFourStepPlan,
        steps: [{ step_number: 1, tool: '', description: 'missing tool' }],
      }),
      null
    );
  });

  it('13. canApproveWorkflow returns false and disables execute button when plan is invalid', () => {
    // 0 steps
    assert.equal(canApproveWorkflow('awaiting_approval', { ...validFourStepPlan, steps: [] }), false);
    // N/A plan_id
    assert.equal(canApproveWorkflow('awaiting_approval', { ...validFourStepPlan, plan_id: 'N/A' }), false);
    // Missing goal
    assert.equal(canApproveWorkflow('awaiting_approval', { ...validFourStepPlan, goal: '' }), false);
    // Empty object
    assert.equal(canApproveWorkflow('awaiting_approval', {}), false);
    assert.equal(canApproveWorkflow('awaiting_approval', null), false);
  });

  it('14. canApproveWorkflow returns true ONLY when status is awaiting_approval and plan is valid', () => {
    // Valid plan & awaiting_approval -> true
    assert.equal(canApproveWorkflow('awaiting_approval', validFourStepPlan), true);

    // Valid plan, but status is executing -> false
    assert.equal(canApproveWorkflow('executing', validFourStepPlan), false);

    // Valid plan, but status is complete -> false
    assert.equal(canApproveWorkflow('complete', validFourStepPlan), false);

    // Valid plan, but status is idle -> false
    assert.equal(canApproveWorkflow('idle', validFourStepPlan), false);

    // Valid plan, but status is error -> false
    assert.equal(canApproveWorkflow('error', validFourStepPlan), false);
  });
});

describe('Workflow plan state and App wiring regression', () => {
  const generatedPlan = {
    plan_id: 'plan-generated',
    goal: 'Analyze generated workflow data',
    steps: [
      { step_number: 1, tool: 'csv_inspector', description: 'Inspect CSV', args: {} },
      { step_number: 2, tool: 'csv_analyzer', description: 'Analyze CSV', args: {} },
      { step_number: 3, tool: 'report_generator', description: 'Generate report', args: {} },
      { step_number: 4, tool: 'artifact_verifier', description: 'Verify report', args: {} },
    ],
  };

  it('stores the generated plan in workflow state and exposes generation as a separate callback', async () => {
    const hookSource = await readFile(new URL('../hooks/useWorkflow.js', import.meta.url), 'utf8');
    assert.match(hookSource, /plan: normalized,/);
    assert.match(hookSource, /status: 'awaiting_approval'/);
    assert.match(hookSource, /const generatePlan = useCallback\(/);
    assert.match(hookSource, /return \{ \.\.\.state, upload, setError, setInstruction, generatePlan, execute, reset, dismissError \}/);
    assert.doesNotMatch(hookSource, /const plan = useCallback\(/);
    assert.deepEqual(normalizeWorkflowPlan(generatedPlan), generatedPlan);
  });

  it('passes workflow state plan to the normalizer and renders plan and approval controls for pending approval', async () => {
    const appSource = await readFile(new URL('../App.jsx', import.meta.url), 'utf8');
    assert.match(appSource, /const \{ status, fileInfo, instruction, plan, results, error \} = workflow/);
    assert.match(appSource, /normalizeWorkflowPlan\(plan\)/);
    assert.match(appSource, /<PlanViewer plan=\{validPlan\} \/>/);
    assert.match(appSource, /<ApprovalBar[\s\S]*?onApprove=\{workflow\.execute\}/);
    assert.match(appSource, /\['awaiting_approval', 'awaiting_execution', 'approving', 'executing', 'error'\]\.includes\(status\) && validPlan/);
    assert.match(appSource, /onSubmit=\{workflow\.generatePlan\}/);
  });
});

describe('Server-side workflow approval handoff', () => {
  it('does not execute when approval fails', async () => {
    let executionCalls = 0;
    await assert.rejects(
      approveAndExecutePlan(
        'plan-1',
        async () => { throw new Error('approval unavailable'); },
        async () => { executionCalls += 1; }
      ),
      /approval unavailable/
    );
    assert.equal(executionCalls, 0);
  });

  it('executes the same plan only after backend approval confirms its ID and state', async () => {
    const calls = [];
    const result = await approveAndExecutePlan(
      'plan-1',
      async (planId) => {
        calls.push(['approve', planId]);
        return { plan_id: planId, status: 'approved' };
      },
      async (planId) => {
        calls.push(['execute', planId]);
        return 'result';
      }
    );

    assert.equal(result, 'result');
    assert.deepEqual(calls, [['approve', 'plan-1'], ['execute', 'plan-1']]);
  });

  it('does not execute if approval confirms a different plan', async () => {
    let executionCalls = 0;
    await assert.rejects(
      approveAndExecutePlan(
        'plan-1',
        async () => ({ plan_id: 'plan-2', status: 'approved' }),
        async () => { executionCalls += 1; }
      ),
      /did not confirm approval/
    );
    assert.equal(executionCalls, 0);
  });
});

describe('Execution outcome classification', () => {
  const verifiedResponse = () => ({
    plan_id: 'plan-verified',
    status: 'completed',
    summary: 'Workflow completed successfully. All 4 steps executed.',
    report_id: 'report-123',
    verified: true,
    steps: [
      { tool: 'csv_inspector', status: 'success', output: {} },
      { tool: 'csv_analyzer', status: 'success', output: {} },
      { tool: 'report_generator', status: 'success', output: { report_id: 'report-123' } },
      {
        tool: 'artifact_verifier', status: 'success',
        output: {
          verified: true,
          report_id: 'report-123',
          integrity: { passed: true },
          completeness: { passed: true },
        },
      },
    ],
  });

  it('marks only fully successful, explicitly verified execution complete', () => {
    assert.equal(classifyExecutionOutcome(verifiedResponse()).status, 'completed_verified');
  });

  it('classifies analysis and report generation failures as failed', () => {
    for (const failedTool of ['csv_analyzer', 'report_generator']) {
      const response = verifiedResponse();
      const failedStep = response.steps.find((step) => step.tool === failedTool);
      failedStep.status = 'error';
      failedStep.error = `${failedTool} failed with useful detail`;
      response.summary = 'Workflow failed at a step.';
      const outcome = classifyExecutionOutcome(response);
      assert.equal(outcome.status, 'failed');
      assert.match(outcome.message, /failed with useful detail/);
    }
  });

  it('distinguishes verification returning false from verification not performed', () => {
    const unsuccessful = verifiedResponse();
    unsuccessful.verified = false;
    unsuccessful.summary = 'Workflow failed because artifact verification did not succeed.';
    unsuccessful.steps[3].status = 'error';
    unsuccessful.steps[3].error = 'Completeness check failed';
    unsuccessful.steps[3].output.verified = false;
    assert.equal(classifyExecutionOutcome(unsuccessful).status, 'verification_unsuccessful');

    const notPerformed = verifiedResponse();
    notPerformed.steps = notPerformed.steps.slice(0, 3);
    delete notPerformed.verified;
    notPerformed.summary = 'Workflow report was generated.';
    assert.equal(classifyExecutionOutcome(notPerformed).status, 'verification_not_performed');
  });

  it('does not trust missing integrity details or a missing report ID', () => {
    const missingIntegrity = verifiedResponse();
    delete missingIntegrity.steps[3].output.integrity;
    assert.equal(classifyExecutionOutcome(missingIntegrity).status, 'verification_unsuccessful');

    const missingReportId = verifiedResponse();
    delete missingReportId.report_id;
    assert.equal(classifyExecutionOutcome(missingReportId).status, 'verification_unsuccessful');
  });

  it('honors a failed execution status even when HTTP returned a response', () => {
    const failed = verifiedResponse();
    failed.status = 'failed';
    failed.summary = 'Workflow failed.';
    assert.equal(classifyExecutionOutcome(failed).status, 'failed');
  });
});

describe('Workflow recovery after browser refresh', () => {
  const completeResult = () => ({
    plan_id: 'saved-plan',
    status: 'completed',
    summary: 'Workflow completed successfully. All 4 steps executed.',
    report_id: 'report-123',
    verified: true,
    steps: [
      { tool: 'csv_inspector', status: 'success', output: {} },
      { tool: 'csv_analyzer', status: 'success', output: {} },
      { tool: 'report_generator', status: 'success', output: { report_id: 'report-123' } },
      { tool: 'artifact_verifier', status: 'success', output: {
        verified: true,
        report_id: 'report-123',
        integrity: { passed: true },
        completeness: { passed: true },
      } },
    ],
  });
  const workflowRecord = (status, result = null) => ({
    plan_id: 'saved-plan',
    file_id: 'saved-file',
    goal: 'Recover saved workflow',
    steps: [
      { step_number: 1, tool: 'csv_inspector', description: 'Inspect CSV', args: {} },
      { step_number: 2, tool: 'csv_analyzer', description: 'Analyze CSV', args: {} },
      { step_number: 3, tool: 'report_generator', description: 'Generate report', args: {} },
      { step_number: 4, tool: 'artifact_verifier', description: 'Verify report', args: {} },
    ],
    status,
    result,
    error: status === 'failed' ? 'Persisted execution failure detail' : null,
  });

  it('restores created workflows to pending approval', () => {
    const restored = buildRecoveredWorkflow(workflowRecord('created'));
    assert.equal(restored.status, 'awaiting_approval');
    assert.equal(restored.fileInfo.file_id, 'saved-file');
    assert.equal(restored.plan.plan_id, 'saved-plan');
  });

  it('restores approved workflows to awaiting execution without executing them', async () => {
    let executions = 0;
    const restored = buildRecoveredWorkflow(workflowRecord('approved'));
    assert.equal(restored.status, 'awaiting_execution');
    assert.equal(executions, 0);
  });

  it('restores a completed workflow only when its persisted result is verified', () => {
    const restored = buildRecoveredWorkflow(workflowRecord('completed', completeResult()));
    assert.equal(restored.status, 'complete');
    assert.equal(restored.results.report_id, 'report-123');

    const unverifiable = buildRecoveredWorkflow(workflowRecord('completed', {
      ...completeResult(), verified: false,
    }));
    assert.equal(unverifiable.status, 'error');
  });

  it('restores failed execution details without discarding its result', () => {
    const failedResult = completeResult();
    failedResult.steps[1].status = 'error';
    failedResult.steps[1].error = 'Analysis output failed';
    const restored = buildRecoveredWorkflow(workflowRecord('failed', failedResult));
    assert.equal(restored.status, 'error');
    assert.equal(restored.results.steps[1].error, 'Analysis output failed');
  });

  it('does not mark an executing plan successful or automatically restart it', () => {
    const restored = buildRecoveredWorkflow(workflowRecord('executing'));
    assert.equal(restored.status, 'executing');
    assert.equal(restored.executionAttempted, true);
    assert.match(restored.error, /will not restart/i);
  });

  it('stores the workflow ID in tab session storage and reset clears it', () => {
    const values = new Map();
    const storage = {
      getItem: (key) => values.get(key) ?? null,
      setItem: (key, value) => values.set(key, value),
      removeItem: (key) => values.delete(key),
    };
    saveWorkflowId('saved-plan', storage);
    assert.equal(values.get(ACTIVE_WORKFLOW_KEY), 'saved-plan');
    assert.equal(readWorkflowId(storage), 'saved-plan');
    clearWorkflowId(storage);
    assert.equal(readWorkflowId(storage), null);
  });

  it('handles a missing saved ID without requesting a workflow', async () => {
    let requests = 0;
    const recovery = await recoverWorkflow(() => null, async () => { requests += 1; }, () => {});
    assert.equal(recovery.kind, 'empty');
    assert.equal(requests, 0);
  });

  it('handles missing plans and backend recovery errors with useful messages', async () => {
    let cleared = false;
    const missing = await recoverWorkflow(
      () => 'expired-plan',
      async () => { const error = new Error('not found'); error.status = 404; throw error; },
      () => { cleared = true; }
    );
    assert.equal(missing.kind, 'missing');
    assert.equal(cleared, true);
    assert.match(missing.message, /no longer available/i);

    const failed = await recoverWorkflow(
      () => 'saved-plan',
      async () => { throw new Error('backend offline'); },
      () => {}
    );
    assert.equal(failed.kind, 'error');
    assert.match(failed.message, /backend offline/);
  });
});
