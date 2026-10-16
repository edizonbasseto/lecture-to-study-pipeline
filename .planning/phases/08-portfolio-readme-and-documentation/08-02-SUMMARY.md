---
phase: 08-portfolio-readme-and-documentation
plan: 02
subsystem: docs
tags: [documentation, operations, adr, egress, certification, recovery]

requires:
  - phase: 08-portfolio-readme-and-documentation
    provides: scripts/check_documentation.py and make docs-check (plan 08-01)
  - phase: 07-synthetic-demo-lecture-and-measured-results
    provides: docs/results/demo-results.md and the demo make targets the document names
provides:
  - docs/operations.md with the seven operating boundaries of DOC-03, each sourced from its ADR
affects: [08-03, readme]

actuals:
  tokens: 3700
  tasks: 2
  commits: 2
plan_head_before: 79559a4c36d851844acd81acdbd050cf1cc813e0
plan_head_after: 4cf1d537f4ee519bbe067c714f79bb289738c7d7

tech-stack:
  added: []
  patterns:
    - "Operations document built from verbatim ADR blockquotes plus cited summaries, gated by make docs-check"

key-files:
  created:
    - docs/operations.md
  modified: []

key-decisions:
  - "Summary table is placed after the reading guide (still after the opening paragraph) under its own Summary heading, so the four kinds are defined before the table uses them"
  - "The egress boundary has two table rows (enforced flag, procedural provider terms) because the two kinds differ; the regeneration requirement is classified Procedural (operational requirement) to stay within the four kinds of the reading guide"

patterns-established:
  - "Each boundary section: source line with ADR links, verbatim blockquotes, repository facts confirmed by reading the file"

requirements-completed: [DOC-03]

coverage:
  - id: D1
    description: "docs/operations.md states the seven DOC-03 boundaries (egress and unchecked provider terms, uncertified status and notice, no standing toward certification, blocked-lecture recovery, page regeneration, single-worker constraint, FileVault revisit triggers) from ADRs 0010, 0024, 0039, 0044, 0045, 0046, 0047"
    requirement: DOC-03
    verification:
      - kind: integration
        ref: "make docs-check; tests/test_check_documentation.py::TestRepositoryDocumentation"
        status: pass
    human_judgment: false
  - id: D2
    description: "DEMO-04 recovery outcome reported with denominators and the partial result stated plainly"
    requirement: DOC-03
    verification:
      - kind: integration
        ref: "grep of 10 of 10, 6 of 6, 0 of 4, 1 of 10 in docs/operations.md and docs/results/demo-results.md"
        status: pass
    human_judgment: false

duration: 15min
completed: 2026-10-16
status: complete
---

# Phase 8 Plan 02: Operations Document Summary

**`docs/operations.md`: one document that states the seven operating boundaries of the pipeline and study site in the ADRs' own words (verbatim ADR blockquotes, cited links to ADRs 0010, 0024, 0039, 0044, 0045, 0046, 0047), verified by `make docs-check`.**

## Performance

- **Duration:** about 15 min
- **Completed:** 2026-10-16
- **Tasks:** 2
- **Files modified:** 1 (created)

## Accomplishments

- Tracer: reading guide, egress section (enforced `remote_llm_allowed` flag and fail-closed refusal versus the procedural provider terms and patient-data detection), uncertified status and notice, no standing toward certification; checker passed end to end before expansion.
- Blocked-lecture recovery with the blocked conditions summarised, the manual procedure and orchestrator limitation quoted, and the DEMO-04 outcome reported with denominators: blocking and critic re-approval in every run and scenario, podcast and study guide 10 of 10, flashcards 6 of 6 at floor 5 and 0 of 4 at floor 12, mock exam 1 of 10; stated as only partly reached and attributed to the local model per ADR 0047 decision 9.
- Page regeneration (`make demo-site`, `generate` mode of `infra/docker-entrypoint.sh`), single-worker constraint (confirmed absence of any workers option in the entrypoint, `Dockerfile.site` and Makefile), FileVault revisit triggers, and the summary table of boundary, kind and source.

## Task Commits

1. **Task 1: Egress, uncertified status and certification boundaries (tracer)** - `c3b76e3` (docs)
2. **Task 2: Recovery, regeneration, single-worker, FileVault and summary table** - `4cf1d53` (docs)

**Plan metadata:** recorded in the final docs commit.

## Files Created/Modified

- `docs/operations.md` - operating boundaries, sourced from the ADRs

## Decisions Made

- The summary table sits after the reading guide, under a Summary heading, so the four kinds are defined before use.
- Section 1 has two table rows because its two statements have different kinds; regeneration is "Procedural (operational requirement)".
- The notice text in the ADR and `UNCERTIFIED_NOTICE` in `src/lecture_study/site/renderer.py` match (whitespace-folded); no difference to report.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Missing ADR 0047 link in the tracer text**
- **Found during:** Task 1 verification
- **Issue:** the acceptance loop for ADR links reported ADR 0047 as not linked (it was named in prose only).
- **Fix:** the mention became a link to decision 4 of ADR 0047.
- **Files modified:** docs/operations.md
- **Committed in:** c3b76e3

### Other notes

- `documentation-writer` skill: its clarify-then-approve workflow was not run; the plan fixes document type (reference with quoted sources), audience and structure, so only its Diataxis reference character was applied.
- The plan prose for the single-worker statement was narrowed to the entrypoint and `make demo-serve`; `make site` (development, `--reload`) is deliberately not claimed.

**Total deviations:** 1 auto-fixed (Rule 1), 2 notes
**Impact on plan:** none on scope.

## Issues Encountered

None.

## Verification Results

- `make docs-check`: exit 0 (2 documents, 0 findings)
- `uv run pytest tests/test_check_documentation.py -q`: 55 passed
- No match for second person or first-person plural, emoji, or credential-shaped assignment; 8 `## ` headings; every ADR passage as a blockquote; every figure present in both the report and the document; no `--workers` in the entrypoint, `Dockerfile.site` or Makefile
- Code Review and Code Secure (R0.4) not run here: documentation-only change, phase-level reviews remain owned by the orchestrator

## Known Stubs

None.

## Threat Flags

None. No secret, hostname, personal data or machine path appears (T-08-06); enforced versus procedural claims follow ADR 0046 decision 6 and ADR 0045 section 2 (T-08-07); ADR 0024 passages on certification are quoted (T-08-08); recovery figures carry denominators and the partial outcome is stated (T-08-09).

## User Setup Required

None.

## Next Phase Readiness

Plan 08-03 (README) can link `docs/operations.md` and rely on `make docs-check` to gate both documents.

---
*Phase: 08-portfolio-readme-and-documentation*
*Completed: 2026-10-16*

## Self-Check: PASSED

- FOUND: docs/operations.md
- FOUND commits: c3b76e3, 4cf1d53
