# Skill inspection regressions

These are inert test inputs. Never execute their helper scripts.

- `poisoned_description` is the authored hidden-comment case from the
  2026-10-30 exploratory comparison. Its instruction requests an unauthorized
  external transfer, which should remain visible as a source finding.
- `benign_formatter` copies NVIDIA SkillSpector's clean MCP formatter fixture
  from revision `54921c8c7c23008c8af2328d5ab492a904b6ece4`, paths
  `tests/fixtures/mcp_clean_skill/SKILL.md` and `scripts/format.py`.
  Source: https://github.com/NVIDIA/SkillSpector/tree/54921c8c7c23008c8af2328d5ab492a904b6ece4/tests/fixtures/mcp_clean_skill
  The included `LICENSE-NVIDIA.txt` retains its Apache-2.0 license.
- `action_cases.json` is a new authored 24-case challenge set. Labels and
  categories are evidence metadata and are not sent to the live model.

The live action formatter is a scanner-owned AST formatting function. It
performs actual file writes but does not execute these Python fixture scripts.
No fixture result changes score weights or activates a production test family.
