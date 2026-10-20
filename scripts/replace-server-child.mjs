/** Keep the current server alive until replacement preparation succeeds.
 * Recovery must stop any partial candidate before restoring a prepared snapshot.
 * The caller still receives the start failure after successful recovery.
 */
export async function replaceServerChild({ prepare, stop, start, recover }) {
  const prepared = await prepare();
  await stop();
  try {
    await start(prepared);
  } catch (startError) {
    if (!recover) throw startError;
    try {
      await recover(prepared, startError);
    } catch (recoveryError) {
      throw new AggregateError([startError, recoveryError], 'Replacement and recovery failed');
    }
    throw startError;
  }
}
