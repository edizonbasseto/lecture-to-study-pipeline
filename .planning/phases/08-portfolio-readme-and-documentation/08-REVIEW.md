---
phase: 08-portfolio-readme-and-documentation
reviewed: 2026-10-16T00:00:00Z
depth: deep
files_reviewed: 7
files_reviewed_list:
  - scripts/check_documentation.py
  - tests/test_check_documentation.py
  - Makefile
  - README.md
  - docs/operations.md
  - .github/workflows/ci.yml
  - docs/decisions (read-only reference for claim tracing)
findings:
  critical: 0
  warning: 1
  info: 6
  total: 7
status: issues_found
---

# Phase 8: Code Review Report (re-review of the corrected phase-final diff)

**Reviewed:** 2026-10-16
**Depth:** deep (every claimed fix probed against a scratch copy of the repository outside the working tree; documentation claims re-traced to ADRs 0027, 0039, 0045, 0046 and to `scripts/01_provision_engines.sh`)
**Files Reviewed:** 6 changed files plus the cited ADRs
**Status:** issues_found (no blocker; one warning and six info items remain)

## Summary

Scope: `git diff main..HEAD -- scripts tests Makefile README.md docs .github` at HEAD c5d626a.

Baseline at HEAD: `make docs-check` exits 0 (2 documents, 21 command lines, 0 findings), 72 checker tests pass (was 55), `ruff check` and `ruff format --check` are clean, and `mypy src scripts/check_documentation.py` reports no issues. The working tree has no source modifications (only the two untracked planning artifacts).

Every claimed fix of CR-01 and WR-01 to WR-04, WR-09 to WR-12 was verified as real. The probes ran against a copied root so the repository stayed untouched. No regression was found in the changed checker code. The three items knowingly skipped (anchors, coverage of inline and root-level references, per-ADR quote attribution) are now stated accurately in the README and in `docs/operations.md`. The substring/option rule (original WR-07) is disclosed as "textual presence" but one of its failure modes is not disclosed and remains open.

## Resolved (verified)

| Earlier ID | Result | Evidence |
| --- | --- | --- |
| CR-01 critic-gate guarantee | Resolved | `README.md:25` now scopes the guarantee to the four scripts (mock exam, podcast, personal-study flashcards, study guide), "post-repair units", whole-lecture refusal, "the site does not re-check how a file was produced", pre-gate material "must be regenerated or deleted", and files served unchanged cannot carry the notice. Each element matches ADR 0045 section 1 (lines 56-59, 60-103), Consequences and section 3. The decisions-table row (`README.md:121`) carries the same caveat. |
| WR-01 unterminated fence | Resolved | `_fence_blocks` yields an unclosed block with `closed=False`; `_unterminated_fence_findings` reports `unterminated-fence` at the opening line, and the commands inside are still checked (probe: both `unterminated-fence` and `make-target: nonexistent` reported). Tests `TestUnterminatedFence`. |
| WR-02 redirections and `&` | Resolved | Probe `make test 2>&1 \| tee out.log`, `> out.log 2>&1`, `&> out.log` yields no spurious finding; `make a & make zzz` now flags both targets (`&` is a separator). Tests `TestRedirectionsAndBackground`. |
| WR-03 third-party modules and absolute commands | Resolved | `python -m pytest` and `/usr/bin/open x` are classified `KIND_OTHER` and listed as "not statically checked" (probe). Relative repository scripts are still checked. Tests `TestProgramsOutsideTheRepository`. |
| WR-04 `uv run` options | Resolved | `uv run --group dev python scripts/missing.py`, `uv run --python 3.12 ...` and `uv run -- python ...` all reach the script rule (probe: `script-missing` reported, `flag-missing --bogus-flag` reported). Tests `TestUvRunOptions`. |
| WR-06 and WR-05 and WR-08 wording | Resolved as disclosure | See "Disclosed limitations" below; the claim in the README and in `docs/operations.md:214` now matches behaviour. |
| WR-09 exit code | Resolved | All reads go through `_read_text` or `_read_lenient`; probe with an unreadable decision file exits 2 with a refusal message, a document outside the root exits 2, a missing document exits 2. The docstring states that a usage error also exits 2. Tests `TestUnprocessableInput`. |
| WR-10 ADR 0039 problem statement quoted as constraint | Resolved | `docs/operations.md:191` no longer quotes Context point 4; it cites "password checks run off the event loop (decision 5)", which is present in ADR 0039 decision 5 (line 135). The remaining quote is verbatim. |
| WR-11 invented causal link for the text guard | Resolved | `README.md:125` now says the critic cannot catch the defect class, so a guard sits in the retry, and that the prompt alone "was rejected because the first run after the prompt fix still produced six questions with a referring phrase". This matches ADR 0027 alternative 1 (line 69). |
| WR-12 quick start prerequisites | Resolved | `README.md:153` now lists `git`, `cmake` and a C/C++ toolchain; confirmed against `scripts/01_provision_engines.sh:95` (`git clone`) and `:119` (`cmake -B build ... && cmake --build build`). |
| IN-01 mypy gap | Resolved | `Makefile:22` and `.github/workflows/ci.yml:76` run `mypy src scripts/check_documentation.py`; the command passes. |
| IN-04 uncited inference | Resolved | `docs/operations.md:196` labels the multi-process statement "Inference of this document, not an ADR statement". |
| IN-06 argument confinement | Resolved | `_read_document` applies `_inside_root`; probes with `/etc/hosts` and `../x.md` exit 2. |
| Local adapter no-egress wording (new fix in c5d626a) | Verified | `README.md:46` and `docs/operations.md:61` now say the adapter "passes no network flags (by construction, no OS sandbox)" and "records no OS sandbox and a trusted pinned binary"; ADR 0046 (lines 232-241 and 536) states exactly that. |
| Walkthrough verification wording | Verified | `README.md:258` now says `make -n` prints commands without running them, so the egress statement is accurate. |

