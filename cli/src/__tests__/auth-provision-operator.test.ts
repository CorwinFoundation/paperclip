import { beforeEach, describe, expect, it, vi } from "vitest";
const mocks = vi.hoisted(() => ({
  readConfig: vi.fn(), reserve: vi.fn(), createDb: vi.fn(), provision: vi.fn(),
  write: vi.fn(), discard: vi.fn(), end: vi.fn(),
}));
vi.mock("../config/env.js", () => ({ loadPaperclipEnvFile: vi.fn() }));
vi.mock("../config/store.js", () => ({ readConfig: mocks.readConfig, resolveConfigPath: (p: string) => p }));
vi.mock("../commands/operator-credential-file.js", () => ({ reserveOperatorCredentialFile: mocks.reserve }));
vi.mock("@paperclipai/db", () => ({ createDb: mocks.createDb }));
vi.mock("../../../server/src/services/board-auth.js", () => ({ boardAuthService: () => ({ provisionOperatorBoardApiKey: mocks.provision }) }));
import { provisionOperator } from "../commands/auth-provision-operator.js";
const opts = { config: "/instance/config.json", companyId: "company", name: "Operations", output: "/private/key.json" };
beforeEach(() => {
  vi.clearAllMocks();
  mocks.readConfig.mockReturnValue({ server: { deploymentMode: "authenticated" }, database: { mode: "embedded-postgres", embeddedPostgresPort: 54329 } });
  mocks.reserve.mockResolvedValue({ write: mocks.write, discard: mocks.discard });
  mocks.end.mockResolvedValue(undefined);
  mocks.createDb.mockReturnValue({ $client: { end: mocks.end } });
  mocks.provision.mockResolvedValue({ keyId: "key", userId: "operator" });
});
describe("operator CLI", () => {
  it("passes the exclusive writer to the service without printing the token", async () => {
    const log = vi.spyOn(console, "log").mockImplementation(() => {});
    await provisionOperator(opts);
    expect(mocks.provision).toHaveBeenCalledWith({ companyId: "company", name: "Operations", persistCredential: mocks.write });
    expect(log).toHaveBeenCalledWith(expect.not.stringContaining("token"));
    expect(mocks.discard).not.toHaveBeenCalled();
    expect(mocks.end).toHaveBeenCalled();
    log.mockRestore();
  });
  it("refuses an existing output before connecting", async () => {
    mocks.reserve.mockRejectedValueOnce(new Error("EEXIST"));
    await expect(provisionOperator(opts)).rejects.toThrow("EEXIST");
    expect(mocks.createDb).not.toHaveBeenCalled();
  });
  it("cleans up failed transactions and sanitizes driver errors", async () => {
    mocks.provision.mockRejectedValueOnce(new Error("secret connection value"));
    await expect(provisionOperator(opts)).rejects.toThrow("Operator provisioning failed;");
    expect(mocks.discard).toHaveBeenCalledOnce();
    expect(mocks.end).toHaveBeenCalledOnce();
  });
  it("rejects local trusted mode before reserving a file", async () => {
    mocks.readConfig.mockReturnValueOnce({ server: { deploymentMode: "local_trusted" } });
    await expect(provisionOperator(opts)).rejects.toThrow("authenticated instance config");
    expect(mocks.reserve).not.toHaveBeenCalled();
  });
});
