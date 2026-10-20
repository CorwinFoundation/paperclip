/** Keep the current server alive until replacement preparation succeeds.
 * Recovery must stop any partial candidate before restoring a prepared snapshot.
 * The caller still receives the start failure after successful recovery.
 */
export async function replaceServerChild({ prepare, stop, start, ready, cleanup, recover }) {
  const prepared = await prepare();
  await stop();
  try {
    await start(prepared);
    if (ready) await ready(prepared);
  } catch (startError) {
    // Never restore while a failed candidate may still own the port/database.
    if (cleanup) {
      try {
        await cleanup(prepared, startError);
      } catch (cleanupError) {
        throw new AggregateError([startError, cleanupError], 'Replacement and cleanup failed');
      }
    }
    if (!recover) throw startError;
    try {
      await recover(prepared, startError);
    } catch (recoveryError) {
      throw new AggregateError([startError, recoveryError], 'Replacement and recovery failed');
    }
    throw startError;
  }
}
