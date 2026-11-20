const REQUIRED_TOOLS = [
  'csv_inspector',
  'csv_analyzer',
  'report_generator',
  'artifact_verifier',
];

/** Classify an ExecuteResponse without treating HTTP success as workflow success. */
export function classifyExecutionOutcome(results) {
  const steps = Array.isArray(results?.steps) ? results.steps : [];
  const failedStep = steps.find((step) => step?.status === 'error' && step?.tool !== 'artifact_verifier');
  const verifier = steps.find((step) => step?.tool === 'artifact_verifier');
  const summary = typeof results?.summary === 'string' ? results.summary : '';

  if (failedStep) {
    return {
      status: 'failed',
      message: failedStep?.error || summary || 'Workflow execution failed.',
    };
  }

  if (!REQUIRED_TOOLS.slice(0, 3).every((tool, index) =>
    steps[index]?.tool === tool && steps[index]?.status === 'success'
  )) {
    return {
      status: 'failed',
      message: summary || 'One or more required workflow steps did not succeed.',
    };
  }

  if (!verifier) {
    return {
      status: 'verification_not_performed',
      message: 'Artifact verification was not completed.',
    };
  }

  if (
    steps.length !== REQUIRED_TOOLS.length ||
    steps[3]?.tool !== 'artifact_verifier' ||
    (verifier.status !== 'success' && verifier.status !== 'error')
  ) {
    return {
      status: 'verification_not_performed',
      message: 'Artifact verification was not completed.',
    };
  }

  if (results?.verified === undefined) {
    return {
      status: 'verification_not_performed',
      message: 'The response did not include an artifact verification verdict.',
    };
  }

  if (/\bworkflow failed\b/i.test(summary) && verifier.status !== 'error') {
    return { status: 'failed', message: summary };
  }

  const verificationOutput = verifier.output;
  if (
    verifier.status === 'error' ||
    results.verified === false ||
    verificationOutput?.verified === false
  ) {
    return {
      status: 'verification_unsuccessful',
      message: verifier.error || verificationOutput?.reason || 'Artifact verification did not pass.',
    };
  }

  if (
    results.verified !== true ||
    verificationOutput?.verified !== true ||
    verificationOutput?.integrity?.passed !== true ||
    verificationOutput?.completeness?.passed !== true
  ) {
    return {
      status: 'verification_unsuccessful',
      message: 'Artifact verification details are incomplete or did not confirm integrity and completeness.',
    };
  }

  if (
    typeof results.report_id !== 'string' ||
    !results.report_id.trim() ||
    verificationOutput.report_id !== results.report_id
  ) {
    return {
      status: 'verification_unsuccessful',
      message: 'A matching verified report ID was not returned.',
    };
  }

  if (results.status === 'failed') {
    return { status: 'failed', message: summary || 'The persisted workflow state is failed.' };
  }

  return { status: 'completed_verified', message: '' };
}
