import { describe, expect, it } from "vitest";
import { issueOperatorScopeSchema, permitsIssueOperatorRead } from "../services/issue-operator-scope.js";

const companyId = "60bcddf3-87be-4197-8f1c-846bd967db11";
const issueId = "836ee384-423e-421d-a590-6a2c5b6f385f";
const otherId = "eae63d69-19d3-4fc2-bb26-93f196cbe2b7";
const scope = { kind: "issue_operator_read_only", companyId, issueIds: [issueId] };
const request = { scope, method: "GET", originalUrl: `/api/issues/${issueId}`, hasBody: false, issueCompanyId: companyId };

describe("issue operator read scope", () => {
  it("allows only the bound issue in its bound company", () => {
    expect(permitsIssueOperatorRead(request)).toBe(true);
    expect(permitsIssueOperatorRead({ ...request, issueCompanyId: otherId })).toBe(false);
    expect(permitsIssueOperatorRead({ ...request, originalUrl: `/api/issues/${otherId}` })).toBe(false);
  });
  it.each(["POST", "PATCH", "DELETE", "PUT", "HEAD", "OPTIONS"])("denies %s", (method) => {
    expect(permitsIssueOperatorRead({ ...request, method })).toBe(false);
  });
  it.each(["?include=all", "/comments", "/attachments", "/", "%2fcomments", "#fragment"])("denies suffix %s", (suffix) => {
    expect(permitsIssueOperatorRead({ ...request, originalUrl: request.originalUrl + suffix })).toBe(false);
  });
  it.each(["/api/issues", "/api/issues/BAC-71", "/api/companies/" + companyId + "/issues", "/api/issues/../issues/" + issueId])("denies route %s", (originalUrl) => {
    expect(permitsIssueOperatorRead({ ...request, originalUrl })).toBe(false);
  });
  it("denies bodies and malformed scopes without falling back to full access", () => {
    expect(permitsIssueOperatorRead({ ...request, hasBody: true })).toBe(false);
    for (const invalid of [null, {}, { kind: "standard" }, { ...scope, issueIds: [] }, { ...scope, issueIds: [issueId, issueId] }, { ...scope, companyId: "BAC" }, { ...scope, isInstanceAdmin: true }]) {
      expect(permitsIssueOperatorRead({ ...request, scope: invalid })).toBe(false);
    }
  });
  it("limits a grant to three explicit issues", () => {
    expect(issueOperatorScopeSchema.safeParse({ ...scope, issueIds: [issueId, otherId, companyId] }).success).toBe(true);
    expect(issueOperatorScopeSchema.safeParse({ ...scope, issueIds: [issueId, otherId, companyId, "4688e2e2-c857-4ef1-b5a7-28356f7f5c00"] }).success).toBe(false);
  });
});
