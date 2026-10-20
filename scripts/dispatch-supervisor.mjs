import { spawn } from 'node:child_process';
import { pathToFileURL } from 'node:url';

/** Replacement is supplied by the owning supervisor. Never infer idle from health. */
export async function replaceAfterDrain({ request, replace }) {
  let attempted = false;
  try {
    attempted = true;
    const drained = await request('drain');
    if (!ready(drained)) throw new Error('Dispatch drain is not restart-ready');
    const status = await request('status');
    if (!ready(status)) throw new Error('Dispatch readiness changed before replacement');
    await replace();
  } catch (error) {
    // Includes ambiguous drain responses: the server might have closed admission.
    if (attempted) {
      try { await request('resume'); }
      catch { throw new AggregateError([error], 'Replacement failed; admission resume failed. Operator must resume explicitly.'); }
    }
    throw error;
  }
}

function ready(value) {
  return value?.closed === true && value.claims === 0 && value.running === 0 && value.restartReady === true;
}

export function dispatchRequester(baseUrl, authorization, fetchImpl = fetch) {
  const base = new URL(baseUrl);
  if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password || base.search || base.hash) {
    throw new Error('Expected an HTTP server origin without credentials, query or fragment');
  }
  if (base.pathname !== '/') throw new Error('Expected a server origin without a path');
  if (!authorization) throw new Error('Instance-admin authorization is required');
  return async (action) => {
    if (!['status', 'drain', 'resume'].includes(action)) throw new Error('Unknown dispatch action');
    const response = await fetchImpl(new URL(`/api/instance/dispatch${action === 'status' ? '' : '/' + action}`, base), {
      method: action === 'status' ? 'GET' : 'POST',
      headers: { Authorization: authorization, 'Content-Type': 'application/json' },
      body: action === 'status' ? undefined : JSON.stringify(action === 'drain' ? { timeoutMs: 30000 } : {}),
      redirect: 'error', signal: AbortSignal.timeout(35000),
    });
    if (!response.ok) throw new Error(`Dispatch ${action} failed: HTTP ${response.status}`);
    return response.json();
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const [origin, separator, command, ...args] = process.argv.slice(2);
    if (separator !== '--' || !command) throw new Error('Usage: node scripts/dispatch-supervisor.mjs ORIGIN -- COMMAND [ARGS]');
    const request = dispatchRequester(origin, process.env.PAPERCLIP_INSTANCE_ADMIN_AUTHORIZATION);
    await replaceAfterDrain({ request, replace: () => new Promise((resolve, reject) => {
      // Do not pass the operator credential into the replacement process.
      const env = { ...process.env };
      delete env.PAPERCLIP_INSTANCE_ADMIN_AUTHORIZATION;
      const child = spawn(command, args, { stdio: 'inherit', env, shell: false });
      child.once('error', reject);
      child.once('exit', (code, signal) => code === 0 ? resolve() : reject(new Error(`Replacement command failed (${signal ?? code})`)));
    }) });
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
