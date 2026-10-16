---
phase: "08"
slug: "portfolio-readme-and-documentation"
status: verified
verdict: SECURED
threats_total: 16
threats_closed: 16
threats_open: 0
asvs_level: 1
block_on: high
audited_head: b4dea35
audited_diff: "main..HEAD"
created: "2026-10-16"
---

# Phase 8 - Security

> Per-phase security contract: threat register, accepted risks and audit trail for the portfolio README, the operating-boundaries document and the documentation checker.

Verdict: **SECURED**. All 16 register entries are closed (14 mitigations verified in the phase-final diff, 2 accepted risks logged). No open threat at or above the `high` block threshold; no open threat below it. Five non-blocking advisories are recorded and are not counted in `threats_open`.

Configuration: no `.planning/config.json` exists, so the GSD defaults apply (`asvs_level: 1`, `block_on: high`). The four threats named for re-verification (T-08-01, T-08-03, T-08-05, T-08-07) were verified at L3 depth (data-flow trace, adversarial probes, edge cases); the remaining threats at L1/L2 depth.

Audited object: `git diff main..HEAD` at HEAD b4dea35 (12 commits, 11 files) plus the untracked `08-REVIEW.md`. Implementation files were not modified by the audit.

---

## Trust Boundaries

| Boundary | Description | Data Crossing |
|----------|-------------|---------------|
| documentation text to the checker | document content is parsed; no documented command is executed | markdown text, command lines as strings |
| repository files to checker output | findings are printed to a terminal and CI log | rule name, line number, missing token |
| repository to public readers | README and `docs/operations.md` become part of a public repository (Phase 9) | prose, ADR quotations, measured figures |
| ADR text to document | statements about guarantees and limits must match the records | enforced-versus-procedural claims |
| walkthrough execution to local services | executed steps start a local HTTPS server and run local preflights | exit codes, status counters |
| walkthrough to remote provider | the remote adapter step would send lecture content off the machine | lecture content (synthetic) |

---

## Threat Register

Status vocabulary: closed (mitigation verified, or acceptance logged) / open. Line references are to the audited HEAD.

