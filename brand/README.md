# Brand canon — BackBond

This directory is the **single source of truth** for the BackBond brand. Every external-bound asset (deck, one-pager, web copy, PDF, press release, partner kit, investor doc, design comp) MUST conform to the document pinned here.

## Canonical location (resolve this before "the project root")

The gate bundle (`brand/` + `scripts/verify-brand-backbone.sh`) exists in two checkouts. They are byte-identical and both hash to the same pin, but only one is version-controlled. When an instruction says "run the verifier from the project root," resolve it as follows:

| Checkout | Path (root) | Role | Authority |
|---|---|---|---|
| **Scanner code repo (`are-scanner`)** | repo root -> `brand/`, `scripts/verify-brand-backbone.sh` | **Canonical.** Version-controlled, survives a clean checkout, travels with the code that ships external assets. | **Source of truth for the gate bundle.** |
| CMO `_default` brand workspace | workspace root → `brand/`, `scripts/verify-brand-backbone.sh` | Authoring mirror. Where the CMO drafts deliverables and keeps the full canon (superseded PDFs, working assets). | Must match the canonical pin. |

Rules so both agents resolve the same path:

1. **"The project root" = the root of whichever checkout you are in.** `scripts/verify-brand-backbone.sh` self-resolves its own root (`dirname/..`), so `bash scripts/verify-brand-backbone.sh` is correct from either root. Do not hard-code an absolute path.
2. **Both copies MUST hash to the pinned SHA** (`a2ffc4f919b0f71a…`). The verifier passing in one checkout does not vouch for the other - run it in the checkout you are shipping from.
3. **On divergence, the Scanner-repo copy wins** and the `_default` mirror is re-synced from it (copy the PDF, `.sha256`, and `scripts/verify-brand-backbone.sh` across; never hand-edit either to "fix" a mismatch).
4. **The PDF lives in `brand/` only** — never at the bare workspace/repo root, never duplicated elsewhere in a checkout.

> Why this section exists: the gate bundle originally lived only in the CMO `_default` workspace, so a clean `are-scanner` checkout was missing it (BEAAA-8464). It is now committed to the Scanner repo (canonical) and this note pins which root is authoritative so the ambiguity cannot recur (BEAAA-8465).

## Currently pinned

| Item | Value |
|---|---|
| Document | `BackBond-Brand-Backbone-v1.6.pdf` |
| Version | v1.6 |
| SHA-256 | `a2ffc4f919b0f71a17b9f4506ac4edf11cd37956ff494f00d7a45f0b166ea090` |
| Pinned at | 2026-07-18 |
| Source issue | [BEAAA-15431](/BEAAA/issues/BEAAA-15431) (metadata correction) / [BEAAA-15377](/BEAAA/issues/BEAAA-15377) / [BEAAA-15379](/BEAAA/issues/BEAAA-15379) (count correction) |
| Pin authority | Founder-attached, board-locked pin move ([BEAAA-15431](/BEAAA/issues/BEAAA-15431)). Corrects the internal DocInfo `/Title` + XMP `dc:title` from v1.4 to v1.6; rendered content byte-identical to the prior v1.6 (27 risk dimensions, `1,180+` probes). |

> **Re-pinned 2026-07-18 ([BEAAA-15431](/BEAAA/issues/BEAAA-15431)).** The pin now points at the Founder-approved metadata-corrected v1.6 build (SHA `a2ffc4f9…`, supersedes `8aa5421c…`). **Only the internal DocInfo `/Title` and XMP `dc:title` were corrected** (v1.4 → v1.6); the visible/rendered content is byte-identical to the prior v1.6 — same ratified 27-dimension content, cover `VERSION 1.6`, `1,180+ probes across 27 risk dimensions`, CHANGELOG intact (verified via empty `pdftotext` diff). The underlying count correction (25 → 27 risk dimensions, [BEAAA-15377](/BEAAA/issues/BEAAA-15377) / [BEAAA-15379](/BEAAA/issues/BEAAA-15379)) is unchanged and carried forward. No palette, type, logo, voice, score-band, warranty, insurance, carrier, or proof-point change. v1.5 remains retained as the historical artifact `BackBond-Brand-Backbone-v1.5.pdf`.

The SHA-pinned filename and hash in `BackBond-Brand-Backbone-v1.6.sha256` are the contract. If the PDF on disk does not hash to that value, the local copy is stale or tampered - re-pull from Paperclip and re-verify before shipping anything external.

## Logo — single source of truth

The Brand Backbone (above) is the law for the logo *treatment*. The **two production logo files** that satisfy it are pinned separately and are themselves canon:

| Item | Value |
|---|---|
| Canon record | [`brand/assets/LOGO-CANON.md`](assets/LOGO-CANON.md) |
| Light file | `brand/assets/BackBond_Logo_light.png` — white / light backgrounds |
| Dark file | `brand/assets/BackBond_Logo_dark.png` — Ink Navy / dark backgrounds |
| Pin | `brand/assets/BackBond_Logo.sha256` (`cd brand/assets && sha256sum -c BackBond_Logo.sha256`) |
| Light SHA-256 | `a1177df0b7db4547f216f948c96c119e729f838d76695427db399e01acc14c20` |
| Dark SHA-256 | `cbe891c751fb9ab20890b24e63f046a5342b888ebbff53d97982312f545b4ba3` |
| Authority | Founder (`local-board`), board-ratified 2026-06-24 via [BEAAA-8361](/BEAAA/issues/BEAAA-8361) |

