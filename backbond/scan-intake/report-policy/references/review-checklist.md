# Review checklist

Run this against the rendered PDF, not the HTML. Answer every line yes or fix it. A report with a "no" is not delivered.

## Reader test
- [ ] A CEO reading only the headline and the right-hand panel knows what the agent did wrong, in plain words, within sixty seconds.
- [ ] Headline names the checks that passed and the demonstrated business error. No blanket security pass or assertion that another review could never find it. Failed security families remain visible by reviewed counts. A wrong decision under complete supplied policy facts is eligible even from normal-request or utility tests. No sentence claims every cited reply passed every guardrail when one did not.
- [ ] A CISO reading the findings pages can find, for every quoted reply, the case id in the run record and the counter-evidence line.
- [ ] Nothing on page 1 requires the reader to know what an oracle, a family, a canary, a marker or an adjudication is.

## Config
- [ ] Profile chosen matches the payload (100 Scan / full / rescan / connected) and the page count matches `profiles.yaml` for the number of surviving findings.
- [ ] Every family on page 1 is in `family-map.yaml`; every finding has a pattern from `pattern-catalogue.yaml`. Unmapped families and new behaviours are in the delivery note as proposed entries, not on the page.
- [ ] Config versions are stated in the delivery note.

## Evidence
- [ ] `template/verify_quotes.py` run against the payload: zero FAILs attributed to the agent; our-own-prose FAILs listed and confirmed not attributed.
- [ ] Every trim inside a quote is marked with an ellipsis; no words changed, no sentences joined silently.
- [ ] Every finding comes from a family the notes do not mark as artefact, by construction, could-not-run or label error; an infra-family case is present only under the assertion rule (pattern flagged `assertion_pattern: true`, quoted reply asserts a check or action).
- [ ] No JSON-shaped reply is quoted from a run with the brace defect.
- [ ] No finding depends on a family with an open defect that a re-run could change.
- [ ] Every "N of M" matches the family counts table.
- [ ] Headline totals (executions, scenarios/families, exchanges) recompute from the payload and match the run record.
- [ ] Each finding carries one counter-evidence line.
- [ ] Owner-dependent observations are in a "to confirm" panel, not in the findings.
- [ ] Original labels and reviewed grades remain separately identifiable. Corrections bind to the exact prompt/reply and source hash. A historical rejection of the same case ID on a different agent is not reused as a blanket exclusion. Requested labelled fiction and descriptions of injected instructions are not treated as observed compliance or factual fabrication.

## Positioning
- [ ] The passed column lists only security-review families that actually passed, with real counts.
- [ ] The headline follows the formula: passed X; also did Y. X is the named checks that passed, never a blanket claim; Y is a behaviour, not a category.
- [ ] Finding titles are in the user's words. None contains values / status / integrity / stability / handling / flagging.
- [ ] No "pilot", "coverage", "bond", "insurance", "rating-and-insurance layer", "QA", "trust", "security platform", "responsible AI".
- [ ] No CTA box, founders bar, proposal section or pricing. The only forward reference is monitoring and a rescan in the customer's environment.
- [ ] Score line present in the exact form "BackBond Score: not issued." followed by a reason with no insurance word (not "coverage").
- [ ] Scan Focus present once, verbatim, in the "This document" cell: "BackBond finds what an agent does when nobody is attacking it, and shows the business what each one exposes it to."

## Brand (per backbond-brand and backbond-sales-marketing, v1.0.5.1 or later)
- [ ] Both brand skills were loaded; the delivery note says so. If not, the report is not brand-final.
- [ ] Every material claim on the page is verified or qualified (dated, scoped) or labelled illustrative. No unverified claim externally. No stale claim (1,180+ probes, old Focus, IFA/BEA branding, old slogan).
- [ ] Naming: "BackBond Score" in full; bond/bonded only as evocative language, never a surety claim; "Agent Exposure Scan" and "100 Scan" as the product names; never QA, pilot, Slim Scanner.
- [ ] Carriers, if mentioned at all, as partners; no claim of replacing carrier capacity. Normally not mentioned in a scan report.
- [ ] Logo at or above the minimum size (80 px digital / 25 mm print), packaged file, not recolored or reconstructed.
- [ ] Voice: risk-fluent operator; no undefined "trust", no responsible-AI vocabulary, no filler (empowering, unlocking, seamless, game-changing, in today's landscape).

## Language
- [ ] No em dashes. Spaced hyphens or commas.
- [ ] No "This isn't X, it's Y." No rule-of-three padding. No critical / significant / robust / seamless / leverage / empower / potential (as hedge).
- [ ] Hedging vocabulary (adjudicable, inconclusive, not pinned, counter-evidence) appears only on the last page.
- [ ] Synthetic values, if shown, are labelled synthetic once.

## Layout
- [ ] Packaged logo file on every page, not typeset. Clear white space between the header rule and the first body line, every page.
- [ ] Fonts: Archivo display, Inter body, IBM Plex Mono Medium labels. No substitutions in the rendered PDF (check a glyph).
- [ ] Copper only on the second headline line, the failed-panel label, the finding numerals and the Exposure labels.
- [ ] Every page's content-to-footer gap is between 20 and 250 px; no page spills; no page is half empty on a findings page.
- [ ] Page count follows the sizing table in `report-structure.md`; findings index used at 6+; delta strip present on a rescan.
- [ ] Header meta and footer read "Findings Report", target, run window, issue date, "Page n of N".

## Delivery note
- [ ] Lists the findings chosen and the candidates rejected, with the reason for each rejection.
- [ ] Names anything not confirmed (dates, model binding, counts) and anything that needs the customer's confirmation.
- [ ] Names what must not be sent alongside (working packs, evidence notes, superseded reports) and why.
- Customer-copy check: Record block has no raw label counts; headline first line is one breath; no finding line opens with a fragment; next step ends with the product hook. (19 Oct 2026)
- Headline words: first line uses the plain-words translations in writing-patterns.md; no scanner family names, no hyphenated jargon, no 'questionnaire'. (20 Oct 2026)
- Shipped files: the shipped HTML is the inline_assets.py output (logo and fonts embedded; no assets/ references); the shipped PDF and HTML are attached by the Writer to the PARENT issue, not only the child. (20 Oct 2026)
- Type scale: body 10pt, findings 10pt (template v2.4). A report at the old 9pt/7.5pt scale is a FIX. (20 Oct 2026)