| Threat ID | Category | Component | Severity | Disposition | Evidence | Status |
|-----------|----------|-----------|----------|-------------|----------|--------|
| T-08-01 | Tampering | `scripts/check_documentation.py` command handling | medium | mitigate | Imports are standard library only (`check_documentation.py:20-29`); no `subprocess`, `os.system`, `socket`, `importlib`, `eval` or `exec` anywhere in the file (pattern search); `lecture_study` occurs once, as a string constant (`:64`). Runtime proof: the checker was run under an audit hook against a scratch repository whose README documented `make build` (Makefile recipe writes a marker), two scripts and a module that write markers, `touch`, `curl` and `rm -rf docs`. Result: no marker file created, `docs/` intact, README and Makefile hashes unchanged, zero process, network or write audit events, only standard-library modules imported. | closed |
| T-08-02 | Information disclosure | checker output | medium | mitigate | `Finding.render` emits only document, line, rule, detail (`:125-126`); machine-path detail is the constant `home-directory path` (`:665`); quote detail is the constant `the passage is not in docs/decisions` (`:639`); refusal text names a relative document path only (`:259-261`, `:254`). Pinned by `TestMachinePathRule.test_each_home_directory_prefix_is_reported_without_echo` and the exit-code test asserting the root path is absent from stderr. See advisory A-2 for the one traceback path. | closed |
| T-08-03 | Tampering | link and path resolution | medium | mitigate | Re-verified at L3. Every path entry point is confined: links `_link_findings` -> `_exists_inside_root` (`:605-612`, `:579-580`); inline-code and command-word paths `_argument_path_findings` (`:595-602`); script and module source `_source_text` -> `_inside_root` (`:568-572`); document arguments `_read_document` (`:291-294`). Probes (scratch repositories): link to an existing outside file via `../`, an absolute path, `docs/../../`, and an in-root symlink to an outside file each report `path-missing`, identical to a nonexistent target (no existence oracle); `python -m` with a traversal module name reports `module-missing`; `python scripts/../../x.py` reports `script-missing`; an absolute, `..`, or symlinked document argument is refused with exit 2. Tests: `test_a_link_leaving_the_root_is_reported`, `TestDocumentConfinement`. See advisories A-1 and A-2 for reads outside link and path resolution. | closed |
| T-08-04 | Repudiation | quote rule | medium | mitigate | `_quote_findings` (`:630-640`) folds whitespace and blockquote markers and requires the passage in the folded `docs/decisions` text (`:233-237`, `:657-659`); `test_an_invented_quote_is_reported` and `test_a_quote_that_reorders_adr_words_is_reported` pin it. Stronger manual check: each of the 21 blockquotes of `docs/operations.md` was matched per ADR against the ADR named by its `Source:` line or lead-in sentence: 0 mismatches. The checker's weaker semantics (any ADR, not the cited one) are disclosed accurately at `README.md:168` and `docs/operations.md:214`. | closed |
| T-08-05 | Denial of service | regular expressions over documents | low | accept | Premise verified. `_LINK` is bounded (`:102`: 500, 2000 and 500 character caps). Adversarial 200 KB single-line inputs for every pattern family (unclosed brackets, link pairs, ADR lists with `,`/`and`, ADR plus whitespace or dashes, backtick runs, blockquote markers, fence lines, home-path prefixes, shell tokens, redirections, continuations, Makefile lines with and without colon) finished in 0.03 s to 1.83 s each (process time); no super-linear regex behaviour. Only super-linear step: line-number counting per ADR reference (advisory A-3). Logged in Accepted Risks. | closed (accepted) |
| T-08-06 | Information disclosure | `docs/operations.md` content | high | mitigate | gitleaks 8.30.1: full history (144 commits), `main..HEAD` (12 commits) and a per-path directory scan of every changed path, including the untracked review file: no leaks. Private-denylist scanner (`scripts/denylist_scan.py`, 8 terms, positions only): exit 0 on the `main..HEAD` log (content, messages, identities), on `git diff main..HEAD`, on changed file names, on `08-REVIEW.md` and on the branch name. Independent search of added lines: no machine path other than the checker's own regex and test fixtures (`someone` placeholders), no credential-shaped assignment, no key-shaped token (hex hits are git object ids in planning front matter), hosts limited to `docs.astral.sh` and `example.org`. `make docs-check` machine-path rule: 0 findings. | closed |
| T-08-07 | Repudiation | statements of what is enforced versus procedural | high | mitigate | Re-verified at L3, all claim sites. `README.md:25` now limits the guarantee to the four generator scripts, states that the site does not re-check provenance and that material generated before the gate was not gated, and cites ADR 0045 section 1 and Consequences (ADR 0045 lines 85-102, 198-201, 219-220). The same caveat is carried at `README.md:121`. `README.md:123` and `:268` and `docs/operations.md:27-74` classify egress as code (fail closed) and the provider terms and patient-identifier detection as procedural, as ADR 0046 decision 6 (lines 274-348) and ADR 0045 section 2 (lines 121-124) record. `docs/operations.md:191-196`: the ADR 0039 problem-statement quote (Context point 4) is gone; the section now cites decision 5 for off-event-loop password checks, quotes only the in-process sentence (ADR 0039 lines 135-136) and labels the multi-process consequence "Inference of this document, not an ADR statement". `README.md:125` now matches ADR 0027 (Context: the critic cannot catch the defects; Alternatives 1: prompt fix alone rejected on the six-of-twelve evidence). Code behind the "enforced" labels: `require_remote_egress_allowed` runs before any executable is resolved (`src/lecture_study/llm/factory.py:43-47`, typed refusal at `gateway.py:89-98`); one shared `UNCERTIFIED_NOTICE` (`site/renderer.py:42-54`, used at `:759` and `site/quiz.py:241,292`); the documented refusal path was executed live: `make demo-preflight ADAPTER=codex LECTURE=925101` exits 2 with "refused" before any step runs (`scripts/demo_run.py:276-282`). See advisory A-5 (strength qualifier on a diagram label). | closed |
| T-08-08 | Spoofing | wording suggesting certification or clinical authority | medium | mitigate | ADR 0024 passages on the ban against presenting artifacts as certified, on "no clinical, academic, or publication authority" and on "no standing toward eventual production certification" appear as blockquotes at `docs/operations.md:104-117`, each verified verbatim in ADR 0024 by the per-ADR match; `README.md:122,270,279` make no claim of authority. | closed |
| T-08-09 | Information disclosure | demo outcome overstated | medium | mitigate | Every figure in `README.md:138-142` and `docs/operations.md:157-165` was found in `docs/results/demo-results.md` with its denominator (14/3/19/0 of 620, 16 of 16, 14 of 16, 0 of 1, 10 of 10, 6 of 6, 0 of 4, 1 of 10, 243.6 s, 2.3%, 0.5%, 3.1%, 87.5%, 100.0%); the partial DEMO-04 outcome is stated at `README.md:148,271` and `docs/operations.md:165-169`. | closed |
| T-08-10 | Information disclosure | README and SUMMARY content, executed walkthrough output | high | mitigate | Same scan set as T-08-06 (gitleaks, denylist, credential-shaped and path searches) over README, three SUMMARY files and the review file: clean. `README.md:205` names the sign-in location as gitignored only; `workspace/` is ignored (`.gitignore:20`) and nothing under `workspace/` is tracked. The SUMMARY records exit codes and counters only. | closed |
| T-08-11 | Information disclosure | executed walkthrough reaching the remote provider | high | mitigate | `Makefile:10` defaults `CONFIRM_REMOTE_EGRESS` to empty and passes the flag only when the value is `yes` (`:54,60`); the driver refuses the codex adapter without the flag before any environment, preflight or step (`scripts/demo_run.py:276-282`, exit code constant 2 at `:61`); live run of the refusal path exited 2. `README.md:246-253` labels the second command as documentation only. Second barrier: ADR 0046 decision 6 fail-closed flag (see T-08-07). | closed |
| T-08-12 | Repudiation | results overstated or partial DEMO-04 outcome hidden | medium | mitigate | Figures cross-checked (T-08-09); Codex figures labelled one pack of one run with the model named (`README.md:146`); partial outcome and not-a-benchmark statements at `README.md:146,148,271,282`. | closed |
| T-08-13 | Tampering | executed steps rewriting tracked files | medium | mitigate | `git diff main..HEAD` contains no path under `docs/results`, `src`, `infra`, `config`, `Dockerfile.site`, `pyproject.toml` or `uv.lock`; `git status --porcelain docs/results` prints nothing. | closed |
| T-08-14 | Denial of service | demo server left listening | low | mitigate | `lsof` on TCP 8443 shows no listener at audit time; the SUMMARY records the same after the executed check. Point-in-time evidence. | closed |
| T-08-15 | Spoofing | README claiming the whole walkthrough was re-executed | medium | mitigate | `README.md:255-262` separates steps executed on 2026-10-16 from steps cited to Phase 7 and states that no executed command passed the confirmation variable; `08-03-SUMMARY.md` table carries a level per step (executed / cited from Phase 7). | closed |
| T-08-SC | Tampering | package installs | low | accept | No dependency file changed (`pyproject.toml`, `uv.lock`, `.python-version` absent from the diff); the CI change only adds a `mypy` target path (`.github/workflows/ci.yml`). Logged in Accepted Risks. | closed (accepted) |