**Rule:** every document, deck, one-pager, certificate / report card, email signature, web surface, or any other asset that carries the BackBond logo MUST use one of these two files (or a derivative that is pixel-faithful to them). No earlier mark, wordmark, concept lockup, typeset CSS wordmark, or auto-generated variant is acceptable. Re-pinning the logo is **board-locked** — only a new Founder-attached file on an issue assigned to CEO can change it (same protocol as the Backbone PDF below).

`LOGO-CANON.md` is the authoritative spec; this table is the pointer. When the two disagree, `LOGO-CANON.md` governs the logo and the Backbone PDF governs everything else visual.

## Hierarchy of authority

1. **`BackBond-Brand-Backbone-v1.6.pdf`** - the law. Colours, typography, logo, score-card spec, Copper Law, voice spirit, anything visual.
2. **`agents/<cmo>/instructions/AGENTS.md`** — the operating system that *applies* the law. References this file by path + SHA.
3. **`agents/<cmo>/instructions/{audience-playbooks, persona-library, content-sequencing, enemies-and-carrier-rule, voice-and-anti-patterns}.md`** — playbooks subordinate to the Backbone.
4. **`agents/<*>/instructions/house-style.md` and `artifact-metadata-schema.md`** — pre-submission gate that enforces Backbone Pass on every artifact.

When any item below the Backbone disagrees with it, the Backbone wins and the lower file is corrected.

## The Brand Backbone Gate (mandatory pre-submission check)

Every external-bound artifact MUST satisfy all four before shipping:

1. **Pinned-version cite.** The artifact's metadata carries `brand_backbone_version: "v1.6"` and `brand_backbone_sha256: "a2ffc4f9…"`.
2. **Visual compliance.** Colours (Ink Navy `#0B1A38` + Copper `#D58E5C` dark / `#BD6A34` white), type (Archivo / Inter / IBM Plex Mono), Copper Law (copper marks bond/capital/payout only — never decoration, never traffic-light bands), and **logo** — the asset carries one of the two canonical files from `brand/assets/LOGO-CANON.md` (`BackBond_Logo_light.png` / `BackBond_Logo_dark.png`), not a retired mark, concept lockup, or typeset CSS wordmark. The nightly Auditor / brand gate reads `LOGO-CANON.md` for the current logo of record.
3. **Voice + canon compliance.** No anti-patterns from `voice-and-anti-patterns.md`; no banned vocab; "per agent, per run" is verbatim when used; bands are words-first (Preferred·A / Standard·B / Constrained·C / Decline·D).
4. **Local hash matches the pin.** Run `scripts/verify-brand-backbone.sh` - exits 0 if the on-disk PDF matches `BackBond-Brand-Backbone-v1.6.sha256`, non-zero if not.

If any check fails, the artifact does not ship. Escalate to CMO (or Founders for ambiguous calls).

## Change-control protocol

Brand changes are **board-locked**. To pin a new version:

1. Founders (or their delegate) attach the new PDF to a Paperclip issue assigned to CEO.
2. CEO downloads, drops the new file alongside the old in this directory (`BackBond-Brand-Backbone-vX.Y.pdf`), and writes a new `.sha256` pin file.
3. CEO updates `README.md` (this file) — moves the old version row into `CHANGELOG.md`, writes the new row into "Currently pinned", and updates the pinned SHA.
4. CEO updates the SHA reference in every agent's `AGENTS.md` (or in canon `house-style.md`).
5. CEO posts the change-of-pin commit/comment on the originating issue and on the CEO digest the day it lands.
6. The old PDF stays in this directory as a historical artifact (never deleted).

Anyone editing brand-canon files without a Founder-attached PDF and an originating issue: the change is rejected on review.

## What lives here

| File | Purpose |
|---|---|
| `BackBond-Brand-Backbone-v1.6.pdf` | The pinned source of truth |
| `BackBond-Brand-Backbone-v1.6.sha256` | One-line `<sha256>  BackBond-Brand-Backbone-v1.6.pdf` for `sha256sum -c` |
| `BackBond-Brand-Backbone-v1.5.pdf` (+ `.sha256`) | Superseded 2026-07-18 by v1.6; retained as historical artifact (never deleted) |
| `BackBond-Brand-Backbone-v1.5-superseded-587587bd.pdf` | Prior v1.5 build (SHA `587587bd…`, BEAAA-8354), superseded 2026-06-25 by the Founder's corrected v1.5 build (BEAAA-8466); retained as historical artifact (never deleted) |
| `BackBond-Brand-Backbone-v1.4.pdf` (+ `.sha256`) | Superseded 2026-06-25 by v1.5; retained as historical artifact (never deleted) |
| `README.md` | This file — protocol + currently-pinned table |
| `CHANGELOG.md` | History of pinned versions and what changed |
| (future) `BackBond-Brand-Backbone-vX.Y.pdf` | Future pinned versions land alongside |

## What does NOT live here

- Working drafts of brand documents (those live with the author, never canon).
- Logo files live in `brand/assets/` and are governed by `brand/assets/LOGO-CANON.md` (Founder-ratified `BackBond_Logo_{light,dark}.png`, board-locked under BEAAA-8361). Retired concept marks are quarantined in `brand/_retired-concept-logos/`.
- Templated decks or one-pagers (those live in `_default/` flat with the rest of the deliverables).

## Pointer for agents

- CMO `AGENTS.md` line that names the pinned path and SHA is load-bearing — when a new version is pinned, that line is updated *in the same commit* as the file drop.
- Designer, CBDO, CTO (external talks), Legal Coordinator, CSO — when those agents start shipping external assets, the same Backbone Gate is added to their `house-style.md` / pre-submission audit. Tracked via cross-agent rollout child of [BEAAA-6683](/BEAAA/issues/BEAAA-6683).
