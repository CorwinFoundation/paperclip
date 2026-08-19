import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { REDACTED_EVENT_VALUE, redactCredentialTokens } from "../redaction.js";

// Synthetic values only. Shaped like real credentials (fine-grained PAT is
// github_pat_ + 22 chars + "_" + 59 chars, 93 total) but built from fixed filler
// alphabets so they can never be live secrets.
const SYNTHETIC_FINE_GRAINED_PAT = `github_pat_${"A".repeat(22)}_${"b".repeat(59)}`;
const SYNTHETIC_CLASSIC_PAT = `ghp_${"C".repeat(36)}`;

describe("redactCredentialTokens", () => {
  it("redacts the fine-grained github_pat_ shape", () => {
    expect(redactCredentialTokens(`token ${SYNTHETIC_FINE_GRAINED_PAT} end`)).toBe(
      `token ${REDACTED_EVENT_VALUE} end`,
    );
  });

  it("redacts a fine-grained token preceded by a word character", () => {
    // NDJSON stores a newline as the two characters \\ and n, so a token starting a
    // captured line is preceded by `n`. A leading \\b would never fire here.
    expect(redactCredentialTokens(`line\\n${SYNTHETIC_FINE_GRAINED_PAT}\\nnext`)).toBe(
      `line\\n${REDACTED_EVENT_VALUE}\\nnext`,
    );
  });

  it("redacts a fine-grained token in an env dump whose name has no secret word", () => {
    // GITHUB_PAT_ARE_SCANNER contains no token/key/secret/credential substring, so no
    // name-driven rule ever fired for it. Shape is the only thing that catches it.
    expect(redactCredentialTokens(`GITHUB_PAT_ARE_SCANNER=${SYNTHETIC_FINE_GRAINED_PAT}`)).toBe(
      `GITHUB_PAT_ARE_SCANNER=${REDACTED_EVENT_VALUE}`,
    );
  });

  it("redacts a fine-grained token embedded in a remote URL", () => {
    const out = redactCredentialTokens(
      `https://${SYNTHETIC_FINE_GRAINED_PAT}@github.com/acme/scanner.git`,
    );
    expect(out).not.toContain(SYNTHETIC_FINE_GRAINED_PAT);
    expect(out).toContain("@github.com/acme/scanner.git");
  });

  it("redacts every occurrence, not just the first", () => {
    const out = redactCredentialTokens(
      `a ${SYNTHETIC_FINE_GRAINED_PAT} b ${SYNTHETIC_FINE_GRAINED_PAT} c`,
    );
    expect(out).not.toContain(SYNTHETIC_FINE_GRAINED_PAT);
    expect(out.split(REDACTED_EVENT_VALUE)).toHaveLength(3);
  });

  it("still redacts classic github tokens", () => {
    expect(redactCredentialTokens(`using ${SYNTHETIC_CLASSIC_PAT}`)).not.toContain(
      SYNTHETIC_CLASSIC_PAT,
    );
  });

  it("leaves ordinary log output alone", () => {
    for (const input of [
      "cloned https://github.com/acme/scanner.git at commit abc1234",
      "PATH=/usr/local/bin:/usr/bin",
      "risk-assessment-framework loaded",
      "disk-usage-monitor reports 42%",
      '{"level":"info","msg":"heartbeat ok"}',
    ]) {
      expect(redactCredentialTokens(input)).toBe(input);
    }
  });

  it("is stateless across calls despite the global regexes", () => {
    const input = `x ${SYNTHETIC_FINE_GRAINED_PAT} y`;
    const first = redactCredentialTokens(input);
    expect(redactCredentialTokens(input)).toBe(first);
    expect(redactCredentialTokens(input)).toBe(first);
  });
});

describe("run log store persistence boundary", () => {
  let baseDir: string;
  const previousBasePath = process.env.RUN_LOG_BASE_PATH;

  beforeEach(async () => {
    baseDir = await fs.mkdtemp(path.join(os.tmpdir(), "run-log-redaction-"));
    process.env.RUN_LOG_BASE_PATH = baseDir;
    vi.resetModules();
  });

  afterEach(async () => {
    if (previousBasePath === undefined) delete process.env.RUN_LOG_BASE_PATH;
    else process.env.RUN_LOG_BASE_PATH = previousBasePath;
    await fs.rm(baseDir, { recursive: true, force: true });
  });

  it("never writes a fine-grained PAT to disk, even when the caller did not redact", async () => {
    const { getRunLogStore } = await import("../services/run-log-store.js");
    const store = getRunLogStore();
    const handle = await store.begin({ companyId: "c1", agentId: "a1", runId: "r1" });

    // Verbatim tool_result stdout: an env dump, the path that bypasses command-text
    // redaction entirely and produced the observed leaks.
    await store.append(handle, {
      stream: "stdout",
      ts: new Date().toISOString(),
      chunk: `GITHUB_PAT_ARE_SCANNER=${SYNTHETIC_FINE_GRAINED_PAT}\nHOME=/home/agent`,
    });

    const written = await fs.readFile(path.join(baseDir, handle.logRef), "utf8");

    expect(written).not.toContain(SYNTHETIC_FINE_GRAINED_PAT);
    expect(written).toContain(REDACTED_EVENT_VALUE);
    expect(written).toContain("HOME=/home/agent");
  });

  it("reports the byte count of what it actually wrote", async () => {
    const { getRunLogStore } = await import("../services/run-log-store.js");
    const store = getRunLogStore();
    const handle = await store.begin({ companyId: "c1", agentId: "a1", runId: "r2" });

    const bytes = await store.append(handle, {
      stream: "stdout",
      ts: new Date().toISOString(),
      chunk: `leaked ${SYNTHETIC_FINE_GRAINED_PAT}`,
    });
    const stat = await fs.stat(path.join(baseDir, handle.logRef));

    expect(bytes).toBe(stat.size);
  });

  it("leaves non-credential output byte-identical", async () => {
    const { getRunLogStore } = await import("../services/run-log-store.js");
    const store = getRunLogStore();
    const handle = await store.begin({ companyId: "c1", agentId: "a1", runId: "r3" });

    const chunk = "pnpm test -- --run\n42 passed at github.com/acme/scanner";
    await store.append(handle, { stream: "stdout", ts: new Date().toISOString(), chunk });

    const written = await fs.readFile(path.join(baseDir, handle.logRef), "utf8");

    expect(JSON.parse(written.trim()).chunk).toBe(chunk);
  });
});