The new code paths introduced by the fixes were also checked for regressions: `_without_uv_options` handles `--` and value-taking options without an index overrun, `_is_descriptor_of_redirection` cannot index past the end (slice), the unterminated block is checked once per rule (no double reporting of commands), the `main` handler maps `OSError` and `ValueError` to exit 2, and the bounded link pattern (`TestLinkBound`) keeps ordinary links matching.

## Warnings

### WR-01: Subcommand and long-option rules still misreport, and one failure mode is not disclosed

**File:** `scripts/check_documentation.py:537-544, 553-556`; disclosure at `scripts/check_documentation.py:11-14`, `README.md:168`, `docs/operations.md:214`
**Issue:** Original WR-07, partially addressed by disclosure only. Reproduced on the corrected code:
- `python -m lecture_study.cli.main --whisper-model-path model.bin run-all` reports `subcommand-missing: model.bin` (the first non-dash word is the value of the preceding option). This is a false positive on valid documentation.
- `flag not in source` is still a raw substring test, so `--lecture-id` passes when the source holds only `--lecture-ids` or a comment mentioning the option.
The README and the module docstring say "textual presence", which honestly covers the second case, but the first case (valid documentation rejected) is not mentioned anywhere.
**Fix:** Either state in the docstring and README that "a value placed before the subcommand is read as the subcommand", or skip the value of a flag when it is not written as `--flag=value` and the next word does not start with a dash only when the module declares that option as value-taking. Match flags with `(?<![\w-])--flag(?![\w-])`. Add a test for the value-before-subcommand shape.

## Info

### IN-01: Anchor fragments are unchecked (disclosed, original WR-05)

**File:** `scripts/check_documentation.py:609`; disclosure `README.md:168`
**Issue:** `[x](#nope)` and `[x](README.md#nope)` pass (probe). The README table of contents consists of anchors only. The README now states "anchors, the part after `#`, are not checked", so the claim is honest; the gap remains.
**Fix:** Optional: collect heading slugs of the target file and check the fragment.

### IN-02: Inline `make` commands, root-level files and bare package paths are unchecked (disclosed, original WR-06)

**File:** `scripts/check_documentation.py:82-93, 615-618`; disclosure `README.md:168`, `docs/operations.md:214`
**Issue:** Probe confirms `` `make no-such` `` in running text passes. The README names the unchecked categories exactly (running-text `make` commands, root-level files such as `Dockerfile.site` or `pyproject.toml`, bare package paths such as `ops/`, unlabelled fences). The statement matches behaviour.
**Fix:** Optional: check inline `make <target>` and single-word inline tokens containing `.` or `/` against the root with `Path.exists`.

### IN-03: Blockquote attribution is not verified (disclosed, original WR-08)

**File:** `scripts/check_documentation.py:230-237, 630-640`; disclosure `README.md:168`, `docs/operations.md:214`
**Issue:** A quote passes if it occurs in any ADR, and a one-word blockquote passes (`> unconditionally`, probe). Both documents say "not that it comes from the ADR cited". The statement is honest; no minimum quote length is enforced, which the disclosure does not mention.
**Fix:** Optional: per-ADR matching against the nearest cited ADR and a minimum word count.

### IN-04: Codex variant does not say how to harvest the Codex run (unchanged, original IN-03)

**File:** `README.md:219-225, 246-253`
**Issue:** Step 6 uses the default `ADAPTER=llama-cpp` and `LECTURE`, so a reader of the Codex variant gets no instruction to harvest with `ADAPTER=codex LECTURE=925101`, and the regenerated report would lack the Codex section described under "Measured results". Not changed in the fix commits.
**Fix:** Add `make demo-harvest ADAPTER=codex LECTURE=925101` to the Codex variant block, or state that the Codex figures come from the Phase 7 harvest.

### IN-05: Architecture diagram omits stages 11 and 12 and the cards-to-guide edge (unchanged, original IN-05)

**File:** `README.md:29-89, 91`
**Issue:** Stages 11 (publishing) and 12 (flashcards) are in the stage map but absent from the diagram, with no sentence saying so; ADR 0045 section 5 says the stage 18 deck "feeds the study guide", while `CARDS` and `GUIDE` are drawn as parallel siblings. Not changed in the fix commits.
**Fix:** Add one sentence listing the omitted stages and an edge `CARDS --> GUIDE`.

### IN-06: Hygiene rule still flags URLs and covers only home directories (partially addressed, original IN-07)

**File:** `scripts/check_documentation.py:109-111`
**Issue:** The new lookbehind `(?<![\w.~-])` does not exclude `/`, so `https://home/x` still reports `machine-path` (probe), while `/Volumes/...` and `~/...` are not reported. In addition, `_LINK` bounds the link text to 500 characters (`[^\]]{0,500}`), so a link with longer text is silently not checked (by inspection). Neither affects the current documents.
**Fix:** Use `(?<![\w./~-])` for the home-path rule and, if wanted, add `/Volumes/` and `~/`.

---

_Reviewed: 2026-10-16_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
