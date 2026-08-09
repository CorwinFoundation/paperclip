---
owner: CTO
artifact_type: audit
status: review
updated_at: 2026-08-09
source_refs:
  - BEAAA-21591
  - origin/master@691d7cf72019d63d38978d93749c2ca3a96bbc15
claim_or_decision: Classify every retained remote branch after exact-head deduplication without authorizing content-equivalent branch deletion.
verification: All 205 retained branch heads were fetched and compared with origin/master; four open pull-request heads and master are protected. Founder co-sign remains required before any content-equivalent deletion.
approvals:
  - Founder co-sign required before deletion of any branch classified equivalent_ancestor, equivalent_tree, or equivalent_patch.
version: 1.0
---

# Retained branch equivalence manifest

Baseline: `origin/master@691d7cf72019d63d38978d93749c2ca3a96bbc15`. Generated after the exact same-SHA pass in BEAAA-21591.

## Method

- `protected_master`: canonical branch; never delete.
- `protected_open_pr`: head of an open pull request at generation time; never delete in this pass.
- `equivalent_ancestor`: branch head is reachable from the baseline master history.
- `equivalent_tree`: branch tree equals the baseline master tree.
- `equivalent_patch`: `git cherry` finds no branch-only patch not already represented on master.
- `genuinely_divergent`: at least one branch-only patch remains. This is fail-closed: it does not assert that every patch is desirable or unmerged.

Counts: `equivalent_patch` 53; `genuinely_divergent` 147; `protected_master` 1; `protected_open_pr` 4.

No branch in this manifest was deleted based on content equivalence.

