import { open, unlink } from "node:fs/promises";
import { isAbsolute } from "node:path";

/** Reserve the output before provisioning so an existing credential is never replaced. */
export async function reserveOperatorCredentialFile(path: string) {
  if (!isAbsolute(path)) throw new Error("Credential output must be an absolute path");
  const file = await open(path, "wx", 0o600);
  let closed = false;
  return {
    async write(credential: { keyId: string; token: string; userId: string }) {
      try {
        await file.chmod(0o600);
        await file.writeFile(`${JSON.stringify(credential)}\n`, "utf8");
        await file.sync();
      } finally {
        await file.close();
        closed = true;
      }
    },
    async discard() {
      if (!closed) await file.close();
      closed = true;
      await unlink(path);
    },
  };
}
