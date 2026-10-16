# Customer Operations targeted marker-behavior pack v2

This corrects v1. It proposes `customer-ops.data-boundary-marker-behavior@2.0.0` as a limited report-only plan decision, not as proof of datastore isolation or PII detection.

The source is released Scanner `0.5.0` at tag `sha-97659f53a83f7ef3fb084fd78ff46d3ed9ad851b` and full SHA `97659f53a83f7ef3fb084fd78ff46d3ed9ad851b`, scope map `scanner_scope_map_v1`, catalog `authoritative-probe-registry@1.1.0+source-closure-655dcc6f2ff74f9cb5f32d46b9e5cf8a129c16b5`, family `cross_customer_data_exposure@0.2.0-candidate`, and the eight exact case revisions in the JSON.

## What it measures

The runtime sends synthetic sandbox scenarios to the named real customer target. Each case runs five replicates and grades whether the response emits the expected synthetic marker. Eight cases therefore make **40 customer-endpoint calls**. There are no judge calls, but customer endpoint cost is provider/customer-specific and is not assumed to be zero.

`PASS` and `FAIL` are terminal, gradeable, evidence-bearing case verdicts that satisfy completion. `INCOMPLETE` and `INVALID` do not. Every result remains `reported_not_scored`, `score=None`, and `synthetic_only`; no numeric score is activated. A FAIL is a completed adverse result. It must be reported, not converted into an incomplete result.

The pack supports case-level comparison only where target identity, tenant/environment, case ID and revision, five-replicate contract, and evidence all match. It never supports a numeric, pillar, or full-score comparison.

## Projection and activation

The implementation projects `case_id_or_probe_id` from the released catalog into Scanner `case_id` rows and verifies catalog revision, family revision, case stable IDs, executable flags, the legacy `bench_wrapper` runtime path, and target constraints. A policy-authored boolean does not activate the pack. `targeted_content_approved` stays false until a Founder decision is independently bound to the replacement policy revision, this registry key, and this artifact's SHA-256.

Unknown/mixed changes, unsupported targets, identity mismatch, revision drift, or any missing/unevidenced/INCOMPLETE/INVALID case route to a full scan. Synthetic or fixture target substitution is prohibited.

## P14 gap and recommendation

This pack does not verify actual datastore isolation, cross-tenant access controls, or a production PII detector, and it does not produce a BackBond Score. It counts as the P14 targeted scan only if the Founder explicitly accepts that limited evidence contract on exact revisions.

**Recommended Founder decision:** Approve the exact v2 pack only as a 40-call, report-only synthetic-marker behavioral check against the named real target, with PASS/FAIL case-level comparison and the stated P14 gaps.

## Integrity pins

- Data JSON: `4f85eb38626c19f08a8a51ad9a6d939ed3d0db42b18114c3c1e4a9f6764fe8a3`
- Released catalog: `6f033b438056b96fa714c2b9a05f2c186bdcd4c36a915878893514623c0ce4ae`
- Family mapping: `0fa05334b1e5e42f1176c23ef09519142a6153713a4305cbbf34852a1f762056`
- Released family runtime: `6d98742ffd67036eadcb9c066cbf13ff52ffbd4eb72f0f2041ddacf3bba2b5fb`
- Released registered executor: `467a954408784886b2b71d3ef80ae995448a281240dab46888e2fbfbb2907f73`
