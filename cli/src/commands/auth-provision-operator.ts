import { createDb } from "@paperclipai/db";
import { loadPaperclipEnvFile } from "../config/env.js";
import { readConfig, resolveConfigPath } from "../config/store.js";
import { reserveOperatorCredentialFile } from "./operator-credential-file.js";

/** Local instance administration: no HTTP endpoint or human session is used. */
export async function provisionOperator(opts: {
  config: string; companyId: string; name: string; output: string;
}): Promise<void> {
  if (!opts.companyId.trim() || !opts.name.trim()) throw new Error("Company and operator name are required");
  const configPath = resolveConfigPath(opts.config);
  loadPaperclipEnvFile(configPath);
  const config = readConfig(configPath);
  if (!config || config.server.deploymentMode !== "authenticated") {
    throw new Error("Operator provisioning requires an explicit authenticated instance config");
  }
  const dbUrl = process.env.DATABASE_URL?.trim()
    || (config.database.mode === "postgres" ? config.database.connectionString :
      `postgres://paperclip:paperclip@127.0.0.1:${config.database.embeddedPostgresPort ?? 54329}/paperclip`);
  if (!dbUrl) throw new Error("No database connection configured");
  const output = await reserveOperatorCredentialFile(opts.output);
  let db: ReturnType<typeof createDb> | undefined;
  let result: { keyId: string; userId: string; expiresAt: Date | null };
  try {
    const { boardAuthService } = await import("@paperclipai/server");
    db = createDb(dbUrl);
    result = await boardAuthService(db).provisionOperatorBoardApiKey({
      companyId: opts.companyId, name: opts.name, persistCredential: output.write,
    });
  } catch {
    await output.discard();
    // Database/driver errors can include connection secrets. Do not print them.
    throw new Error("Operator provisioning failed; reserved output removed. Check instance connectivity and company ID.");
  } finally {
    await db?.$client.end({ timeout: 5 }).catch(() => undefined);
  }
  console.log(JSON.stringify({ ...result, output: opts.output }));
}
