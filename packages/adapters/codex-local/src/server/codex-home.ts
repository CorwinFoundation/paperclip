import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { AdapterExecutionContext } from "@paperclipai/adapter-utils";
import { resolvePaperclipInstanceRootForAdapter } from "@paperclipai/adapter-utils/server-utils";

const TRUTHY_ENV_RE = /^(1|true|yes|on)$/i;
const COPIED_SHARED_FILES = ["config.json", "config.toml", "instructions.md"] as const;
const SYMLINKED_SHARED_FILES = ["auth.json"] as const;
const AUTH_CREDENTIAL_KEYS = /(?:openai[_-]?key|api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|session|auth)/i;

function nonEmpty(value: string | undefined): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : null;
}

export async function pathExists(candidate: string): Promise<boolean> {
  return fs.access(candidate).then(() => true).catch(() => false);
}

const THREAD_STORE_RE = /^\s*experimental_thread_store\s*=/m;

// Codex CLI >= 0.160 added an on-disk SQLite per-thread "live writer" store under
// $CODEX_HOME. When two codex runs touch the same thread in the SAME home
// concurrently, the second HANGS on the writer lock ("thread-store conflict:
// thread <id> already has an active writer") instead of failing fast. Managed
// codex-homes are shared per-company, so high-volume or overlapping runs collide
// there (observed wedging Platform Maintainer on 0.163). Pinning the store to
// in_memory disables the on-disk store -> no lock, no hang. The only cost is
// cross-process thread *resume*, a no-op for this fleet (sessions are reset by
// the token-bloat guard and the adapter already falls back to a fresh session).
// 0.144.4 ignores the key, so it is safe ahead of a CLI upgrade. The key is a
// TOML top-level assignment and must precede any [table] section, so it is
// prepended. Idempotent: skipped if already present (e.g. per-agent homes that
// carry the key) and re-applied each run after seedManagedCodexHome re-copies
// config.toml from the shared source.
async function ensureManagedThreadStoreInMemory(
  targetHome: string,
  onLog: AdapterExecutionContext["onLog"],
): Promise<void> {
  const configPath = path.join(targetHome, "config.toml");
  let existing = "";
  try {
    existing = await fs.readFile(configPath, "utf8");
  } catch {
    existing = "";
  }
  if (THREAD_STORE_RE.test(existing)) return;
  const id =
    "paperclip-managed-" +
    createHash("sha256").update(path.resolve(targetHome)).digest("hex").slice(0, 16);
  const line = `experimental_thread_store = { type = "in_memory", id = "${id}" }\n`;
  await fs.writeFile(configPath, existing ? `${line}${existing}` : line, { mode: 0o600 });
  await onLog(
    "stdout",
    `[paperclip] Pinned Codex thread store to in_memory in "${configPath}" (prevents 0.160.x+ shared-home writer-lock hangs).\n`,
  );
}

function hasUsableAuthPayload(authPayload: unknown): boolean {
  if (authPayload === null || typeof authPayload !== "object" || Array.isArray(authPayload)) {
    return false;
  }

  for (const [key, value] of Object.entries(authPayload as Record<string, unknown>)) {
    if (!AUTH_CREDENTIAL_KEYS.test(key)) continue;
    if (key.toLowerCase() === "token_type") continue;
    if (typeof value === "string" && value.trim().length > 0) return true;
  }

  return false;
}

function readApiKeyFromAuthPayload(authPayload: unknown): string | null {
  if (authPayload === null || typeof authPayload !== "object" || Array.isArray(authPayload)) {
    return null;
  }
  const raw = (authPayload as Record<string, unknown>).OPENAI_API_KEY;
  return typeof raw === "string" && raw.trim().length > 0 ? raw.trim() : null;
}

export function resolveSharedCodexHomeDir(
  env: NodeJS.ProcessEnv = process.env,
): string {
  const fromEnv = nonEmpty(env.CODEX_HOME);
  return fromEnv ? path.resolve(fromEnv) : path.join(os.homedir(), ".codex");
}

