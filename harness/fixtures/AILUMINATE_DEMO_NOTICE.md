# AILuminate v1.0 DEMO — attribution + content warning

`ailuminate_demo_en_us_v1.0.csv` in this directory is the
`airr_official_1.0_demo_en_us_prompt_set_release.csv` file from MLCommons'
public AILuminate v1.0 DEMO release, distributed unchanged.

- Source: <https://github.com/mlcommons/ailuminate>
- License: CC-BY-4.0
- Released by: MLCommons AI Risk & Reliability working group
- Contents: 1,290 English DEMO prompts (10% sample of the AILuminate v1.0
  Public Practice dataset, 12 hazard categories, adversarial + non-adversarial)

## Content warning

These prompts were authored to elicit hazardous responses across CSAM,
violent / non-violent crime, weapons, hate, self-harm, privacy violation,
defamation, IP infringement, and other safety-critical categories. They
contain offensive and disturbing material by design.

**Do not echo AILuminate prompts unredacted into ARE customer reports,
external comms, or vendor demos.** When AILuminate-derived measurements
ship to customers (BEAAA-201 follow-up + UW co-sign on the hazard → ARE
mapping), the report pipeline must redact prompt bodies to hazard +
persona + prompt id only, matching the redaction policy for our other
adversarial corpora.

## Mapping into ARE

Phase-1 (BEAAA-218): loader only, no rubric/scoring wired. AILuminate
measurements stay OFF the ARE rubric until UW co-signs a hazard →
pillar/dim mapping and the harness `applicability_policy.yaml` gets
updated. BEAAA-21 byte-stability is preserved by keeping this corpus
out of the scoring path.
