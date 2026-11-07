import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { expect, it } from "vitest";

it("bundles operator administration for plain Node without the server workspace entrypoint", async () => {
  const cliRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
  // Keep dependencies resolvable from the CLI's own node_modules.
  const directory = mkdtempSync(resolve(cliRoot, ".operator-bundle-"));
  try {
    const { default: config } = await import("../../esbuild.config.mjs");
    const outfile = resolve(directory, "operator.mjs");
    const result = await build({
      ...config,
      absWorkingDir: cliRoot,
      entryPoints: ["src/commands/auth-provision-operator.ts"],
      outfile,
      metafile: true,
      sourcemap: false,
    });
    expect(Object.keys(result.metafile!.inputs).some((name) => name.endsWith("server/src/services/board-auth.ts"))).toBe(true);
    const imports = Object.values(result.metafile!.outputs).flatMap((output) => output.imports);
    expect(imports.some((entry) => entry.external && entry.path.startsWith("@paperclipai/server"))).toBe(false);
    const output = execFileSync(process.execPath, ["--input-type=module", "-e",
      `const m = await import(${JSON.stringify(pathToFileURL(outfile).href)}); if (typeof m.provisionOperator !== 'function') process.exit(1);`,
    ], { encoding: "utf8", timeout: 15000 });
    expect(output).toBe("");
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
}, 30000);
