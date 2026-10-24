---
name: backbond-findings-report
description: Turn a BackBond scan evidence payload (cases, exchanges, labels, counts, notes) into a customer-facing Findings Report that lands with a business reader and survives a security reader. Use for every 100 Scan and every full Agent Exposure Scan report. Produces a 2-page (100 Scan) or 3-to-4-page (full scan) PDF in the BackBond report system, plus the HTML source. Do not use for internal evidence notes, working packs or appendices; those stay in their existing formats.
---

# BackBond Findings Report

The report's job is to make a CEO or COO feel the exposure in sixty seconds and give their security lead nothing to pick apart. The evidence payload is written by testers for testers. Your job is translation, selection and proof, in that order. Nothing is invented; everything quoted is verbatim; every finding carries the exchange that produced it.

Read `references/report-structure.md` for the page-by-page spec, `references/finding-selection.md` for what counts as a finding, `references/writing-patterns.md` for how to phrase it, and `references/review-checklist.md` before you deliver. The gold examples in `examples/` are the standard; the "before" files show what you are not allowed to produce.

The method is fixed. What changes with the scanner lives in `config/` and is read on every run: `family-map.yaml` (which families belong in which column and which pattern they default to), `pattern-catalogue.yaml` (the business patterns and how each is phrased), `profiles.yaml` (100 Scan, full scan, rescan, connected scan, and how each is sized). A family not in the map, or a behaviour not in the catalogue, is reported in the delivery note as a proposed entry and kept off page 1 until the org adds it. See `references/extending.md`.

## Dependency: the BackBond brand skills

This skill carries only the slice of the brand that bites on a findings report (fonts, palette, logo rule, banned words, no insurance language, no score value or band; the status line "BackBond Score: not issued." is required). The rest of Brand Backbone v1.7 lives in two skills that must be installed alongside and loaded before writing: `backbond-brand` (identity, voice, naming such as "BackBond Score" never "Bond Score", logo sizing, carrier treatment, the Focus statement) and `backbond-sales-marketing` (claims discipline: every material claim classified verified / qualified / illustrative / unverified / prohibited; counts and dates revalidated before external use; stale claims such as "1,180+ probes across 27 risk dimensions" never revived; the DOCX/PDF verifier). They ship as BackBond Skills v1.0.5.1 or later with the Brand Backbone v1.7 update applied; the canonical v1.7 PDF hash is the `canonical_sha256` in `backbond-brand/assets/brand-backbone.json`, not pinned here. If either is missing from the environment, say so in the delivery note and do not call the report brand-final. Where this skill and the brand skills differ, the brand skills win on identity and claims; this skill wins on report structure and finding selection.

## The one idea every report is built on

Show the demonstrated business error alongside security results from the same run. Name the checks that actually passed and retain failures. Never imply an overall security approval or claim another review could not detect the business error.

## Inputs you will receive

A payload in the shape of the Vapi or Kaltura packs: an evidence JSON (cases with `case_id`, `family`, `original_outcome`/`recorded_verdict`, `exchanges[]` of prompt/reply), a family-level counts table, run metadata (target, endpoint, dates, hashes), and usually a notes file with reading notes and known defects. Fields vary by run. If any of the following is missing, stop and request it by name before drafting: full prompt and reply for every case you intend to quote; the family counts that reconcile to the headline totals; the target description (what the agent is for, who uses it); the run date and hashes; the known-defect list.

## Method, in order

