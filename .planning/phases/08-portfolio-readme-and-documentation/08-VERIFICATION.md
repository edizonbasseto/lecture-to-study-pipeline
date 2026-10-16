---
phase: 08-portfolio-readme-and-documentation
verified: 2026-10-16T18:00:00Z
status: passed
score: 3/3 must-haves verified
covered_files:
  - ".github/workflows/ci.yml"
  - ".planning/phases/08-portfolio-readme-and-documentation/08-01-PLAN.md"
  - ".planning/phases/08-portfolio-readme-and-documentation/08-01-SUMMARY.md"
  - ".planning/phases/08-portfolio-readme-and-documentation/08-02-PLAN.md"
  - ".planning/phases/08-portfolio-readme-and-documentation/08-02-SUMMARY.md"
  - ".planning/phases/08-portfolio-readme-and-documentation/08-03-PLAN.md"
  - ".planning/phases/08-portfolio-readme-and-documentation/08-03-SUMMARY.md"
  - "Makefile"
  - "README.md"
  - "docs/operations.md"
  - "scripts/check_documentation.py"
  - "tests/test_check_documentation.py"
covered_digest: "v3:sha256:1acce412a3d431f79563c77480daa481c717a29383c25ef35b388d914c462b96"
behavior_unverified: 0
overrides_applied: 0
---

# Phase 8: Portfolio README and documentation Verification Report

**Phase Goal:** A technical reader or recruiter understands the problem, the architecture, the design decisions and the measured results from the README, the demo walkthrough runs as written, and the operating boundaries the ADRs impose are stated.
**Verified:** 2026-10-16T18:00:00Z
**Status:** passed
**Re-verification:** No, initial verification (no previous VERIFICATION.md existed)

All checks below were run by the verifier itself in this session against HEAD 4e2f435 (branch gsd/phase-08-portfolio-readme). The SUMMARY.md claims were not relied on. Per operator instruction there are no human verification items; the single check that cannot run offline (Mermaid visual rendering) is recorded under Limitations and is not a gate.

## Goal Achievement

### Observable Truths (ROADMAP success criteria, DOC-01..DOC-03)

| # | Truth | Status | Evidence |
| --- | --- | --- | --- |
| 1 | DOC-01: README is in English, has no "under active extraction" placeholder, and presents the problem, a Mermaid architecture diagram, the stage map, the design decisions with ADR links, the DEMO-03 results and a quick start | VERIFIED | `README.md` (313 lines). `rg -c "under active extraction"` = 0 in README.md and docs/operations.md. Sections present: The problem, Architecture (mermaid flowchart), Stage map (19-row table, ADR links), Design decisions (11-row table, ADR links), Measured results, Quick start. Diagram structural check run by the verifier: header `flowchart TB`, 20 declared nodes with quoted labels, 24 edges, 0 undeclared ids, 0 isolated nodes, 7 `subgraph` / 7 `end`, no subgraph/node id clash. Results figures re-compared line by line with `docs/results/demo-results.md`: 243.6 s, 620 words, WER 2.3% (14/620) -> 0.5% (3/620) and 3.1% (19/620) -> 0.0% (0/620), exams first-attempt 100.0% (16/16) codex and 87.5% (14/16) llama-cpp, critic rejection 0.0% (0/1) both, recovery 10/10, 6/6 (floor 5), 0/4 (floor 12), 1/10; model name gpt-6-sol matches the report note. The partial DEMO-04 outcome is stated. |
| 2 | DOC-02: the end-to-end demo walkthrough has been executed once and every command in it exists as written | VERIFIED | `make docs-check` run by the verifier: exit 0, "2 document(s), 21 command line(s), 0 finding(s)" (README.md and docs/operations.md). `make -n` of every walkthrough target (`demo-video`, `demo-register`, `demo-preflight`, `demo-run`, `demo-site-access`, `demo-site`, `demo-serve`, `demo-check`, `demo-harvest`, `demo-recovery`, `demo-report`, `hooks`) and of the variable forms shown (`ADAPTER=codex LECTURE=925101`, `CONFIRM_REMOTE_EGRESS=yes`, `FLASHCARDS_MINIMUM_TOTAL_CARDS=5`) expands to the expected script and arguments. Executed for real by the verifier: `make demo-preflight` (`preflight=ok`); `make demo-preflight ADAPTER=codex LECTURE=925101` (refused by design, "pass --confirm-remote-egress", make exit 2, nothing sent); `make demo-report` (exit 0, `git status --short` empty afterwards, so docs/results is byte-identical to the committed report); `make demo-site` (one line per demo lecture); `make demo-serve` in the background followed by `make demo-check` for 925100 and 925101 (both `anonymous_status=401 login_status=303 page_status=200 notice_present=True`), server stopped afterwards (no uvicorn process left). The long steps (provisioning, video build, register, run, harvest, recovery, site-access) are cited to the Phase 7 run in README "Walkthrough verification" and the verifier confirmed their script entry points and options exist (`--help` of `demo_site.py`, `demo_results.py`, `demo_blocked_recovery.py`; `--with-example-llm-model` in the provisioning script). No command passing the egress confirmation was executed by the verifier. |
| 3 | DOC-03: the documentation states the egress rule and unchecked provider terms, the uncertified status and notice, no standing toward certification, blocked-lecture recovery, page regeneration after an inline script or style change, the single-worker site constraint and the FileVault revisit triggers | VERIFIED | `docs/operations.md` (214 lines) has one section per boundary plus a summary table; README "Operating boundaries" links it and summarises all seven. Every blockquote was re-traced by the verifier to the ADR the document cites (19 quotations, all found in the cited ADR: 0045 x7, 0046 x3, 0047 x2, 0024 x4, 0044, 0039, 0010). The notice text matches `src/lecture_study/site/renderer.py:43`. The single-worker claim is backed by ADR 0039 ("in-process, matching the single-replica deployment") and `rg workers infra Dockerfile.site Makefile` returning nothing; the document labels the multi-process consequence as the document's own inference. The FileVault section quotes the three revisit triggers of ADR 0010. |

