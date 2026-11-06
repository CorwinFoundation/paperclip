import { mkdtemp, readFile, rm, stat, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, expect, it } from "vitest";
import { reserveOperatorCredentialFile } from "../commands/operator-credential-file.js";

let dir: string;
beforeEach(async () => { dir = await mkdtemp(join(tmpdir(), "paperclip-operator-")); });
afterEach(async () => { await rm(dir, { recursive: true, force: true }); });

it("writes a private credential file", async () => {
  const path = join(dir, "credential.json");
  const output = await reserveOperatorCredentialFile(path);
  await output.write({ keyId: "key", userId: "operator", token: "test-only" });
  expect((await stat(path)).mode & 0o777).toBe(0o600);
  expect(JSON.parse(await readFile(path, "utf8"))).toEqual({ keyId: "key", userId: "operator", token: "test-only" });
});

it("refuses existing files and symlinks without modifying their target", async () => {
  const target = join(dir, "existing");
  await writeFile(target, "preserved");
  await symlink(target, join(dir, "link"));
  await expect(reserveOperatorCredentialFile(target)).rejects.toMatchObject({ code: "EEXIST" });
  await expect(reserveOperatorCredentialFile(join(dir, "link"))).rejects.toMatchObject({ code: "EEXIST" });
  expect(await readFile(target, "utf8")).toBe("preserved");
});

it("rejects relative paths and removes an unused reservation", async () => {
  await expect(reserveOperatorCredentialFile("relative.json")).rejects.toThrow("absolute path");
  const path = join(dir, "unused");
  await (await reserveOperatorCredentialFile(path)).discard();
  await expect(stat(path)).rejects.toMatchObject({ code: "ENOENT" });
});
