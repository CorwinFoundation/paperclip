# OSS clean-export pipeline

Publish-**prerequisite** tooling. Produces the public BackBond CI/CD-plugin
repository as a **single squashed commit** under a **neutral author identity**,
built from a **ref-scrubbed** copy of the internal shipping set, and verified by
an independent **grep gate** (0 internal references) before the commit is built.

**This pipeline does not publish.** It emits a self-contained, verified bundle
that the separate, gated publish step consumes.

## Why this exists

The plugin cannot be published straight from the internal working tree:

1. **File bodies** carry internal issue-tracker ids (`BEAAA-*`), internal
   design-doc ids (`ADR-*`), and legacy `ARE Scanner` product naming in
   comments and docstrings across every shipping file.
2. **Working history** (hundreds of commits) leaks internal e-mail domains
   (`beai.ing`, `are-scanner.internal`, `corwinfoundation.local`,
   `ifa.invalid`), an internal agent id, and a `BEAAA-*` id in every commit
   subject.

The pipeline neutralizes (1) with a deterministic ref-scrub and (2) by
discarding all history into a single squashed commit with a neutral author.

## The shipping set

The Dockerfile `COPY`s the three `api` modules, so a bare `ci_plugin/` export
will not build. The real shipping set is:

```
ci_plugin/**              (Dockerfile, README.md, __init__.py, action.yml,
                           client.py, gate.py, runner.py, templates/.gitlab-ci.yml)
api/__init__.py
api/jcs.py
api/ci_scan_ext.py
```

## Usage

```bash
tools/oss-export/clean_export.sh [<source-ref>] [<out-dir>]
#   <source-ref>  git ref/sha to export from   (default: origin/master)
#   <out-dir>     output directory for bundle   (default: dist/oss-export)
```

Output under `<out-dir>`:

| Artifact             | Contents                                                        |
| -------------------- | --------------------------------------------------------------- |
| `repo/`              | The public repo: scrubbed shipping set, **1** squashed commit.  |
| `oss-plugin.bundle`  | `git bundle` of `repo/` for handoff to the publish step.        |
| `MANIFEST.txt`       | Provenance: source sha, public commit sha, author, gate result. |
| `scrub.log`          | Rewrite rules applied.                                          |
| `gate.log`           | Independent gate second pass.                                   |

## What the scrub does (and does NOT touch)

`scrub.py` rewrites only **comments and docstrings**. It never touches a
runtime identifier, function name, constant value, or env-var name, and it
preserves the grandfathered public interface names:

* `are-scanner-api-v1` — the v1 contract name (glossary: grandfathered for v1).
* `backbond/are-scanner-action@v1` — the published GitHub Action ref.
* `ARE_SCAN_TIER` / `ARE_SCAN_WEBHOOK` — runtime env-var names.
* `are_total`, `policy_band_floor`, etc. — response fields.

The gate is UPPERCASE-word-only for `ARE`, so those lowercase / `ARE_`-prefixed
identifiers are intentionally left intact while legacy `ARE Scanner` naming is
flagged.

## The gate (authoritative)

`scrub.py gate <dir>` fails non-zero on any of:

* `beaaa[-_ ]?\d+`  (case-insensitive — also catches path spellings)
* `adr[-_ ]?\d+`    (case-insensitive)
* `beai.ing`, `are-scanner.internal`, `corwinfoundation.local`, `ifa.invalid`
* `\bARE\b`         (uppercase word — legacy product name)

`clean_export.sh` runs the gate twice: once inside `scrub` and once as an
independent pass, and refuses to build the commit if either fails.

## Reproducibility

Same `<source-ref>` + same tool version ⇒ byte-identical public tree
(verified by comparing `HEAD^{tree}` across runs). The commit sha is stable
because the neutral author/committer identity is fixed and the commit carries
no timestamp-derived content beyond the standard git author/commit dates.

## Rules are drift-guarded

Every configured rewrite rule must match at least once; if a source change
silences a rule, the scrub fails loudly (`rule matched nothing`) rather than
letting an internal reference slip through. When the shipping set changes,
update the rules in `scrub.py` and re-run.
