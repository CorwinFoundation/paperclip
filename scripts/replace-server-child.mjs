/** Keep the current server alive until replacement preparation succeeds. */
export async function replaceServerChild({ prepare, stop, start }) {
  await prepare();
  await stop();
  await start();
}
