/* Fixture setup explicitly reads a snapshot after an identity-only receipt.
 * Browser bridges must use rawRpc so production acknowledgements stay visible.
 */
function snapshotRpc(rawRpc) {
  return async (name, args) => {
    const result = await rawRpc(name, args);
    const state = result?.structuredContent;
    if (!state || Object.hasOwn(state, 'progress') || !state.session_id ||
        !Number.isSafeInteger(state.state_revision) || !state.task_instance_id ||
        state.acknowledged !== true) return result;
    const snapshot = await rawRpc('get_progress_view', {
      session_id: state.session_id, task_instance_id: state.task_instance_id, include_history: true,
    });
    return {...result, structuredContent: {...state, ...snapshot.structuredContent}};
  };
}
module.exports = {snapshotRpc};
