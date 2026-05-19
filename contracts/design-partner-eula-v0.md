---
contract_id: design-partner-eula-v0
title: ARE Scanner Design-Partner End-User License Agreement (v0 draft)
status: draft — CBDO review pending
draft_author: Scanner Engineer (d8bbc3b5) for BEAAA-567 §8
adr_authority: ADR-0011 §3.6 + §4 (tamper-resistance posture)
classification: rule-change (binding contract terms for Phase-1 design partners)
co-owns: CBDO (intake motion, partner-relationship surface — `contracts/design-partner-overlap-v1.md@v1.0`)
date: 2026-05-19
scanner_version_when_drafted: 0.3.0+T-CLOSE
review_required_from:
  - CBDO — partner intake fit, NDA dovetail
  - CTO — tamper-resistance posture (§4 of ADR-0011 must remain accurate)
  - Counsel (if engaged) — enforceability of §3 prohibitions and §6 audit logging consent
---

## Preamble

This End-User License Agreement (the "EULA") governs your use of the
ARE Scanner design-partner binary (the "Binary") delivered to you by
IFA Inc. ("IFA") under the Phase-1 design-partner program. By typing
"I ACCEPT" at the prompt presented on first invocation of the Binary,
you ("Partner") agree to the terms below. The acceptance record is
written to a local state file keyed on the Binary's build id and is
recoverable by IFA support on request.

This EULA is delivered alongside, and is subordinate to, the
out-of-band Non-Disclosure Agreement signed between Partner and IFA in
connection with the design-partner engagement (the "NDA"). Where the
NDA is silent, this EULA governs. Where the NDA and this EULA conflict,
the NDA governs unless the conflict concerns runtime behavior of the
Binary itself (in which case this EULA governs as to that behavior).

## 1. Grant of license

IFA grants Partner a limited, non-exclusive, non-transferable,
non-sublicensable, revocable license to use the Binary solely for:

a. Running ARE scans against AI agents that Partner owns and operates,
   or that Partner has been explicitly authorized in writing to
   evaluate; and

b. Internal review and discussion of the resulting scan output with
   Partner's own staff and IFA personnel.

No other use is licensed. In particular, the license does NOT extend
to evaluating third-party AI agents without their consent, to embedding
the Binary in any product or service Partner offers to third parties,
or to running the Binary in any way that benefits a party other than
Partner.

## 2. Time-bounded license

Partner's license is bounded by the expiry date recorded in the Binary's
build manifest. By default this is the end of the Phase-1 design-partner
buffer (2026-06-30 as of this draft). After expiry, the Binary's
license-validation callback will refuse to issue a fresh attestation
and the Binary will fail closed on the next invocation. Renewal is at
IFA's discretion and is delivered via a fresh build through the same
channel — Partner is not authorized to modify the expiry of an
already-delivered build.

## 3. Prohibited conduct

Partner agrees not to, and not to permit any third party to:

a. **Reverse-engineer the Binary.** Decompile, disassemble, extract the
   bytecode, recover the source corpus, recover the rubric, or otherwise
   reconstruct any non-public part of the Binary's internals.

b. **Redistribute.** Share the Binary, or any extract from the Binary
   (including the corpus, the rubric, judge prompts, or any subset of
   them), with any third party, including affiliates of Partner not
   covered by the NDA. Each Binary is uniquely watermarked and traceable
   to Partner.

c. **Tamper with the license gate.** Patch, hex-edit, network-isolate
   (DNS blackhole, firewall block), stub the license endpoint, or
   otherwise prevent the Binary from reaching IFA's license-validation
   service and receiving a valid attestation on every invocation. An
   invocation that scores without a valid attestation is a material
   breach.

d. **Run against unauthorized targets.** Run the Binary against any AI
   agent that is not owned and operated by Partner, or for which
   Partner does not hold the necessary written authorization from the
   agent's owner.

e. **Misuse output.** Publish ARE scores, sub-scores, or any portion of
   ARE Scanner output externally, in any form (marketing copy, public
   blog posts, sales collateral, comparative tables, regulatory
   filings), without IFA's prior written consent. Internal use within
   Partner and joint use with IFA personnel is permitted.

