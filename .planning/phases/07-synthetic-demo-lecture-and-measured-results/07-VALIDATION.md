---
phase: "7"
slug: "synthetic-demo-lecture-and-measured-results"
# status lifecycle: draft (seeded by plan-phase) → validated (set by validate-phase §6)
# audit-milestone §5.5 distinguishes NOT-VALIDATED (draft) from PARTIAL (validated + nyquist_compliant: false) (#2117)
status: draft
nyquist_compliant: false
wave_0_complete: false
created: "2026-10-15"
---

# Phase 7 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest |
| **Config file** | pyproject.toml |
| **Quick run command** | `uv run pytest -q tests/demo` |
| **Full suite command** | `uv run pytest` |
| **Estimated runtime** | ~7 seconds for `tests/demo` without the opt-in dry run (the dry run and the live runs are measured at execution) |

---

## Sampling Rate

- **After every task commit:** Run `{quick run command}`
- **After every plan wave:** Run `{full suite command}`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 7 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 7-01-01 | 01 | 1 | DEMO-01 | T-07-01 | Narration reaches Kokoro as one argument of an argument list, sanitized environment, bounded process | unit | `uv run pytest tests/test_tts.py tests/demo/test_demo_script.py -q` | ❌ created in task | ⬜ pending |
| 7-01-02 | 01 | 1 | DEMO-01 | T-07-02, T-07-03 | Authored content only; lint forbids digits and dosage words; strict YAML loader | unit | `uv run pytest tests/demo/test_demo_script.py -q` | ❌ created in task | ⬜ pending |
| 7-02-01 | 02 | 2 | DEMO-01 | T-07-04, T-07-05 | Pure argument-list builders; video and images gitignored | unit + build | `uv run pytest tests/demo/test_demo_build.py -q -rs`; `make demo-video` then `git check-ignore -q workspace/demo/aula-demo.mp4` | ❌ created in task | ⬜ pending |
| 7-02-02 | 02 | 2 | DEMO-01 | T-07-06, T-07-07 | Finite timeouts; build directory reset confined to workspace/demo | unit + build | `make demo-video` then `jq -e` on `workspace/demo/build/build-summary.json` | ❌ created in task | ⬜ pending |
| 7-03-01 | 03 | 2 | DEMO-03 | T-07-08 | Corrections parsed strictly; unmatched correction fails the measurement | unit | `uv run pytest tests/demo/test_demo_wer.py tests/demo/test_demo_asr_measure.py -q` | ❌ created in task | ⬜ pending |
| 7-03-02 | 03 | 2 | DEMO-03 | T-07-08 | Run records parsed with the strict extra-forbid model | unit | `uv run pytest tests/demo/test_demo_run_metrics.py tests/demo/test_demo_phase_timing.py -q` | ❌ created in task | ⬜ pending |
| 7-03-03 | 03 | 2 | DEMO-03 | T-07-09, T-07-10 | N beside every rate; failed records never shown as rates; no absolute path | unit | `uv run pytest tests/demo/test_demo_report.py -q` | ❌ created in task | ⬜ pending |
| 7-04-01 | 04 | 2 | DEMO-02 | T-07-12, T-07-14 | Real CSRF sign-in; generate_site only on tmp roots | integration (opt-in, macOS tools) | `LECTURE_STUDY_DEMO_TOOLS=1 uv run pytest tests/demo/test_demo_dry_run.py -q -rs` | ❌ created in task | ⬜ pending |
| 7-04-02 | 04 | 2 | DEMO-02 | T-07-11, T-07-13, T-07-15 | Secrets generated at run time, 0o600, gitignored; loopback-only certificate | unit | `uv run pytest tests/demo/test_demo_site_access.py -q -rs` | ❌ created in task | ⬜ pending |
| 7-05-01 | 05 | 2 | DEMO-04 | T-07-16, T-07-17 | Gate untouched; inductions with nanosecond times and atomic rewrite | unit | `uv run pytest tests/demo/test_demo_tree_snapshot.py tests/demo/test_demo_recovery.py -q` | ❌ created in task | ⬜ pending |
| 7-05-02 | 05 | 2 | DEMO-04 | T-07-18, T-07-19, T-07-20 | Local adapter forced; refuses to run before the harvest | unit | `uv run pytest tests/demo/test_demo_recovery.py -q` | ❌ created in task | ⬜ pending |
| 7-06-01 | 06 | 3 | DEMO-02 | T-07-21, T-07-22, T-07-25 | Codex refused on the unflagged id; confirmation flag required; loopback serving | unit + live | `uv run pytest tests/demo/test_demo_run.py -q`; `rg -q` on `workspace/demo/logs/925100/signed-in-check.txt` | ❌ created in task | ⬜ pending |
| 7-06-02 | 06 | 3 | DEMO-03 | T-07-23 | Harvest only into gitignored workspace, copies with metadata, no move | unit + live | `uv run pytest tests/demo/test_demo_harvest.py -q`; `test -s workspace/demo/runs/llama-cpp/harvest.json` | ❌ created in task | ⬜ pending |
| 7-07-01 | 07 | 4 | DEMO-03 | T-07-27, T-07-29 | No-egress preflight; unconfirmed codex run refused | live | `rg -q "preflight=ok" workspace/demo/logs/925101/preflight.txt` | n/a (runtime) | ⬜ pending |
| 7-07-02 | 07 | 4 | DEMO-03 | T-07-26, T-07-28 | Operator confirms egress before any remote call | manual (blocking checkpoint) | n/a | n/a | ⬜ pending |
| 7-07-03 | 07 | 4 | DEMO-02, DEMO-03 | T-07-26 | Remote run only after "confirmed"; decline writes NOT_RUN.txt | live | `test -s` on `workspace/demo/runs/codex/NOT_RUN.txt` or on the codex harvest plus signed-in check | n/a (runtime) | ⬜ pending |
| 7-08-01 | 08 | 5 | DEMO-04 | T-07-32, T-07-33 | Gate and scripts unchanged; local adapter only | live | `jq -e` on `workspace/demo/recovery/evidence.json` | n/a (runtime) | ⬜ pending |
| 7-08-02 | 08 | 5 | DEMO-03 | T-07-30, T-07-31 | No absolute path or secret in the committed report; N beside every rate | unit + doc | `uv run pytest tests/demo/test_demo_report.py -q`; negative `rg` over `docs/results` | ❌ created in task | ⬜ pending |
| 7-08-03 | 08 | 5 | DEMO-03, DEMO-04 | T-07-30 | No home path in ADR 0047 | doc | `rg -n "^Accepted" docs/decisions/0047-demo-lecture-measurement-method.md` | ❌ created in task | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Existing infrastructure covers the framework (pytest 9.1.1, pytest-asyncio, httpx in the dev group). No separate Wave 0 plan: each task that adds code writes its test file first under `tests/demo/` (no `__init__.py`; every basename starts with `test_demo_` to stay unique), and fixtures (run records, transcripts, corrections, logs, units) are built in-test from the domain and run-record models.