*Severity: critical > high > medium > low. Only open threats at or above `block_on: high` count toward `threats_open`.*

---

## Accepted Risks Log

| Risk ID | Threat Ref | Rationale | Accepted By | Date |
|---------|------------|-----------|-------------|------|
| AR-08-01 | T-08-05 | Inputs are repository-controlled markdown of a few hundred lines. Regular expressions are bounded or unambiguous; audited at 200 KB adversarial inputs with a worst case of 1.83 s. The only super-linear step (line-number counting per ADR reference) costs 0.28 s on a 2000-line document and about 8 s at 80,000 ADR mentions. | Planner disposition in `08-01-PLAN.md`; premise re-verified by the audit | 2026-10-16 |
| AR-08-02 | T-08-SC | The phase installs no package. Standard library only; dependency files unchanged. | Planner disposition in `08-01-PLAN.md`, `08-02-PLAN.md`, `08-03-PLAN.md`; premise re-verified by the audit | 2026-10-16 |

---

## Advisories (unregistered, non-blocking, not counted in `threats_open`)

| ID | Severity | Observation | Evidence | Suggested action |
|----|----------|-------------|----------|------------------|
| A-1 | low | Two fixed-name inputs are read without the root confinement applied to every other read: the `Makefile` (`check_documentation.py:225`) and `docs/decisions/*.md` (`:233-237`, via `_read_lenient`). A committed symlink could make the checker consult a file outside the root: a Makefile symlink was honoured (a target present only in the outside file passed), and a blockquote matching only an outside decision-file symlink passed. Contents are never printed, so exposure is a pass/fail signal for a repository committer. | Scratch probes P-A and P-B | Apply `_inside_root` to the Makefile and to each decision page, or refuse symlinks. |
| A-2 | low | A symlink loop under a linked path raises an uncaught `RuntimeError` from `Path.resolve()` on Python 3.12; the process exits 1 (the findings code, not the documented 2) and the traceback prints the absolute repository path. `main` catches only `UnreadableInputError`, `OSError` and `ValueError` (`:249-254`). The gate still fails closed (non-zero). | Scratch probe P-C; `_inside_root` at `:575-576` | Catch `RuntimeError` in `main` (or in `_inside_root`) and return exit 2 with the generic message. |
| A-3 | info | `_adr_findings` counts newlines from the start of the text for each ADR reference (`:624`), which is quadratic in references. Within the accepted risk AR-08-01. | Scaling probe | None required; precompute line offsets if documents grow. |
| A-4 | info | `core.hooksPath` is unset in this checkout, so the local leak-gate hooks were not active during the phase. Compensated by running gitleaks and the denylist scanner manually over the final range; CI runs both on push. | `git config core.hooksPath` empty | Run `make hooks` before committing the audit artifacts and before Phase 9. |
| A-5 | low | `README.md:47` labels the local adapter node "local model, no network egress" without the strength qualifier of ADR 0046: the property is shown by construction and tests, not by confinement, there is no operating-system sandbox, and the pinned binary is trusted (ADR 0046 lines 233-241 and 535-536). `docs/operations.md:61` repeats the ADR 0046 phrase "entirely local" (lines 101-102) likewise. Judged non-blocking for T-08-07 because the label uses the ADR's own named property, which is enforced in code and misclassifies nothing as code that is procedural. | Read of ADR 0046 against the README label | Reword the node, for example "local model, passes no network flags (by construction, no OS sandbox)", before the repository goes public. Elevate to a blocking finding if the strict reading of T-08-07 is preferred. |

