---
phase: 08-portfolio-readme-and-documentation
plan: 03
subsystem: docs
tags: [documentation, readme, mermaid, walkthrough, adr]

requires:
  - phase: 08-portfolio-readme-and-documentation
    provides: scripts/check_documentation.py and make docs-check (plan 08-01)
  - phase: 08-portfolio-readme-and-documentation
    provides: docs/operations.md (plan 08-02)
  - phase: 07-synthetic-demo-lecture-and-measured-results
    provides: docs/results/demo-results.md and the demo make targets the README walkthrough names
provides:
  - README.md portfolio page with problem, Mermaid architecture, stage map, design decisions, results, quick start, end-to-end demo and its verification status
  - Evidence table for the cheap demo steps (this file)
affects: [phase-09-public-repository]

actuals:
  tokens: 6100
  tasks: 3
  commits: 3
plan_head_before: 11bdb2a08655becca55a8183eaaca1eaa7358d58
plan_head_after: b089de16327ebe2c7938b9c7d8f308d39aa8fdd7

tech-stack:
  added: []
  patterns:
    - "README commands are make targets and repository scripts, gated by make docs-check"

key-files:
  created: []
  modified:
    - README.md

key-decisions:
  - "The ADR sequence gap is worded as 'records numbered 17 to 19' because the checker reads 'ADRs 0017 to 0019' as references to missing records"
  - "Variable forms in commands are written as 'make demo-run VAR=value' so each line starts with the make target as the acceptance loop expects"
  - "The Mermaid diagram omits the gateway edges of stages 13, 14 and 18 and says so in the following sentence, to keep it readable"

patterns-established:
  - "Results are repeated in the README only as headline figures with denominators, each followed by the limits (not a benchmark, one run, partial DEMO-04)"

requirements-completed: []

coverage:
  - id: D1
    description: "README.md in English replaces the placeholder: problem, Mermaid architecture, stage map with ADR links, twelve design decisions, results with limits, quick start, end-to-end demo, operating boundaries pointer, status and layout"
    requirement: DOC-01
    verification:
      - kind: integration
        ref: "make docs-check; section, figure, Mermaid and wording greps of the plan"
        status: pass
    human_judgment: false
  - id: D2
    description: "Every command, target, variable, script, option, path, link and ADR number of the README exists as written, and the cheap walkthrough steps were executed once with recorded outcomes"
    requirement: DOC-02
    verification:
      - kind: integration
        ref: "make docs-check; walkthrough execution evidence table below"
        status: pass
    human_judgment: false
  - id: D3
    description: "Mermaid diagram renders on the hosting platform"
    requirement: DOC-01
    verification:
      - kind: manual
        ref: "structural check only (header, quoted labels, declared ids, balanced subgraphs); visual rendering unverified offline"
        status: unverified
    human_judgment: true

duration: 25min
completed: 2026-10-16
status: complete
---

# Phase 8 Plan 03: Portfolio README Summary

**`README.md` rewritten in place as the portfolio page: problem, Mermaid architecture, 17-row stage map and 12 design decisions linked to ADRs, DEMO-03 results repeated with denominators and limits, quick start and an end-to-end demo whose commands are all checker-verified and whose cheap steps were executed once on 2026-10-16.**

## Performance

- **Duration:** about 25 min
- **Completed:** 2026-10-16
- **Tasks:** 3
- **Files modified:** 1

## Accomplishments

- Tracer: placeholder removed; quick start (requirements from `.python-version`, the provisioning script, CI), the seven demo steps as make targets, a variables table copied from the Makefile and the Codex variant as documentation only; `make docs-check` passed before expansion.
- Problem, Mermaid flowchart (7 subgraphs, 24 edges, all node ids declared), stage map (stages 2 to 15, stage 18, study guide and site, 38 README lines linking ADRs), twelve design decisions, headline results with the not-a-benchmark statement and the partial DEMO-04 outcome (mock exam 1 of 10), operating boundaries (seven bullets, links `docs/operations.md`), status and limits, repository layout, license.
- "Walkthrough verification" subsection separating what ran on 2026-10-16 from what is cited to Phase 7.

## Task Commits

1. **Task 1: README spine with quick start and end-to-end demo (tracer)** - `d1e10b5` (docs)
2. **Task 2: Problem, architecture, stage map, decisions, results, boundaries, layout** - `6d8d51f` (docs)
3. **Task 3: Walkthrough executed, verification status in README** - `b089de1` (docs)

**Plan metadata:** recorded in the final docs commit.

## Walkthrough execution evidence

Date of execution: 2026-10-16. Outputs were kept outside the repository; only exit codes and counters are recorded. The sign-in file of the demo site was not read, and no executed command passed the egress confirmation variable.