|Branch|Head SHA|Classification|Behind master|Ahead of master|
|---|---|---:|---:|---:|
|BEAAA-12065-fix-prober-fake-signature-drift|275818140423bb0b34189aed26c9fefd61e3308e|equivalent_patch|843|1|
|CorwinFoundation-patch-1|50729ba95ddbddf4ec14d8636e00cc6447b20093|genuinely_divergent|236|1|
|agent/beaaa-14430-ci-gate-semantics|5916db854d143b1ef4f9917ae3b534585753202c|genuinely_divergent|551|2|
|agent/beaaa-15688-replay|ef98dcb009a53ead29b9b9edccde43f8d4bae223|genuinely_divergent|548|2|
|agent/beaaa-15758-wave0-gate|57324e8eaeaa1da2c7d112d2b6c58e94978d252d|genuinely_divergent|711|7|
|agent/beaaa-18691-metadata|afcc1e6381b6969bc9be217dc8843dc926594c92|genuinely_divergent|311|12|
|agent/beaaa-18801-branch-hygiene|83fdf63bb7a0b5e6baa220fe35904f36d806fdb6|genuinely_divergent|229|3|
|agent/beaaa-18846-partner-kit-reconcile|ad11cd7794899e1784ef461d06a10ac72ee3896b|protected_open_pr|113|3|
|agent/beaaa-18997-stale-sha-repair|aa6556e35e96c33fbd70dc75a9adb6d534eae354|genuinely_divergent|212|1|
|agent/beaaa-19024-promotion-authority|bcaaa82d43f00ede374aa6c06eba9e0435c7d032|genuinely_divergent|209|2|
|agent/beaaa-19292-self-contained|f1458971499a72cd8cccb1710e60a38a351f6b73|equivalent_patch|531|1|
|agent/beaaa-19316-internal-canary-rotation|67755e0724f722acef189cfb9ef3e331f8f41dbc|genuinely_divergent|185|7|
|agent/beaaa-19397-clean-integration|6f819b00d4491b90c3a04fc498ca9199d8cf62b6|genuinely_divergent|182|7|
|agent/beaaa-19430-integration-gate|171b4327691218f11b68d620aa50c3c4150e1220|equivalent_patch|135|1|
|agent/beaaa-19532-clean-context-fix|b43356ee8f4e16131eaadd7ac77a1f995c0cddd5|protected_open_pr|185|9|
|agent/beaaa-19571-deterministic-image|0456db95d88823404d5756939a18e7a08a1c94f1|genuinely_divergent|185|8|
|agent/beaaa-20073-voice-e2e|bd7ebabbcc1509b0043b961318fbef46fbebb91c|genuinely_divergent|104|3|
|agent/beaaa-20170-corrected-oci|39295d8057aa381f53146a9913c4e32e37d22c7a|genuinely_divergent|84|1|
|agent/beaaa-20173-qa-ghcr-read|0864685e8f87b3c493fabbb0a4d85debb8540af0|genuinely_divergent|84|1|
|agent/beaaa-20227-current-master-integration|8e67992b3d760c8f86671d04fb77a33f4208337c|genuinely_divergent|28|1|
|agent/beaaa-20227-report-only-remediation|e28721fe021f2231bfd03007a3e784cb31fded64|genuinely_divergent|508|1|
|agent/beaaa-20228-de50-embedded-manifest|96cddfaf1c5823a9a050b2705ab9841af6cb8537|genuinely_divergent|80|5|
|agent/beaaa-20228-embedded-release-identity|b607b3d22ceff6f9b000f9ddd81b9f7a1faa8bb6|genuinely_divergent|80|1|
|agent/beaaa-20231-production-ingest|9adc726872cdea0109e6c8e3e6a39a97a5d5a221|genuinely_divergent|25|1|
|agent/beaaa-20301-provenance-atomic|7215ba8ddf14a327f7772b22bf1af5ad4ab56f20|genuinely_divergent|72|4|
|agent/beaaa-20309-6fea-helper|1fadecef8deb7cee6b3b46f2d37a1ea70d94cf4d|genuinely_divergent|70|1|
|agent/beaaa-20332-state-mode|35bff2074172071d388a2cf0896dd5c02c750b82|genuinely_divergent|70|2|
|agent/beaaa-20725-tau-pilot|1f32a73cb2016257eb169a7d9372f1b1bbe1aeb4|equivalent_patch|63|3|
|agent/beaaa-21021-bounded-phase2-timeout|71353f8e32ef1242987494d4bc7ecf64ef8fba1f|genuinely_divergent|61|2|
|agent/beaaa-21317-registry-forward-fix|51617129d5ca863b52c1159881ee1f7266fb31bd|genuinely_divergent|49|4|
|agent/beaaa-21363-restore-ci|5c6ee8988bf9d8862488c16a7f5ce4d9b21eeb17|genuinely_divergent|4|10|
|agent/beaaa-21389-evidence-ref-redaction|6812b9d11dd8373a6b86ad14679265b3db59bad8|genuinely_divergent|508|2|
|agent/beaaa-21391-synthetic-voice-qa-ref|5215f4c2660972ef07e6058472812a376ec9a870|genuinely_divergent|28|2|
|agent/beaaa-21393-n5-identity|a2968f161443e998d3b734ab09becc3d7bbf5711|genuinely_divergent|53|14|
|agent/beaaa-21407-report-only-live|480d58ea95d8ce7cabd4667a19791ba089877c1d|genuinely_divergent|28|3|
|agent/beaaa-21512-readonly-verify|5d52651a473cada3c639f38c5bec1d6fc4933044|genuinely_divergent|62|1|
|agent/retire-legacy-cd|ffb6b1b683de313ac444f07dd6e0e67d20881991|genuinely_divergent|550|3|
|beaaa-10641-support-voice-fixtures|e3c6f65a40c4b3ea8cc651f63c21600b626cbfd7|genuinely_divergent|910|12|
|beaaa-10642-support-voice-transcript-merge|3392bccc9018607664e5bbe2494c64bf3e184c4c|genuinely_divergent|910|12|
|beaaa-10643-support-voice-four-pillar|9f88bc183467866b67ec5a5f65691020ddb3a550|genuinely_divergent|910|14|
|beaaa-10906-ws1-canon|b1f8006509c29a3356c41c44c3c75c87bfd52282|genuinely_divergent|910|16|
|beaaa-10920-authz-regression|4fcfb8f1e0c03734965626117034ac4acde0c553|genuinely_divergent|910|18|
|beaaa-10945-live-edge-provenance|35b28a1f7cd68b73da73fb1aa4dd1d5b9b414478|genuinely_divergent|910|19|
|beaaa-10953-principal-bootstrap|e6234c3cce9639ae8066daa1f895039c2e5de228|genuinely_divergent|910|20|
|beaaa-11286-malformed-json-422|3ce7ad59f4caf42f36dc469447a5b78dac5b5995|genuinely_divergent|910|21|
|beaaa-11365-collector-v1|576ee3c8b0a66a0194de9dc808b7720484eb8be9|genuinely_divergent|1060|11|
|beaaa-11366-evidence-store-v1|493299f5640639e092e390b87bb1fcd3ab68a69e|genuinely_divergent|907|3|
|beaaa-11372-determination-engine-v1|86de3fca62bd1c31a23d17faa3a008e05f895e56|equivalent_patch|906|9|
|beaaa-11374-rescan-triggers|b68feddfc78d8746c812da363945f5fc1e465f52|genuinely_divergent|1060|12|
|beaaa-11378-evidence-store-read-adapter|c085e6a86ff92c169586f7077659a07dfd7f70d4|equivalent_patch|906|11|
|beaaa-11713-mock-tool-harness|cd3f50bc0ff149d4f1c40d02f5ca87463455f91c|genuinely_divergent|910|28|
|beaaa-11731-session-adapters|dfc933bbcb489fb81dd765a220147ca06a38e3ad|genuinely_divergent|910|30|
|beaaa-11732-sai-redispatch-fix|a10bb098d145bf5303d7f6e403694ed7e275500c|genuinely_divergent|910|29|
|beaaa-11808-tau-bench|3ba37d41920583bc7a3a9da22abdef5ff6f29340|equivalent_patch|871|1|
|beaaa-11838-behavioral-integrity-admittance-wiring|853017b6d4a5dcb2551b72e95a1f5057ef0c0bae|equivalent_patch|867|1|
|beaaa-11858-scan-tenant-authz|74a8b0fc7b228afdf15dd64720892442b111569a|equivalent_patch|866|1|
|beaaa-11916-monitor-readiness-blockers|2fd9bd1a91d52d20c20efdd26133974579eef22b|equivalent_patch|906|12|
|beaaa-11916-monitor-readiness-to-master|60bc24f8e5e10911c87b1645623a1617b03ee62b|equivalent_patch|853|2|
|beaaa-12029-openapi-v1.11.0|0345dcba63b50a8e54a1aa578f0aa31cd0727126|equivalent_patch|845|1|
|beaaa-12029-warranty-event-family-probe-coverage|fdc7f14e9625fda0890bbd45f39678221d29b95c|genuinely_divergent|848|5|
|beaaa-12030-behavioral-golden-fixtures|9797ab6600ec42533344755267d95b03f6c88fb5|equivalent_patch|850|1|
|beaaa-12031-warranty-recipient-integrity|70ec9926a2f384861ee80a9ecf6ae130dcd75e25|genuinely_divergent|850|3|
|beaaa-12058-ci-plugin-install|03742dab7c24323a50ed524da416f9513e757f98|equivalent_patch|847|1|
|beaaa-12061-cosign-reconcile|ccf6095347532231abbd4e6adb6b31f884d24719|equivalent_patch|845|1|
|beaaa-12093-master-candidate|cbc2019a8d3e5b51aaa7a63586781fcbce532915|genuinely_divergent|638|2|
|beaaa-12093-vendor-scan-capability-manifest|123ad9ce26adf2b40edadcd930b7d1331fc605ad|equivalent_patch|838|1|
|beaaa-12659-reconcile-provenance-on-master|fca5c946d2f0c50b17d060fe5bb30894c11c3ba5|equivalent_patch|814|1|
|beaaa-12663-hostinger-deploy-repro|3aa4a16abc732ce5dce5a80d98862e05ffbef28a|genuinely_divergent|910|22|
|beaaa-12895-pin-openai-pyrit|4d19338cb006831526146693cf4e795bb48d022d|equivalent_patch|810|1|
|beaaa-12895-scanlog-migration|2c6051520e1be42091809ed38ef61dad6f538ba6|equivalent_patch|809|1|
|beaaa-13169-scan-envelope-drift|9655d472c0435fd746ecbe7c03109f345628b8cc|equivalent_patch|811|1|
|beaaa-13390-scanlog-backup|521fcfe862e362150cef33e6ab33ad771a8a4df6|equivalent_patch|805|1|
|beaaa-13614-pr61-checks|7f26934cc6ce5447c1bb1564f24584be516c7660|genuinely_divergent|780|1|
|beaaa-13678-probe-registry|c11f26fc463a07d725f631ee27aacb9bdd1a7c8a|genuinely_divergent|910|21|
|beaaa-13678-probe-registry-rebased|9a58c1980ef769a4cefc99cc83b2287d640316e9|genuinely_divergent|791|2|
|beaaa-13693-registry-merge|e169e9fc9fb5024a3527eeac6cb512e389b5b899|genuinely_divergent|767|1|
|beaaa-13761-execution-evidence|cfc48fa961e0e7602b096b6129fa08d54c9f3f08|equivalent_patch|747|1|
|beaaa-13802-master-reconcile|3ea64b1fe7d39983df97793c5d3cc4a28983b3f5|equivalent_patch|737|1|
|beaaa-14087-real-agent-count-gate-v2|b710a82cc301831c698ec82eff1cf9027e9d6450|genuinely_divergent|691|3|
|beaaa-14136-pr74-repair|823685027b8a8489746c7a958b5fa091b974c6b3|genuinely_divergent|734|2|
|beaaa-14394-ci-lifecycle|35cdcfbf2463da5f2b7ce01aeb8945b5b6df46aa|genuinely_divergent|713|1|
|beaaa-14398-f2-fail-closed|48dce0b69a4d31042a755eafe80efd7e3616401b|equivalent_patch|621|1|
|beaaa-14399-lease-until-terminal|295cc944984e94110df080e5592eda9a66553dfe|equivalent_patch|708|1|
|beaaa-14412-pr72-followup|7cf77b5088e06fa583e9bc6e60845aca27bdb195|genuinely_divergent|526|2|
|beaaa-14425-f4|388108b8b5c05bd5737054355e5c90398ef8b801|equivalent_patch|713|3|
|beaaa-14426-f8|1e71a02c3ec549a9eb491e0fd908f0b24a738ac4|genuinely_divergent|713|1|
|beaaa-14426-f8-bg-log|53e4e1e5e2dab5cf54c4e312e1cb60a020da0c80|genuinely_divergent|712|1|
|beaaa-14427-lease-lifetime|455439400ec0ec980abf3c954a6e687079d1b2a0|genuinely_divergent|734|6|
|beaaa-14428-f7-scanlog-lock|79ec259cb35bfb5d3b5449e74a698c9d6adb97b4|genuinely_divergent|712|1|
|beaaa-14428-scan-log-serialize|90042b55cb763e5007bd2630d025022b81853ce1|genuinely_divergent|712|1|
|beaaa-14428-serialize-scanlog|0c03f2684cc2b5b9b378110a71e2ae07c1b9006d|genuinely_divergent|708|1|
|beaaa-14429-f9-jcs-float|f0744387b7271f0529c7e7f21a6d28307f0bd27d|genuinely_divergent|712|1|
|beaaa-14429-jcs-float|b14533b052a5e5e8d8aaf2ddb7719429f0614212|genuinely_divergent|713|1|
|beaaa-14432-f5-ssrf|ba4dc0f962fafd7c97dbeba1b5089a08ff42986a|genuinely_divergent|712|3|
|beaaa-14455-f5-ssrf|115b335e0715859f86fcdc22ea27825d7ca2bf8e|genuinely_divergent|711|2|
|beaaa-15606-two-deploy-observation|407ec92306956c689fec3af7642d940e12d2933a|equivalent_patch|549|1|
|beaaa-15740-integration|726b8a6a3d6b66ed26647c153767c2f4d312bf7c|equivalent_patch|549|1|
|beaaa-15764-m2-m3-relock|77a731e618fcba34498c977b5c2d7b7f64dc99d6|genuinely_divergent|548|3|
|beaaa-15764-m2-only-relock|5d5c32fd105da45a038746c530adc0748b99dd6e|genuinely_divergent|548|3|
|beaaa-15780-monotonic-sequence|0dcdb5b5301b94662298ee1b8c233bcb20b04bda|genuinely_divergent|547|2|
|beaaa-16263-seq20-runbook-v1.3-clean|744c6adecb47a67ad109c7b80390a8548dfb8065|equivalent_patch|680|1|
|beaaa-16269-seq20-lineage|4cf4a418bf927cc61c0e58dca177bdc102305803|equivalent_patch|500|1|
|beaaa-16277-aiuc-combined|1ddb991ed9d3301fb3aa9098228d41776fbae848|genuinely_divergent|526|4|
|beaaa-16316-reconcile|db9722825ce5360c17424a62d60c16c50a4bb6da|genuinely_divergent|680|2|
|beaaa-16318-seq20-consolidation|f92130fcc5063531d0d369ec20f849c9e91b8730|genuinely_divergent|680|2|
|beaaa-16489-production-integration|eb7833a81578ad47f46648e7460bf65097d99e0d|equivalent_patch|526|1|
|beaaa-16489-production-integration-r2|582762ca2b3b1eab6da72213141cdd425b5d2b3f|equivalent_patch|524|1|
|beaaa-16489-production-integration-v2|422d1e704a4f64df1f0bc5f0bcd1e4159307893d|equivalent_patch|524|1|
|beaaa-16598-deploy-path|737721965756e83ec6e7acfb1c61206c64c49ab9|genuinely_divergent|526|5|
|beaaa-16598-integration-r7|d8871ff6f7609c04307a5cba4ae76d803650256f|equivalent_patch|509|1|
|beaaa-16617-db-worker-candidate|7c96cc171e9237ff5e7a6fdde51fbd447e6bcd40|genuinely_divergent|796|3|
|beaaa-16631-m3-band-regression-evidence|9670fd6b6069acdba0c4e0418cf9c7c112301ecd|genuinely_divergent|548|4|
|beaaa-16631-m3-relock|94efffe599ca9d4a35a00381279abc81578b4fc9|genuinely_divergent|548|6|
|beaaa-16647-aiuc-count-freeze|a426079d2aa6da7d1983fa84eec32ba1e443d081|genuinely_divergent|526|8|
|beaaa-16647-master-integration|506b93ce75ea08c26cd313c5ccaa41a0df2437d3|genuinely_divergent|513|4|
|beaaa-16662-aws-cross-provider-integration|632100e8b20b6f73d621542bf5ba2b725a0a5894|genuinely_divergent|521|1|
|beaaa-16713-approval-provenance|afecd4859890ac3fd6472f24a0527bb578c72f95|genuinely_divergent|711|8|
|beaaa-16713-approval-provenance-v2|73e68443c8e2e3390edf9be3152a2a1f667bde86|genuinely_divergent|711|9|
|beaaa-16715-approval-chain-hardening|49c344de9c341fa9ef2f0d2f8c221e59bd0557c2|genuinely_divergent|711|8|
|beaaa-16717-release-workflow-test-drift|b228170972a9b1c2a7fa9f8cc980df84f17edfd4|genuinely_divergent|513|1|
|beaaa-16737-queue-worker-finalization|acfea12241de825b3bbad12467b451b4933a92b2|genuinely_divergent|796|10|
|beaaa-16768-reconcile|b0a4b0aa0caae7eff82bc9f97d1d2d9626dc8c14|genuinely_divergent|505|1|
|beaaa-16834-reconcile|77cb58d41f36a72334f5aca7ddc7649dd6eff7da|genuinely_divergent|494|1|
|beaaa-16834-recut-2|ee8beaf3e5a675e7c59a4df272fb764be1b51993|genuinely_divergent|487|1|
|beaaa-16834-recut-3|fee8c12f30b53146dc5ee012fcd0575322b33049|genuinely_divergent|270|1|
|beaaa-16852-r2-recut|be5e4366f0a7a28b56e62934196babd858e18f69|genuinely_divergent|494|1|
|beaaa-16853-provenance-fix|d2d53b9c65668ff44a2e3fc9c3dd3c23a978a76c|genuinely_divergent|494|1|
|beaaa-16864-n5-provenance-accounting|c221c1d0ac25d99988fe317cdadf81b37150a98f|genuinely_divergent|796|12|
|beaaa-16864-v16-n5-provenance|d13f953af862a4c521ac5cf922c56caa92969b10|genuinely_divergent|472|9|
|beaaa-16910-recut|1794dda6c4ee35b1f4aee33764cae7ed83e2f2e9|genuinely_divergent|490|2|
|beaaa-16914-terminal-webhook-retry|5627d97e75ae3e1a18eb63b7eb05c2f61e7f4297|genuinely_divergent|796|13|
|beaaa-16915-v1.6-recut|de572b6b4192770f50e56deb1018bbfefcb6734b|genuinely_divergent|472|8|
|beaaa-17196-r6-lineage-fix|7a4049799f32921163804f745cf7909fac47e645|genuinely_divergent|481|15|
|beaaa-17222-wave2-retention-canonical|876fead89786e10de728447114d8e307516fb8f0|genuinely_divergent|481|9|
|beaaa-17719-master-integration|0b97f8e8f41d0630f0909ee9f017301adfd62344|genuinely_divergent|379|8|
|beaaa-17890-repair-passive-demo-merge|dd01a7ab08e20fd23aa371deac9e9e82a5831a62|genuinely_divergent|242|2|
|beaaa-17896-webhook-safety|10c0a7018cbc67417ab0520e8f13be583653c898|equivalent_patch|271|2|
|beaaa-17904-ci-zero-failure|9c80031b8a83705807db0eb21722255ba9984872|genuinely_divergent|611|7|
|beaaa-17964-deploy-identity-ledger|1235b45cd33a495b96417468dbb5e2ee53f45a4e|genuinely_divergent|805|1|
|beaaa-18042-remediation|f0bf6510e607622de9cc72e60d587bc76cae8cbd|genuinely_divergent|260|4|
|beaaa-18122-partner-kit|549511a27d5c6501b348fa1d425ca9a7e6b3f371|genuinely_divergent|242|3|
|beaaa-18616-clean-candidate|9b2a51afcc37394b29bd11b5d76d9159deb383ae|genuinely_divergent|234|7|
|beaaa-18817-evidence-receipt|c315c600ea84d59597b742c10e97c6afd01ac916|genuinely_divergent|232|2|
|beaaa-18825-evidence-receipt-candidate|a14e16b416eac4441690885350077ddbd19e80ef|equivalent_patch|231|1|
|beaaa-18825-evidence-receipt-projection|c6fc74fc906e95e582a9082d2039e2776b34035a|equivalent_patch|229|1|
|beaaa-18851-ci-fix|081edc4bfe5c705bd05b84fc5180fe9da5940d23|genuinely_divergent|229|4|
|beaaa-18889-reconcile-current|ea932f42fe3e093bf9067ad2c13201a662a8b30c|genuinely_divergent|223|2|
|beaaa-18991-detection-latency-recut|13179bd726b58b92133dafdfb7274cfeffd49a57|equivalent_patch|219|1|
|beaaa-18993-corrected|f74b0490bce0f5ae3eb1ec47fe65f048729df6b5|genuinely_divergent|311|12|
|beaaa-19098-agent-egress|9ff0295644f15aa3f306ea26319ca5f82b691f92|genuinely_divergent|202|1|
|beaaa-19340-telemetry-boundary|9784a3b1508dc25089f710c30f9dc30c333b631c|genuinely_divergent|200|1|
|beaaa-19606-telemetry-descriptor|307dabb5ec4f3e87c857690872de7b20804ca524|genuinely_divergent|199|2|
|beaaa-19988-sha-tag-signer|4a5da93993707fc83d6670fcee7997d11a385eec|protected_open_pr|101|1|
|beaaa-19990-voice-metadata|1ab05cefb7e719c5c5f457149ec2708fae37dd33|genuinely_divergent|103|2|
|beaaa-20116-oss005-semantic-binding|9ff5934b90efbd570a357d7794118ae0688d7cd9|equivalent_patch|197|1|
|beaaa-21115-release-candidate|233445d3b3cb5c7825bea8e181e39c6b0e297b0d|genuinely_divergent|53|13|
|beaaa-2869|34f6a6b065cf004168322182608a6df01f9cce69|genuinely_divergent|1106|2|
|beaaa-2942-darwin-real-build|9d10b5b42917559983b2ca87ee4b5c16ce2b1bc9|genuinely_divergent|1094|9|
|beaaa-3055|14a5c9de693f838ff22e8cbe33598086094f74b2|genuinely_divergent|1098|1|
|beaaa-3057|432e3bfd745802d2f33cfd82fe22c59c7dfb9d26|genuinely_divergent|1098|1|
|beaaa-3057-integrated|ec84a305fefbef7fede0e06eecdf265c2ad8fe0c|genuinely_divergent|1094|1|
|beaaa-3060|56c722be2459dea70d398860b61d0a5a66596526|genuinely_divergent|1098|4|
|beaaa-5118-brain-storage-substrate|996476bf995adbb66907f160c5527fc8141a3331|genuinely_divergent|1094|1|
|beaaa-5207-r2-ops|ee678c2d7a6b35509a95fbbf6d05896ac7f7947e|equivalent_patch|1092|3|
|beaaa-5208-r2-rob|79c3c52ac2ccd3aff8e942f04924cc503c7a06eb|equivalent_patch|1087|3|
|beaaa-5209-r2-bias|2fbf38011f251c56e008780c42a60348da94e5fa|equivalent_patch|1081|3|
|beaaa-5210-r2-gov|9150915d71ee4bd717e2f0fe967f50005a4ccf36|genuinely_divergent|1087|3|
|beaaa-5264-audit|a85a77e3f23984241a1a7af0e0da3b025817cdf4|genuinely_divergent|1087|1|
|beaaa-5265-audit|c5ad7f18e60d7e244ee3ea21bb8d378d12966415|genuinely_divergent|1094|2|
|beaaa-5266-audit|b5b4b6fac9ad1c64fe63fecccabd9ec5c426d49f|genuinely_divergent|1087|2|
|beaaa-5267-rollup|8bfd21d5e0f7cb7aaf12ad4247ea67ab5dc79961|genuinely_divergent|1077|1|
|beaaa-5271-pr-unit-test-ci|0d177e86ceda48dfa28aa12c7838f80bc0752d37|equivalent_patch|1076|1|
|beaaa-5272-windows-portable-artifacts|5643ea50beaacdc29bfba4ed6faa39a3a6f9452e|equivalent_patch|1076|2|
|beaaa-5275-are-total-rename|8366f92fb6d9b9703c65f807d99dcb9483cd5720|genuinely_divergent|1078|2|
|beaaa-5324-evidence|b3e6521aef28dc948689d1ecb7c6b591c2049b61|genuinely_divergent|1077|1|
|beaaa-5325-adr-0029|656433bc7c6e9eeef9a33fd6d34143e9b25a7c34|genuinely_divergent|1078|1|
|beaaa-5448-cutover-runbook-v2|69fd9b4eee4b0ad4f921a83b813b2d600bc3699e|genuinely_divergent|1063|5|
|beaaa-5487-ci-pytest|e2eeb8c4baa6e130a5c0e150e691bce3a304a42f|genuinely_divergent|1055|5|
|beaaa-5528-customer-runner-streaming|dd7b29570c338661dc9eefe60950184786dcdb2a|equivalent_patch|1054|2|
|beaaa-5740-track-c-remediations|7bc18cf5e4cee6e299883a2a9bd47da28186d54e|equivalent_patch|1042|1|
|beaaa-5757-r5-prompt-injection-harness|0c403293e96c3ed258ef58e7deaafca55be0c147|equivalent_patch|1041|1|
|beaaa-5865-p4-detector|9993b36ea8eb9e58ffa5c1a3e21841a2bcdaf27e|genuinely_divergent|1029|2|
|beaaa-5974-collector-build2-firewall-mutation|17c040d0af9e8269cb6b1a420fb8be9a9c537c75|genuinely_divergent|1008|1|
|beaaa-6162-timeline-read|30189a734514ea377c2f14b28182f989d1f84253|genuinely_divergent|1019|1|
|beaaa-6216-rule-b-ordering|2f5cbc09a8197a1617b6a0f12dfa5abcad752105|equivalent_patch|1032|1|
|beaaa-7791-syllable-request-construction|1c09867a05db57bfcb8a2a25aa64e53a85f7ff35|genuinely_divergent|1028|6|
|beaaa-7819-rebase|2a23f403bad93902074db807715d0649353994dc|genuinely_divergent|967|2|
|beaaa-782-secrets-activation|6b40a26be4175e7c0df5eeb9c68c043fac179c08|genuinely_divergent|1068|7|
|beaaa-7846-syllable-retry|017ed420b46c0e0a0695ba6b267a5574bc3f0c7f|equivalent_patch|956|1|
|beaaa-7890-additive-hardening|bb2fbc1707f958a190010df583e8cd7d61f79d50|genuinely_divergent|952|1|
|beaaa-7890-egress-fix|8a25f7f3ecfe046ab6da07ab1aa8fad7a8a84769|equivalent_patch|950|1|
|beaaa-8219-adr0037-phase2-applied|0cfe3cfc0b1bd2168df3dacf1a7ff5d1544e9bb1|equivalent_patch|929|1|
|beaaa-8219-master-protection-adr|3e23791c8e3c6ae7b782700e63a6974ff84c7587|equivalent_patch|938|1|
|beaaa-8225-consolidated-blocker|fcc8d65a4fe640729c2542065275fb86ba012769|genuinely_divergent|936|1|
|beaaa-8464-restore-brand-backbone|c24c72de9cef29f5d9ed4b41c7bbab41567bb12c|genuinely_divergent|1060|11|
|codex/fix-sequence21-release-integrity|e2f6779b70494eddb19780226bb22cefbbb03736|genuinely_divergent|538|3|
|feature/beaaa-759-bias-pack-v0.3-v1-path-b|441dbe5b8b2756b847e4b227fb8ffdbe18fb6f42|genuinely_divergent|1126|5|
|fix/beaaa-16094-master-signing-identity|44ea852b6478a161089061f7f06af2c35ae2ca1a|genuinely_divergent|541|1|
|fix/demo-lifecycle-auth-v4|dcea859ef502db9973fc98c62d6b6d144a8f0a65|genuinely_divergent|544|3|
|fix/full-scan-dispatch-2201|55fe5cfac4f47ebd39e69df680ae608c0a5c4928|protected_open_pr|4|1|
|master|691d7cf72019d63d38978d93749c2ca3a96bbc15|protected_master|0|0|
|ops/beaaa-20028-ghcr-publish-78502893|9715b2d7107091e4a90f5f10f34b00feb37665ea|genuinely_divergent|95|2|
|recovery/version-control-source-of-truth-v2|9a3466e1773f21040bf971fd7b3187faf10bde12|equivalent_patch|713|1|
|worktree-beaaa-14400-webhook-outbox|c95f7ba64660175f93613cff128d3d0fb6de1639|genuinely_divergent|712|3|
|worktree-beaaa-16598-deploy-path|7baaa24416f6232852d58430532a18e94a90b365|genuinely_divergent|526|7|