function isWorktreeMode(env: NodeJS.ProcessEnv): boolean {
  return TRUTHY_ENV_RE.test(env.PAPERCLIP_IN_WORKTREE ?? "");
}

export function resolveManagedCodexHomeDir(
  env: NodeJS.ProcessEnv,
  companyId?: string,
): string {
  const instanceRoot = resolvePaperclipInstanceRootForAdapter({
    homeDir: nonEmpty(env.PAPERCLIP_HOME) ?? undefined,
    instanceId: nonEmpty(env.PAPERCLIP_INSTANCE_ID) ?? undefined,
    env,
  });
  return companyId
    ? path.resolve(instanceRoot, "companies", companyId, "codex-home")
    : path.resolve(instanceRoot, "codex-home");
}

/**
 * True when `homePath` lives under the Paperclip-managed company tree
 * (`<instanceRoot>/companies/<companyId>/...`). This covers both the shared
 * company `codex-home` and the per-agent `agents/<agentId>/codex-home` set by
 * the server-side isolation guard. A path outside that tree is a genuine
 * external/user-supplied override that Paperclip must not seed or overwrite.
 */
export function isManagedCodexHomePath(
  env: NodeJS.ProcessEnv,
  companyId: string | undefined,
  homePath: string,
): boolean {
  if (!companyId) return false;
  const instanceRoot = resolvePaperclipInstanceRootForAdapter({
    homeDir: nonEmpty(env.PAPERCLIP_HOME) ?? undefined,
    instanceId: nonEmpty(env.PAPERCLIP_INSTANCE_ID) ?? undefined,
    env,
  });
  const companyRoot = path.resolve(instanceRoot, "companies", companyId);
  const resolved = path.resolve(homePath);
  return resolved === companyRoot || resolved.startsWith(companyRoot + path.sep);
}

/**
 * True when the Codex home has a usable `auth.json`. Uses `fs.access` (follows
 * symlinks), so a dangling auth symlink whose source has been removed counts as
 * no usable credentials.
 */
export async function codexHomeHasUsableAuth(home: string): Promise<boolean> {
  const authPath = path.join(home, "auth.json");
  if (!(await pathExists(authPath))) return false;
  try {
    const raw = await fs.readFile(authPath, "utf8");
    const parsed = JSON.parse(raw);
    return hasUsableAuthPayload(parsed);
  } catch {
    return false;
  }
}

async function codexHomeHasMatchingApiKeyAuth(home: string, apiKey: string): Promise<boolean> {
  const authPath = path.join(home, "auth.json");
  const existing = await fs.lstat(authPath).catch(() => null);
  if (!existing || existing.isSymbolicLink()) return false;
  try {
    const raw = await fs.readFile(authPath, "utf8");
    const parsed = JSON.parse(raw);
    return readApiKeyFromAuthPayload(parsed) === apiKey.trim();
  } catch {
    return false;
  }
}

async function ensureParentDir(target: string): Promise<void> {
  await fs.mkdir(path.dirname(target), { recursive: true });
}

async function isExpectedSymlink(target: string, source: string): Promise<boolean> {
  const existing = await fs.lstat(target).catch(() => null);
  if (!existing?.isSymbolicLink()) return false;

  const linkedPath = await fs.readlink(target).catch(() => null);
  if (!linkedPath) return false;

  return path.resolve(path.dirname(target), linkedPath) === path.resolve(source);
}

async function createExpectedSymlink(target: string, source: string): Promise<void> {
  try {
    await fs.symlink(source, target);
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code === "EEXIST" && await isExpectedSymlink(target, source)) return;
    throw error;
  }
}

