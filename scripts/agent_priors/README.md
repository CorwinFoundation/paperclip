# Directional priors for real AI agents

**These are priors, not measurements.** Each file estimates 0–4 raw scores
per BMI dim from public information (model cards, vendor trust portals,
published benchmarks, public red-teaming reports). They are intended for
prioritization — *which agents would we want to scan first as pilot
targets?* — not for binding, pricing, or any contract decision.

Real scanner output requires the hosted-eval-only intake guard (BEAAA-22
§3): a snapshot hash, a forensic evidence bundle, and per-dim runs from
the BEAAI-hosted eval harness. None of that exists for any of these
priors.

Each file structure:

```json
{
  "id": "...",
  "display_name": "...",
  "rationale": "Plain-English summary of where the prior comes from",
  "evidence_links": ["https://..."],
  "raw_scores": {"P1.1": 3.0, ...}
}
```
