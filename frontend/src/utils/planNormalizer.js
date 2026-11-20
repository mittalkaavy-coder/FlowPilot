/**
 * Normalizes and validates incoming workflow plans.
 * Supports direct PlanResponse, JSON strings, and nested { plan: ... }, { data: ... }, or { result: ... } objects.
 * Returns a canonical { plan_id, goal, steps } object or null if invalid.
 */
export function normalizeWorkflowPlan(raw) {
  if (!raw) {
    return null;
  }

  // Handle string JSON payload if raw was not automatically parsed by Axios
  let parsed = raw;
  if (typeof parsed === 'string') {
    try {
      parsed = JSON.parse(parsed);
    } catch {
      return null;
    }
  }

  if (!parsed || typeof parsed !== 'object') {
    return null;
  }

  // Unwrap potential nesting: raw.data.plan, raw.data, raw.plan, raw.result, raw.workflow_plan
  let candidate = parsed;
  if (candidate.plan && typeof candidate.plan === 'object' && !Array.isArray(candidate.plan)) {
    candidate = candidate.plan;
  } else if (candidate.data && typeof candidate.data === 'object' && !Array.isArray(candidate.data)) {
    candidate =
      candidate.data.plan && typeof candidate.data.plan === 'object' && !Array.isArray(candidate.data.plan)
        ? candidate.data.plan
        : candidate.data;
  } else if (candidate.result && typeof candidate.result === 'object' && !Array.isArray(candidate.result)) {
    candidate = candidate.result;
  } else if (candidate.workflow_plan && typeof candidate.workflow_plan === 'object' && !Array.isArray(candidate.workflow_plan)) {
    candidate = candidate.workflow_plan;
  }

  // Extract plan_id across common aliases
  const rawPlanId =
    candidate.plan_id ||
    candidate.planId ||
    candidate.id ||
    parsed.plan_id ||
    parsed.planId ||
    parsed.id ||
    '';
  const planId = typeof rawPlanId === 'string' ? rawPlanId.trim() : '';

  // Extract goal across common aliases
  const rawGoal =
    candidate.goal ||
    candidate.description ||
    parsed.goal ||
    parsed.description ||
    '';
  const goal = typeof rawGoal === 'string' ? rawGoal.trim() : '';

  // Extract steps array across common aliases
  const rawSteps = Array.isArray(candidate.steps)
    ? candidate.steps
    : Array.isArray(candidate.workflow_steps)
    ? candidate.workflow_steps
    : Array.isArray(parsed.steps)
    ? parsed.steps
    : [];

  // Map and validate each step
  const steps = rawSteps.map((s, idx) => {
    if (!s || typeof s !== 'object') return null;
    const tool =
      typeof s.tool === 'string'
        ? s.tool.trim()
        : typeof s.name === 'string'
        ? s.name.trim()
        : '';
    const description =
      typeof s.description === 'string'
        ? s.description.trim()
        : '';
    const args =
      s.args && typeof s.args === 'object' && !Array.isArray(s.args)
        ? s.args
        : s.arguments && typeof s.arguments === 'object' && !Array.isArray(s.arguments)
        ? s.arguments
        : {};
    const stepNumber =
      typeof s.step_number === 'number'
        ? s.step_number
        : typeof s.stepNumber === 'number'
        ? s.stepNumber
        : idx + 1;

    return {
      step_number: stepNumber,
      tool,
      description,
      args,
    };
  });

  // Validation 1: plan_id must exist, be a non-empty string, and not 'N/A'
  if (!planId || planId.toUpperCase() === 'N/A') {
    return null;
  }

  // Validation 2: goal must be non-empty and not default placeholder
  if (!goal || goal.toLowerCase() === 'no goal description provided') {
    return null;
  }

  // Validation 3: steps must have at least 1 step and ALL steps must be valid with non-empty tool
  if (steps.length === 0 || steps.some((s) => !s || !s.tool)) {
    return null;
  }

  return {
    plan_id: planId,
    goal,
    steps,
  };
}

/**
 * Checks whether an execution can be approved or executed.
 */
export function canApproveWorkflow(status, plan) {
  if (status !== 'awaiting_approval') {
    return false;
  }
  return Boolean(normalizeWorkflowPlan(plan));
}