export async function ensureSymlink(target: string, source: string): Promise<void> {
  const existing = await fs.lstat(target).catch(() => null);
  if (!existing) {
    await ensureParentDir(target);
    await createExpectedSymlink(target, source);
    return;
  }

  if (!existing.isSymbolicLink()) {
    // A previous Paperclip version copied this file into the managed home
    // instead of symlinking it. Codex refresh tokens rotate and are
    // single-use, so a stale copy fails with refresh_token_reused on the next
    // run (#5028). Replace the regular file with a symlink so the CLI follows
    // the live source. Safe to delete: target is always under the
    // Paperclip-managed company home, never the user's real ~/.codex.
    // Directories are left alone — `fs.unlink` would throw EISDIR on Unix
    // (and behave inconsistently on Windows). A directory at this path is not
    // a Paperclip-written stale copy and warrants operator inspection rather
    // than silent removal.
    if (existing.isDirectory()) return;
    await fs.unlink(target);
    await createExpectedSymlink(target, source);
    return;
  }

  if (await isExpectedSymlink(target, source)) return;

  await fs.unlink(target);
  await createExpectedSymlink(target, source);
}

async function ensureCopiedFile(target: string, source: string): Promise<void> {
  const existing = await fs.lstat(target).catch(() => null);
  if (existing) return;
  await ensureParentDir(target);
  await fs.copyFile(source, target);
}

/**
 * Writes an `auth.json` containing only `OPENAI_API_KEY` so the codex CLI can
 * authenticate via API key. Overwrites any existing file or symlink at that
 * path. Required because the codex CLI (>= 0.122) ignores the `OPENAI_API_KEY`
 * environment variable and only reads credentials from `$CODEX_HOME/auth.json`.
 */
export async function writeApiKeyAuthJson(home: string, apiKey: string): Promise<void> {
  await fs.mkdir(home, { recursive: true });
  const target = path.join(home, "auth.json");
  await fs.rm(target, { force: true });
  await fs.writeFile(target, JSON.stringify({ OPENAI_API_KEY: apiKey }), { mode: 0o600 });
}

/**
 * Seeds auth/config into an explicit Paperclip-managed `targetHome`. Symlinks
 * `auth.json` from the shared source home (so ChatGPT-subscription credentials
 * stay live and single-use refresh tokens are not copied), copies the static
 * shared config files, and — when an API key is supplied — writes an API-key
 * `auth.json` instead. Used both for the default company home and for the
 * per-agent home set by the server isolation guard.
 */
export async function seedManagedCodexHome(
  targetHome: string,
  env: NodeJS.ProcessEnv,
  onLog: AdapterExecutionContext["onLog"],
  options: { apiKey?: string | null } = {},
): Promise<void> {
  const apiKey = nonEmpty(options.apiKey ?? undefined);

  const sourceHome = resolveSharedCodexHomeDir(env);
  const seedFromShared = path.resolve(sourceHome) !== path.resolve(targetHome);

  await fs.mkdir(targetHome, { recursive: true });

  // If a previous run wrote an apikey-mode auth.json (regular file) and this
  // run has no apiKey, remove it so the chatgpt-mode symlink can be restored.
  // Without this cleanup, ensureSymlink bails on a non-symlink and Codex keeps
  // authenticating with the stale key after it is removed from configuration.
  if (!apiKey && seedFromShared) {
    const authPath = path.join(targetHome, "auth.json");
    const existing = await fs.lstat(authPath).catch(() => null);
    if (existing && !existing.isSymbolicLink()) {
      await fs.rm(authPath, { force: true });
    }
  }

  if (seedFromShared) {
    for (const name of SYMLINKED_SHARED_FILES) {
      const source = path.join(sourceHome, name);
      if (!(await pathExists(source))) continue;
      await ensureSymlink(path.join(targetHome, name), source);
    }

    for (const name of COPIED_SHARED_FILES) {
      const source = path.join(sourceHome, name);
      if (!(await pathExists(source))) continue;
      await ensureCopiedFile(path.join(targetHome, name), source);
    }

    await onLog(
      "stdout",
      `[paperclip] Using ${isWorktreeMode(env) ? "worktree-isolated" : "Paperclip-managed"} Codex home "${targetHome}" (seeded from "${sourceHome}").\n`,
    );
  }

  // Managed homes are shared per-company; keep the Codex thread store in-memory
  // so concurrent/overlapping runs cannot hang on the 0.160.x+ SQLite writer lock.
  await ensureManagedThreadStoreInMemory(targetHome, onLog);

  if (apiKey) {
    await writeApiKeyAuthJson(targetHome, apiKey);
    await onLog(
      "stdout",
      `[paperclip] Wrote API-key auth.json into Codex home "${targetHome}" from configured OPENAI_API_KEY.\n`,
    );
  }
}

