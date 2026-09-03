import { describe, it, expect } from "vitest";
import { kubernetesProviderConfigSchema, parseKubernetesProviderConfig } from "../../src/types.js";

describe("kubernetesProviderConfigSchema", () => {
  it("accepts inCluster=true with no kubeconfig", () => {
    const parsed = parseKubernetesProviderConfig({ inCluster: true });
    expect(parsed.inCluster).toBe(true);
    expect(parsed.namespacePrefix).toBe("paperclip-");
    expect(parsed.imageAllowList).toEqual([]);
    expect(parsed.egressMode).toBe("standard");
    expect(parsed.serviceAccountName).toBe("paperclip-tenant-sa");
    expect(parsed.jobTtlSecondsAfterFinished).toBe(900);
  });

  it("accepts an operator-pinned service account name", () => {
    const parsed = parseKubernetesProviderConfig({
      inCluster: true,
      serviceAccountName: "pc-canary-auditor-dbiso-v1",
    });
    expect(parsed.serviceAccountName).toBe("pc-canary-auditor-dbiso-v1");
  });

  it.each(["UPPERCASE", "-leading-hyphen", "trailing-hyphen-", "two..dots"])(
    "rejects invalid service account name %s",
    (serviceAccountName) => {
      expect(() =>
        parseKubernetesProviderConfig({ inCluster: true, serviceAccountName }),
      ).toThrow(/DNS-1123/);
    },
  );

  it("accepts inline kubeconfig", () => {
    const parsed = parseKubernetesProviderConfig({
      inCluster: false,
      kubeconfig: "apiVersion: v1\nkind: Config\n",
    });
    expect(parsed.kubeconfig).toContain("apiVersion");
  });

  it("rejects when neither inCluster nor any kubeconfig source is set", () => {
    expect(() => parseKubernetesProviderConfig({ inCluster: false })).toThrow(
      /requires one of `inCluster` or `kubeconfig`/,
    );
  });

  it("rejects invalid companySlug", () => {
    expect(() =>
      parseKubernetesProviderConfig({ inCluster: true, companySlug: "INVALID UPPER" }),
    ).toThrow();
  });

  it("rejects egressAllowCidrs entries that are not valid CIDR", () => {
    expect(() =>
      parseKubernetesProviderConfig({ inCluster: true, egressAllowCidrs: ["not-a-cidr"] }),
    ).toThrow(/CIDR/i);
  });
});
