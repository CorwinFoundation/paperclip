import { describe, expect, it } from "vitest";
import { sanitizeInheritedDatabaseCredentialEnv } from "./database-credential-env.js";

describe("sanitizeInheritedDatabaseCredentialEnv", () => {
  it("removes inherited runtime, migration, and libpq credential sentinels", () => {
    const inherited = {
      DATABASE_URL: "sentinel-runtime",
      DATABASE_MIGRATION_URL: "sentinel-migration",
      PGHOST: "sentinel-host",
      PGUSER: "sentinel-user",
      PGPASSWORD: "sentinel-password",
      SAFE_VALUE: "visible",
    };

    expect(
      sanitizeInheritedDatabaseCredentialEnv(
        { ...inherited, EXPLICIT_VALUE: "visible-too" },
        inherited,
      ),
    ).toEqual({
      SAFE_VALUE: "visible",
      EXPLICIT_VALUE: "visible-too",
    });
  });

  it("retains a deliberately supplied workload-specific database role", () => {
    const inherited = {
      DATABASE_URL: "sentinel-server-role",
      PGUSER: "sentinel-server-user",
    };

    expect(
      sanitizeInheritedDatabaseCredentialEnv(
        {
          DATABASE_URL: "sentinel-workload-role",
          PGUSER: "sentinel-workload-user",
        },
        inherited,
      ),
    ).toEqual({
      DATABASE_URL: "sentinel-workload-role",
      PGUSER: "sentinel-workload-user",
    });
  });

  it("matches credential variable names case-insensitively", () => {
    expect(
      sanitizeInheritedDatabaseCredentialEnv(
        { database_url: "sentinel-runtime" },
        { DATABASE_URL: "sentinel-runtime" },
      ),
    ).toEqual({});
  });
});
