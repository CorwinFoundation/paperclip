import { z } from "zod";

/** Credential policy only. Provisioning must verify every issue's company before
 * persisting this scope. This module does not authenticate or grant board access. */
export const issueOperatorScopeSchema = z.object({
  kind: z.literal("issue_operator_read_only"),
  companyId: z.string().uuid(),
  issueIds: z.array(z.string().uuid()).min(1).max(3)
    .refine((ids) => new Set(ids).size === ids.length, "Duplicate issue IDs"),
}).strict();

/** Call before general API routing, then verify the loaded issue's company.
 * Only canonical UUID detail reads are supported. Collections, subresources,
 * query parameters, request bodies and all mutations fail closed. */
export function permitsIssueOperatorRead(input: {
  scope: unknown;
  method: string;
  originalUrl: string;
  hasBody: boolean;
  issueCompanyId: string;
}): boolean {
  const parsed = issueOperatorScopeSchema.safeParse(input.scope);
  if (!parsed.success || input.method !== "GET" || input.hasBody) return false;
  if (input.issueCompanyId !== parsed.data.companyId) return false;
  const match = /^\/api\/issues\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/.exec(input.originalUrl);
  return Boolean(match && parsed.data.issueIds.includes(match[1]));
}