| Step | Command | Exit code | Observed counters or line | Level |
| --- | --- | --- | --- | --- |
| 1 | `make docs-check` | 0 | 2 documents, 21 command lines, 0 findings | executed |
| 2 | `make -n` for demo-video, demo-register, demo-preflight, demo-run, demo-site-access, demo-site, demo-serve, demo-check, demo-harvest, demo-recovery, demo-report | 0 each (11) | recipes name build_demo_lecture, register_local_lecture (two registrations), demo_run, demo_site (prepare-access, generate, check), the site application on the loopback address, demo_results (harvest, report), demo_blocked_recovery | dry run |
| 2 | `make -n demo-run FLASHCARDS_MINIMUM_TOTAL_CARDS=5` | 0 | passes the llama-cpp adapter, lecture 925100 and the deck floor 5 | dry run |
| 2 | `make -n demo-run ADAPTER=codex LECTURE=925101` with the confirmation variable | 0 | passes the codex adapter, lecture 925101 and the confirmation flag; nothing started | dry run |
| 2 | `make -n demo-preflight ADAPTER=codex LECTURE=925101` | 0 | passes the codex adapter, no confirmation flag | dry run |
| 3 | `make demo-preflight` | 0 | `adapter=llama-cpp lecture=925100`, `preflight=ok` | executed |
| 4 | `make demo-preflight ADAPTER=codex LECTURE=925101` (no confirmation) | 2 | `refused: the codex adapter sends lecture content to a remote provider; pass --confirm-remote-egress to confirm`; nothing sent | refused by design |
| 5 | `make demo-report` | 0 | `git status --porcelain docs/results` empty: the regenerated report and JSON equal the committed ones | executed |
| 6 | `make demo-site` | 0 | 3 output lines: the recipe line and one line each for lectures 925100 and 925101 | executed |
| 7 | certificate expiry check (`openssl x509 -checkend 0`) | 0 | certificate not expired, so `make demo-site-access` was not run | executed |
| 7 | `make demo-serve` in the background | n/a | listening on port 8443 after 2 s (bounded poll, limit 30 s) | executed |
| 7 | `make demo-check LECTURE=925100` | 0 | `anonymous_status=401 login_status=303 page_status=200 notice_present=True` | executed |
| 7 | `make demo-check LECTURE=925101` | 0 | `anonymous_status=401 login_status=303 page_status=200 notice_present=True` | executed |
| 7 | server stopped | n/a | `lsof` on port 8443 shows no listener (exit 1) | executed |
| 8 | provisioning script (about 9 GB download) | not run | engine and model state evidenced by step 3 | cited from Phase 7 |
| 8 | `make demo-video` | not run | a rebuild would replace the video the measured runs used; accepted at 243.6 s in Phase 7, covered by `tests/demo/test_demo_build.py` | cited from Phase 7 |
| 8 | `make demo-register` | not run | rewrites registry and queue rows; dry run only | cited from Phase 7 |
| 8 | `make demo-run`, `make demo-harvest`, `make demo-recovery` | not run | multi-stage model runs; evidenced by `docs/results/demo-results.md` and `07-VERIFICATION.md` | cited from Phase 7 |
| 8 | `make demo-site-access` | not run | dry run only, certificate still valid | cited from Phase 7 |
| 8 | Codex form of `make demo-run` | not run | sends lecture content to a remote provider; run once in Phase 7 under the operator's recorded confirmation | cited from Phase 7 |

### Findings

- Environment findings: none. Engines, model file, study guides and the demo certificate were present and valid.
- Drift: none. The regenerated results report equals the committed one, and no tracked file changed during the execution.
- Note: `make demo-site-access` and the report regeneration print absolute paths on the terminal; those lines were not recorded here.
- The Mermaid diagram was checked structurally only (flowchart header, double-quoted labels, 24 edges whose endpoints are all declared ids, 7 subgraphs each closed). Visual rendering is unverified offline and happens on the hosting platform.

## Decisions Made

- See `key-decisions` in the frontmatter.
- The Codex section of the report names only the model (gpt-6-sol); how it was selected on the operator's machine is not described in the README.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Checker finding on the ADR gap sentence**
- **Found during:** Task 2 verification
- **Issue:** "ADRs 0017 to 0019 are absent by design" made the checker report ADR 0017 as a missing record.
- **Fix:** reworded to "the records numbered 17 to 19".
- **Files modified:** README.md
- **Committed in:** 6d8d51f

**2. [Rule 1 - Bug] Two statements stronger than their source**
- **Found during:** Task 2 review against the ADR text
- **Issue:** a causal claim for identical WER figures and a cost claim for resume that the sources do not make.
- **Fix:** reworded to what the report and ADR 0016 state.
- **Files modified:** README.md
- **Committed in:** 6d8d51f

### Other notes

- `documentation-writer` skill: its clarify-then-approve workflow was not run; the plan fixes document type, audience and structure.
- `.planning/REQUIREMENTS.md` was not modified: DOC requirements are marked complete by the orchestrator (hence `requirements-completed` is empty).
- The Code Review and Code Secure phase gates (R0.4) were not run here; they are owned by the orchestrator on the phase-final diff.

**Total deviations:** 2 auto-fixed (Rule 1), 3 notes
**Impact on plan:** none on scope.

## Issues Encountered

None.

## Verification Results

- `make docs-check`: exit 0 (2 documents, 21 command lines, 0 findings)
- `uv run pytest tests/test_check_documentation.py -q`: 55 passed
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`: clean; `uv run pytest -q`: 2971 passed, 1 skipped
- Required sections present; all 13 report figures present in both the report and the README; Mermaid fence starts with `flowchart TB`; `docs/operations.md` and `demo-results.md` linked; 38 lines with `docs/decisions/00`; 24 lines with `-->`; no second person or first-person plural, no emoji, no credential-shaped assignment, no home path; `CONFIRM_REMOTE_EGRESS=yes` only in the Codex variant; `git status --porcelain docs/results` empty; nothing listens on port 8443

## Known Stubs

None.

## Threat Flags

None. No secret, hostname, personal data or machine path appears in the README or this summary (T-08-10); no executed command passed the confirmation variable and the refusal path returned exit code 2 (T-08-11); figures carry denominators and the partial DEMO-04 outcome is stated (T-08-12); the committed report did not change (T-08-13); the server was stopped and port 8443 confirmed free (T-08-14); the verification subsection separates executed and cited steps (T-08-15).

## User Setup Required

None.

## Self-Check: PASSED

- README.md exists; commits d1e10b5, 6d8d51f and b089de1 are ancestors of HEAD.