**Score:** 3/3 truths verified (0 present, behavior-unverified)

### Required Artifacts

| Artifact | Expected | Status | Details |
| --- | --- | --- | --- |
| `README.md` | Portfolio README | VERIFIED | Exists, substantive, wired (links `docs/operations.md`, `docs/results/demo-results.md`, 30+ ADRs; link targets validated by docs-check). |
| `docs/operations.md` | Operating boundaries | VERIFIED | Exists, substantive, linked from README, checked by docs-check. |
| `scripts/check_documentation.py` | Static documentation checker | VERIFIED | 669 lines, real rules (make target/variable, script, module, subcommand, flag, path, link, ADR, quote, home path, unterminated fence). Wired to `Makefile` target `docs-check`, listed in `make lint` (mypy) and CI (`mypy src scripts/check_documentation.py`). |
| `tests/test_check_documentation.py` | Checker tests | VERIFIED | 72 tests pass. |
| `Makefile` | `docs-check` target and `.PHONY` entry | VERIFIED | `docs-check: uv run python scripts/check_documentation.py`. |

### Key Link Verification

| From | To | Via | Status | Details |
| --- | --- | --- | --- | --- |
| README.md | docs/operations.md | markdown link | WIRED | Present in Quick start, End-to-end demo (Codex variant) and Operating boundaries. |
| README.md | docs/results/demo-results.md | markdown link | WIRED | Present in Measured results; figures match the file. |
| Makefile `docs-check` | scripts/check_documentation.py | recipe | WIRED | Target runs and exits 0. |
| docs-check | README.md, docs/operations.md | `default_documents` (README plus `docs/*.md`) | WIRED | Report states 2 documents. |

### Data-Flow Trace (Level 4)

Not applicable: documentation phase, no dynamic rendering. The one derived data set (the results figures) was traced to `docs/results/demo-results.md` above, and `make demo-report` regenerates that report identically.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
| --- | --- | --- | --- |
| Checker passes on the real documents | `make docs-check` | exit 0, 0 findings | PASS |
| Checker detects planted defects (negative test, scratch copy of the tree under the session scratchpad, repository untouched) | `check_documentation.py --root <scratch> README.md` with a bogus make target, bogus variable, missing script, fabricated blockquote, missing path, ADR 9999, home path | exit 1, 8 findings, one per planted defect | PASS |
| Checker test suite | `uv run pytest tests/test_check_documentation.py -q` | 72 passed | PASS |
| Full test suite (run once) | `uv run pytest -q -x` | 2988 passed, 1 skipped | PASS |
| Lint, format, types | `ruff check .`, `ruff format --check .`, `mypy src scripts/check_documentation.py` | clean (270 files formatted, 140 source files, no issues) | PASS |
| Refusal path of the remote adapter | `make demo-preflight ADAPTER=codex LECTURE=925101` | refused, exit 2 | PASS |
| Local preflight | `make demo-preflight` | `preflight=ok` | PASS |
| Served page and notice | `make demo-serve` + `make demo-check` x2 | 401 / 303 / 200 / notice True for both ids | PASS |