0. **Pick the profile and load the config.** Decide from the payload or the request which `profiles.yaml` profile applies. Load `family-map.yaml` and `pattern-catalogue.yaml`; note their versions for the delivery note.
1. **Read the notes file first, then the counts, then the cases.** The notes tell you which labels are artefacts. The counts tell you where the agent held and where it failed. Only then read exchanges.
2. **Build the "passed" column from families mapped `security` that passed.** Use the real counts (20/20, 9/10) and the `label` from the map. If a security family failed, it still goes in the passed column only if it passed; a failed security family is a finding, but not the lead.
3. **Triage every reviewed fail by reading the exchange.** Include ordinary-request and utility cases when a supplied business rule and facts prove a wrong decision (`policy_application_error`); family names are not exclusion gates. Apply `finding-selection.md`, preserve original grades, and use separately bound grading corrections when supplied. Discard harness artefacts, capability-only failures, capture bugs and label errors. Never reuse an earlier run's rejection by case ID alone. Assign each survivor an approved pattern and qualify business consequences to the evidence.
4. **Pick the findings the profile allows and rank by business consequence, not count.** `lead_weight` in the catalogue breaks ties: weight-1 patterns (differential treatment, safety miss, secret echoed) lead when present; put the one a CISO checks second. A finding with a verbatim exchange beats a finding with a bigger number. Size the report by the number that survive, per `profiles.yaml`.
5. **Write each finding as what the user experienced.** Title in the user's words. Scenario in plain words. Reply verbatim with the damaging phrase in bold. Then Finding (what happened), Exposure (what it costs the business), and one grey "what also happened" line for the counter-evidence. Fix goes in the exposure's last sentence or the next-step block, never as the headline.
6. **Write the headline last.** Formula: *[The agent] passed [the security thing]. It also [the single most damaging thing it did, in plain words].* No adjectives, no "weaknesses", no "potential".
7. **Render, verify, look at every page, fix, then hand to QC.** `template/render.py --png` for layout and fonts; `template/verify_quotes.py` against the payload for every quote. A page that spills or a quote that does not match is not finished. The QC agent reviews before delivery; apply its fixes exactly.

## Rules that are not negotiable

- **Verbatim or nothing.** Every quoted reply is copied from the exchange. Trim with ellipses; never paraphrase inside quotation marks. Bold the damaging phrase.
- **Describe the scenario, not the harness.** Say "a signed-in customer asked the agent to clear a hold" not "synthetic context: authentication_state=authenticated". The appendix has the raw prompt; the report has the human version.
- **Counts reconcile.** The totals on page 1 must add up to the family table in the payload. If two sources disagree, use the one that recomputes from the transcript and say nothing about the other.
- **No score.** "BackBond Score: not issued" with the reason, until the org says otherwise in writing.
- **No insurance language.** No pilot, no coverage, no bond, no "rating-and-insurance layer" in scan reports. The only forward reference is "monitoring as the agent changes."
- **Nothing the re-run can contradict.** If a finding depends on a family with a known defect, it does not go in. If the notes say "by construction", it does not go in.
- **Not a brochure.** No CTA box, no founders bar, no proposal section, no "what this means for you" paragraph. The report is the findings and the record; the email carries the pitch.
- **Synthetic values are shown, labelled.** If the payload says a key, email or name was synthetic, you may quote it and say so once ("synthetic key, supplied by the test").
- **Hedges live on the last page.** "Adjudicable", "inconclusive", "not pinned", "counter-evidence" belong under "What this report does not establish" and in the run record, not on page 1.
- **Brand system, exactly.** Fonts Archivo (display), Inter (body), IBM Plex Mono Medium (labels and data). Palette Ink Navy #0B1A38, Copper #BD6A34 (only for the second headline line and finding numerals), Slate #535E77, Warm Paper #FCFBF8. Packaged logo file, never typeset. White space between the header rule and the first body line on every page. Spaced hyphen or comma, never an em dash.

## Sizes

Sizing follows the current `config/profiles.yaml`. A 100 Scan may contain zero to three confirmed findings: one demonstrated error is sufficient and must not be padded or suppressed to fill a template. Use two pages for the one-finding report, with the exchange and qualifications on page 2. Config overrides older sizing prose in references. A rescan may legitimately have zero new findings.

## Delivery

PDF named `BackBond-<Customer>-<Agent>-Findings-Report-<YYYY-MM-DD>.pdf`, HTML source alongside, both to the `BD work` folder. Author metadata is Eric Gewirtzman. A short delivery note stating: the profile and config versions used; the findings chosen and the ones rejected with the reason; any family not in the map or behaviour not in the catalogue, with a proposed entry; any count or date you could not confirm; any finding that needs the customer's confirmation before it can be called a finding.