- [ ] `tests/demo/test_demo_script.py`, `tests/demo/test_demo_build.py` (07-01, 07-02)
- [ ] `tests/demo/test_demo_wer.py`, `tests/demo/test_demo_asr_measure.py`, `tests/demo/test_demo_run_metrics.py`, `tests/demo/test_demo_phase_timing.py`, `tests/demo/test_demo_report.py` (07-03, 07-08)
- [ ] `tests/demo/test_demo_dry_run.py` (opt-in with `LECTURE_STUDY_DEMO_TOOLS=1`), `tests/demo/test_demo_site_access.py` (07-04)
- [ ] `tests/demo/test_demo_tree_snapshot.py`, `tests/demo/test_demo_recovery.py` (07-05)
- [ ] `tests/demo/test_demo_run.py`, `tests/demo/test_demo_harvest.py` (07-06)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Narration quality and slide/narration alignment of the rebuilt video | DEMO-01 | Perceptual (07-02 human-check) | Play `workspace/demo/aula-demo.mp4`; narration intelligible, slides change with narration, no clipped sentence |
| Browser sign-in and video playback on the served site | DEMO-02 | The scripted check proves the HTTP flow, not browser rendering (07-06 human-check) | `make demo-serve`, open `https://127.0.0.1:8443`, sign in with the values in `workspace/demo/site-access/.env`, open the "(adaptador local)" lecture, play the watch page |
| Remote egress confirmation for the codex run | DEMO-03 | Operator decision with provider terms the repository does not record (07-07 blocking checkpoint) | Answer "confirmed" or "decline" at the checkpoint |
| Live runs (local and codex adapters, recovery) | DEMO-02, DEMO-03, DEMO-04 | Need provisioned engines, the Qwen2.5-14B model, the Kokoro cache, whisper and a logged-in codex CLI; not available in CI | Executed in the main checkout by 07-06, 07-07 and 07-08; evidence under `workspace/demo/` and the committed `docs/results/` |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 7s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** {pending / approved YYYY-MM-DD}