export async function prepareManagedCodexHome(
  env: NodeJS.ProcessEnv,
  onLog: AdapterExecutionContext["onLog"],
  companyId?: string,
  options: { apiKey?: string | null } = {},
): Promise<string> {
  const targetHome = resolveManagedCodexHomeDir(env, companyId);
  await seedManagedCodexHome(targetHome, env, onLog, options);
  return targetHome;
}

export type ReconcileManagedCodexHomeStatus =
  | "no_managed_home"
  | "external_override"
  | "already_seeded"
  | "source_auth_missing"
  | "seeded";

export interface ReconcileManagedCodexHomeInput {
  companyId: string | undefined;
  configuredCodexHome: string | null | undefined;
  apiKey?: string | null;
  /**
   * Set when the agent's persisted `OPENAI_API_KEY` is a secret binding that
   * could not be resolved in this context (e.g. startup reconciliation, which
   * never resolves secrets). When true and the home already has usable auth,
   * reconciliation preserves that auth instead of downgrading it to the shared
   * subscription symlink.
   */
  apiKeySecretBound?: boolean;
  env?: NodeJS.ProcessEnv;
  onLog?: AdapterExecutionContext["onLog"];
}

export interface ReconcileManagedCodexHomeResult {
  status: ReconcileManagedCodexHomeStatus;
  home: string | null;
}

const noopOnLog: AdapterExecutionContext["onLog"] = async () => {};

/**
 * Idempotently reconciles a persisted `codex_local` agent home. Phase 1 seeds
 * managed homes at execute time; this is the backfill for agents that already
 * carry a persisted (but unseeded) per-agent `CODEX_HOME` and have not run
 * since the seeding fix landed. Shares the managed-home detection
 * (`isManagedCodexHomePath`) and seeding (`seedManagedCodexHome`) logic so a
 * genuine external/user override is never touched. Safe to re-run: when a valid
 * `auth.json` is already present (and no API-key rewrite is requested) it is a
 * no-op and reports `already_seeded`.
 */
export async function reconcileManagedCodexHome(
  input: ReconcileManagedCodexHomeInput,
): Promise<ReconcileManagedCodexHomeResult> {
  const env = input.env ?? process.env;
  const configured = nonEmpty(input.configuredCodexHome ?? undefined);
  if (!configured) return { status: "no_managed_home", home: null };

  const resolved = path.resolve(configured);
  if (!isManagedCodexHomePath(env, input.companyId, resolved)) {
    return { status: "external_override", home: resolved };
  }

  const apiKey = nonEmpty(input.apiKey ?? undefined);
  const hadUsableAuth = await codexHomeHasUsableAuth(resolved);

  // A secret-bound OPENAI_API_KEY cannot be resolved here, so we cannot rewrite
  // it into auth.json. If the home already has usable auth — typically an
  // API-key auth.json written at execute time when the secret WAS resolved —
  // preserve it. Re-seeding without the key would delete that file and restore
  // the shared subscription symlink, silently changing the agent's credentials
  // on every boot while the persisted config still says "use the secret key".
  if (input.apiKeySecretBound && hadUsableAuth) {
    return { status: "already_seeded", home: resolved };
  }

  if (apiKey && await codexHomeHasMatchingApiKeyAuth(resolved, apiKey)) {
    return { status: "already_seeded", home: resolved };
  }

  await seedManagedCodexHome(resolved, env, input.onLog ?? noopOnLog, { apiKey });

  if (!apiKey && !(await codexHomeHasUsableAuth(resolved))) {
    return { status: "source_auth_missing", home: resolved };
  }

  // Without an API key, seeding only changes disk state when auth was missing.
  // With an API key, the matching-file short-circuit above filters out the
  // already-seeded case before this write path.
  const status: ReconcileManagedCodexHomeStatus =
    !apiKey && hadUsableAuth ? "already_seeded" : "seeded";
  return { status, home: resolved };
}