### Probe Execution

SKIPPED: no `scripts/*/tests/probe-*.sh` exist and the phase plans declare none.

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
| --- | --- | --- | --- | --- |
| DOC-01 | 08-03 (README) | English README with problem, Mermaid diagram, stage map, decisions with ADR links, DEMO-03 results, quick start | SATISFIED | Truth 1. |
| DOC-02 | 08-01 (checker), 08-03 (walkthrough) | Walkthrough executed once, every command exists as written | SATISFIED | Truth 2. |
| DOC-03 | 08-02 (operations), 08-03 (README pointer) | Operating boundaries stated as the ADRs define them | SATISFIED | Truth 3. |

No orphaned requirements: REQUIREMENTS.md maps DOC-01..03 to Phase 8 and all three are claimed by the plans. (The traceability table in REQUIREMENTS.md still shows "Pending"; updating it is an orchestrator close-out task, not a phase gap.)

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
| --- | --- | --- | --- | --- |
| scripts/check_documentation.py | 95, 585 | the word `PLACEHOLDER` in the constant `PLACEHOLDER_CHARACTERS` | Info | False positive of the marker grep; it names path-placeholder characters. No TBD/FIXME/XXX/TODO/HACK anywhere in README.md, docs/operations.md, the checker or its tests. |

No stubs, no machine-specific paths (docs-check `machine-path` rule clean), no secrets or throwaway credentials shown in the documents.

### Review and security gates (R0.4)

`08-REVIEW.md` (re-review of the corrected diff): 0 critical, 1 warning, 6 info, no blocker. `08-SECURITY.md`: verdict SECURED, `threats_open: 0`, 16 of 16 closed. Both were produced against the corrected phase-final diff; HEAD only adds those two reports on top.

### Advisory (non-blocking, from the reviewer and re-observed by the verifier)

| # | Finding | Category | Why advisory |
| --- | --- | --- | --- |
| 1 | WR-01: the checker reads a value placed before the subcommand (`--whisper-model-path model.bin run-all`) as the subcommand, and tests option presence by raw substring. Tooling imprecision, partly disclosed ("textual presence"); no current document is affected (docs-check is clean). | other | Does not affect any must-have truth. |
| 2 | IN-04: the Codex variant block does not tell the reader to harvest with `ADAPTER=codex LECTURE=925101`; the Codex figures are described as coming from the Phase 7 run. | other | Walkthrough commands all exist; the gap is an omitted optional step. |
| 3 | IN-05: the diagram omits stages 11 and 12 (present in the stage map) and draws the stage 18 cards and the study guide as siblings although ADR 0045 section 5 says the deck feeds the guide. | other | The README text and stage map are accurate; the diagram is a simplification. |
| 4 | IN-01..03, IN-06: anchors, running-text `make` commands, root-level files, quote attribution and URL false positives of the home-path rule are not checked. | other | All disclosed accurately in README.md and docs/operations.md. |

### Human Verification Required

None, by operator instruction. Every check that can be performed was performed by the verifier.

### Limitations

- Mermaid visual rendering cannot be checked offline. The diagram passed the structural check above (header, quoted labels, declared ids, balanced subgraphs, no undeclared or isolated nodes) and the README itself says that rendering on the hosting platform was not verified offline. This is recorded as a limitation, not a gate.
- Long walkthrough steps (provisioning, `demo-video`, `demo-register`, `demo-run`, `demo-harvest`, `demo-recovery`, `demo-site-access`) were not re-executed by the verifier (multi-hour and model-dependent); they rest on the Phase 7 verification and the committed results report, which is how the plan and the README scope the DOC-02 claim.

### Gaps Summary

No gaps. The phase goal is achieved: the README presents the problem, architecture, stage map, decisions and measured results with figures that match the committed report; `docs/operations.md` states all seven operating boundaries with quotations verified against the ADRs; the walkthrough commands exist as written (checker plus the verifier's own `make -n` and live executions); and the checker detects planted defects.

---

_Verified: 2026-10-16T18:00:00Z_
_Verifier: Claude (gsd-verifier)_