---

## Verification Evidence (commands run by the audit)

| Check | Result |
|-------|--------|
| `make docs-check` | exit 0: 2 documents, 21 command lines, 0 findings |
| `pytest tests/test_check_documentation.py` | 72 passed |
| `ruff check`, `ruff format --check`, `mypy scripts/check_documentation.py` (checker and its tests) | clean |
| `gitleaks git` (`--all`, `main..HEAD`) and `gitleaks dir` per changed path | no leaks |
| `scripts/denylist_scan.py` over range log, diff, file names, untracked review, branch name | exit 0 on all |
| Audit-hook run of the checker on an adversarial scratch repository | no process, network or write events; no marker files; inputs unchanged |
| Confinement probes (links, inline paths, scripts, modules, documents, symlinks) | all outside-root targets reported as `path-missing` / `script-missing` / `module-missing` or refused with exit 2; symlinked Makefile and decision files followed (A-1); symlink loop traceback (A-2) |
| Regex stress probes (200 KB, 36 cases) | maximum 1.83 s per run |
| `make demo-preflight ADAPTER=codex LECTURE=925101` | exit 2, refused before any step |
| Per-ADR attribution of all 21 `docs/operations.md` blockquotes | 0 mismatches |

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-10-16 (earlier run; verdict OPEN_THREATS as reported by the requester, counts not recorded in this file; T-08-07 named, including the `README.md` line 25 overclaim and the ADR 0039 quotation) | 16 | not recorded | not recorded | gsd-security-auditor |
| 2026-10-16 (re-run on the corrected phase-final diff, HEAD b4dea35) | 16 | 16 | 0 | gsd-security-auditor |

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer)
- [x] Accepted risks documented in the Accepted Risks Log
- [x] `threats_open: 0` confirmed
- [x] `status: verified` set in frontmatter

**Approval:** verified 2026-10-16 (auditor verdict; persistence and commit owned by the orchestrator)