f. **Bypass the EULA acceptance flow.** Distribute a Binary together
   with a pre-populated EULA acceptance state file, or otherwise enable
   a recipient to skip the on-first-invocation acceptance step.

## 4. Per-binary watermark

Each Binary delivered to Partner is uniquely watermarked. The watermark
is recoverable from any extracted copy of the corpus on disk and is
mapped server-side to Partner's identity. Partner acknowledges and
agrees that any leaked copy of the corpus, however obtained, that
matches Partner's watermark will be treated as originating from
Partner.

## 5. Revocation

IFA may revoke Partner's license at any time and without prior notice
for material breach of this EULA or the NDA, for cessation of the
Phase-1 design-partner program, or for security reasons (suspected
compromise of the signing key, anomalous usage pattern, etc.). On
revocation, the next invocation of the Binary will fail closed. This
behavior is not a defect; it is the contracted behavior. Partner waives
any claim of remedy that depends on continued Binary operation after
revocation.

## 6. Audit logging

Partner consents to IFA logging the following metadata for each
invocation of the Binary, for a retention period of ninety (90) days:

* Partner identifier;
* Binary build identifier;
* Per-invocation scan identifier;
* UTC timestamp;
* Source IP address visible to the IFA endpoint;
* License-validation outcome (`valid` / `revoked` / `expired` /
  `partner_mismatch` / `binary_blacklisted` / `unknown_key`).

No transcript content, no scan inputs, no scan outputs, and no
personally identifiable information beyond the source IP address are
logged. The audit log is used for billing reconciliation, anomaly
detection, and (where applicable) enforcement of this EULA and the NDA.

## 7. No warranty; limitation of liability

The Binary is provided "AS IS" without warranty of any kind. IFA
disclaims all implied warranties to the extent permitted by law. IFA's
aggregate liability under this EULA is limited to the fees Partner has
paid IFA for the design-partner engagement during the twelve (12)
months preceding the claim — which, in the Phase-1 design-partner
program, is generally zero unless Partner has separately contracted
for paid services with IFA.

## 8. Termination

This EULA terminates automatically on:

* Expiry of the license per §2;
* Revocation per §5;
* Conversion of Partner to a paying customer (at which point a separate
  paying-customer license takes effect and the design-partner Binary's
  key is revoked); or
* Partner's written notice of withdrawal from the design-partner
  program.

On termination, Partner shall (a) cease all use of the Binary and the
corpus, (b) delete all copies of the Binary, the corpus, and any
derivative within thirty (30) days, and (c) certify deletion in writing
on IFA's request. Sections 3, 4, 6, 7, and 9 survive termination.

## 9. Governing law and disputes

[Counsel TBD — placeholder. Recommend IFA's home jurisdiction with
arbitration carve-out; CBDO to confirm against existing NDA template.]

## 10. Acceptance

Acceptance is recorded by typing the exact string `I ACCEPT` at the
prompt presented on the Binary's first invocation. The acceptance
record is written to the host's user state directory under a filename
keyed on the Binary's build id and includes Partner identifier and
the UTC timestamp of acceptance. Subsequent invocations check this
record and do not re-prompt unless the record is missing.

## Drafting notes (delete on CBDO sign-off)

* CBDO: please confirm the design-partner-overlap-v1 intake motion's
  consent flow does not double-prompt against §6 of this EULA. If a
  partner has already signed a separate logging consent in the NDA
  packet, we may want to weaken §6 to a reference rather than a
  re-prompt.
* CBDO: §3(e) is intentionally restrictive on external publication of
  ARE scores. If a partner is paying for a "right to publish" carve-out
  in any future engagement, that becomes a paying-customer term and
  does not belong in this design-partner EULA.
* Counsel (if engaged): §7 limitation-of-liability assumes a
  zero-or-near-zero fee structure for Phase-1 design partners. If a
  partner contracts for paid services concurrently, that contract's
  limitation should govern and §7 should defer to it.
* CTO: §3(a)–(c) match the prohibitions named in ADR-0011 §3.6 and
  §4. If the ADR's posture changes (e.g. additional obfuscation is
  added in a future implementation child), §3 must be reviewed for
  consistency before that change ships.
