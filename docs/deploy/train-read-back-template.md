# Production train — live read-back template

- artifact_id: beaaa-28631-train-read-back-template
- owner: CTO (release captain)
- authority: BEAAA-25170 Founder directive; BEAAA-25173 train runbook; CEO ruling on BEAAA-28631, 2026-09-02
- external_scope: out

Fill this in after activation, against the live host, and attach it to the
implementation parent. Every line records an observed value or FAIL. No line may
be answered with "unverifiable" — a check that cannot be obtained is removed or
replaced within one train cycle, per the CEO standing rule on BEAAA-28631.

## Identity

| Field | Value |
| --- | --- |
| Train / parent issue | |
| Candidate SHA (approved) | |
| Host / environment | |
| Activation or rollback | |
| Read-back run at (UTC) | |

## Live read-back

1. **Release identity.** `GET /scanner/v1/release-identity` — record `commit_sha`
   and `image_digest`. `commit_sha` must equal the approved candidate SHA.
   Mismatch is a stop condition.
2. **Health.** Record the health probe used and its status.
3. **Registry / count.** Record the live registry count and the expected count
   from the candidate. Any drift is a stop condition.
4. **Capability.** Record the capability surface read-back for the shipped
   change.
5. **Edge contract smoke.** Run the edge contract smoke against
   `docs/deploy/edge-contract.yaml` at SHA `<contract file SHA>` and record every
   route as PASS or FAIL:

   ```
   python3 scripts/edge_contract_smoke.py docs/deploy/edge-contract.yaml
   ```

   | route id | method | path | expected | observed | PASS/FAIL |
   | --- | --- | --- | --- | --- | --- |

   A route that is absent, or answers in a different status class, is a FAIL.
   Any entry under `must_not_serve` that answers 2xx is a FAIL. One FAIL fails
   the read-back.

## Disposition

- Result: PASS / FAIL
- Stop conditions triggered:
- Rollback executed:

---

Removed 2026-09-02 by CEO ruling on BEAAA-28631: the OpenAPI smoke line
(`GET /openapi.json` at the public edge). No schema document is published at the
edge, so the line was structurally unobtainable and always read "unverifiable".
The property it was meant to measure — that the deployed build serves the
contract QA approved — is now measured by the edge contract smoke above.
Publishing a schema surface at the edge remains its own scoped decision.
