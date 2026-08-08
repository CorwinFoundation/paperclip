const DATABASE_CREDENTIAL_ENV_KEYS = new Set([
  "DATABASE_URL",
  "DATABASE_MIGRATION_URL",
  "PGCHANNELBINDING",
  "PGDATABASE",
  "PGHOST",
  "PGHOSTADDR",
  "PGOPTIONS",
  "PGPASSFILE",
  "PGPASSWORD",
  "PGPORT",
  "PGREQUIREAUTH",
  "PGSERVICE",
  "PGSERVICEFILE",
  "PGSSLCERT",
  "PGSSLCRL",
  "PGSSLCRLDIR",
  "PGSSLKEY",
  "PGSSLMODE",
  "PGSSLROOTCERT",
  "PGTARGETSESSIONATTRS",
  "PGUSER",
]);

function readEnvValueCaseInsensitive(
  env: NodeJS.ProcessEnv,
  key: string,
): string | undefined {
  const direct = env[key];
  if (typeof direct === "string") return direct;
  const normalizedKey = key.toUpperCase();
  for (const [candidateKey, candidateValue] of Object.entries(env)) {
    if (
      candidateKey.toUpperCase() === normalizedKey &&
      typeof candidateValue === "string"
    ) {
      return candidateValue;
    }
  }
  return undefined;
}

/**
 * Removes database credential variables copied unchanged from a parent
 * process. A deliberately supplied, different value remains available for a
 * workload-specific least-privilege role.
 */
export function sanitizeInheritedDatabaseCredentialEnv(
  env: NodeJS.ProcessEnv,
  inheritedEnv: NodeJS.ProcessEnv = process.env,
): NodeJS.ProcessEnv {
  const sanitized: NodeJS.ProcessEnv = {};
  for (const [key, value] of Object.entries(env)) {
    if (
      DATABASE_CREDENTIAL_ENV_KEYS.has(key.toUpperCase()) &&
      typeof value === "string" &&
      readEnvValueCaseInsensitive(inheritedEnv, key) === value
    ) {
      continue;
    }
    sanitized[key] = value;
  }
  return sanitized;
}
