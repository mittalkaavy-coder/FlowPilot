/**
 * Persist approval for this exact plan before submitting its execution request.
 * The callback runs only after the backend confirms the same plan is approved.
 */
export async function approveAndExecutePlan(
  planId,
  requestApproval,
  requestExecution,
  onApproved = () => {}
) {
  const approval = await requestApproval(planId);
  if (approval?.plan_id !== planId || approval?.status !== 'approved') {
    throw new Error('Backend did not confirm approval for this workflow plan.');
  }

  onApproved();
  return requestExecution(planId);
}
