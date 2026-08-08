import { describe, expect, it } from "vitest";
import { sanitizeRemoteExecutionEnv } from "./remote-execution-env.js";

describe("sanitizeRemoteExecutionEnv", () => {
  it("does not forward inherited database credential sentinels to remote targets", () => {
    const inherited = {
      DATABASE_URL: "sentinel-runtime",
      DATABASE_MIGRATION_URL: "sentinel-migration",
      PGPASSWORD: "sentinel-password",
      HOME: "/host/home",
    };

    expect(
      sanitizeRemoteExecutionEnv(
        {
          DATABASE_URL: "sentinel-runtime",
          DATABASE_MIGRATION_URL: "sentinel-migration",
          PGPASSWORD: "sentinel-password",
          HOME: "/host/home",
          SAFE_VALUE: "visible",
        },
        inherited,
      ),
    ).toEqual({ SAFE_VALUE: "visible" });
  });
});
