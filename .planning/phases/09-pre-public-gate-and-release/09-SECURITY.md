---
phase: "09"
slug: "pre-public-gate-and-release"
scope: "PUBG-02 whole-repository security review before the repository is switched to public"
status: open_threats
verdict: OPEN_THREATS
threats_total: 29
threats_closed: 25
threats_open: 1
threats_open_nonblocking: 3
asvs_level: 2
block_on: high
audited_head: 0ba2051
audited_refs: "all refs (main and three merged gsd/phase-* branches), reflog, 7 unreachable local commits"
reaudited_head: cf9c8f2
reaudited_range: "main..HEAD (0ba2051..cf9c8f2, 4 commits)"
reaudited: "2026-10-16"
created: "2026-10-16"
---

# Phase 9 - Security (PUBG-02, whole repository)

Verdict: **OPEN_THREATS**. 23 of 29 register entries are closed. One entry is open at or above the `high` block threshold (T-09-05, a decision gate on personal data in commit headers that no one has accepted or remediated). Five entries are open below the threshold and are non-blocking (T-09-12, T-09-20, T-09-26, T-09-27, T-09-29). `threats_open` counts only the blocking entry.

No secret, credential, key, private-denylist term, third-party personal datum, machine path or private hostname exists in the tracked tree, in any of the 148 commits on any ref, in the reflog, in the 7 unreachable local commits, or in the 9 GitHub Actions run logs and the one run artifact that become public with the repository. No live probe against the site access gate obtained content without valid credentials; two malformed-input probes provoked HTTP 500 responses (T-09-12). The open blocking entry is not a content leak of a secret; it is an irreversible-publication decision that the repository's own rule R0.1 lists explicitly ("commits") and that carries no recorded acceptance.

> Re-audit note: the frontmatter counts, the status of T-09-12 and T-09-26, and the advisory list reflect the re-audit of the corrected diff in the final section of this file (HEAD cf9c8f2). The register table and the paragraphs above are the first audit at HEAD 0ba2051 and are kept as the record of that audit; where they differ, the final section prevails.

Configuration: no `.planning/config.json` exists; `asvs_level: 2` and `block_on: high` were taken from the audit request. Access control, egress and path handling were traced to L3 depth (data flow plus adversarial probes). Implementation files were not modified by the audit.

---

## Trust Boundaries

| Boundary | Description | Data Crossing |
|----------|-------------|---------------|
| Local repository to public GitHub | Visibility switch publishes every reachable object, every Actions log and artifact | tree, 148 commits, commit headers, CI logs |
| Anonymous network client to site | Every request, including static files, passes the access gate | paths, cookies, Authorization header, form bodies |
| Authorized reader to site | Named readers, optionally restricted to path prefixes | session cookie, quiz answers |
| Operator CLI to child processes | Media tools, llama.cpp and Codex run as bounded children | argv, sanitized environment, lecture text |
| Child process to remote provider | Codex adapter sends lecture content off the machine | lecture content (per-lecture flag gated) |
| CI to private denylist secret | The `DENYLIST` repository secret is written to a runner file for one job | denylist terms (positions only reported) |
| Operator workstation to repository | Gitignored workspace, credentials, keys, local configuration | media, hashes, session key, certificates |

---

## Threat Register

Severity for threats found without a plan register was assigned retroactively (impact x likelihood). Status: closed (mitigation verified in code or configuration at the audited HEAD) / open. Line references are to HEAD 0ba2051.

| Threat ID | Category | Component | Severity | Disposition | Evidence | Status |
|-----------|----------|-----------|----------|-------------|----------|--------|
| T-09-01 | Information disclosure | Secrets in the tracked tree | high | mitigate | `gitleaks dir` over a `git archive HEAD` export (6.28 MB): no leaks. Independent sweeps (hidden directories included): no private-key or certificate markers, no provider token shapes (GitHub, OpenAI-style, AWS, Slack, Google, Hugging Face), no credentialed URLs, no quoted credential assignments of 12 or more characters (single hit: the literal `pull-through`), no base64 blobs of 48 or more characters, no high-entropy token outside lockfile hashes, pinned commit ids and SHA-256 pins. `git ls-files` shows one environment-file template only; no `.pem`, `.key`, credential or session-key file is tracked. | closed |
| T-09-02 | Information disclosure | Secrets anywhere in history | high | mitigate | `gitleaks git --log-opts=--all` (148 commits, 6.5 MB): no leaks. Repeated over the reflog plus the 7 unreachable commits (150 commits scanned): no leaks. History diff text (7.5 MB, all refs, merge diffs separated): no key block, token shape, credential assignment, `/Users/` or `/home/` path other than the placeholders `someone` and `page`, no CPF/CNPJ or phone shape. The set of file names ever present in history (470) equals the tracked set: no file was ever deleted, so no removed secret can hide in an earlier tree. No `.gitleaks.toml` or `.gitleaksignore` exists, so no finding is allowlisted. | closed |
| T-09-03 | Information disclosure | Private denylist terms in history | high | mitigate | `scripts/denylist_scan.py` against the real private denylist (positions only, terms never read): exit 0 over the CI-equivalent stream (`git log -p --all --diff-merges=separate` with author, committer and message fields, plus all ref names), over every historic file name, over the reflog plus unreachable commits, and over the text of all 9 CI run logs. The scanner fails closed on a missing or empty list (`denylist_scan.py:46-58`). | closed |
| T-09-04 | Information disclosure | Third-party personal data, patient data, real identifiers, machine paths, hostnames | high | mitigate | Hidden-directory-inclusive sweeps of the tree: e-mail addresses are all `example.com`, `example.org` or `example.invalid` (164 hits, 0 other); hosts are package indexes, `github.com`, `huggingface.co`, scientific publishers, `127.0.0.1` and `*.example.*`/`*.test`/`*.invalid` placeholders; no title-plus-name pattern, no professional-register or identity-document shape, no named owner or patient; IPv4 limited to loopback and `0.0.0.0`; the demo lecture script, schedule and slide source carry an explicit synthetic-provenance header. | closed |
| T-09-05 | Information disclosure | Operator personal e-mail address in commit headers | high | mitigate (not yet decided) | All 148 commits carry one identity (name plus personal e-mail) as author and committer; the tree itself contains the name only, in `LICENSE:3` and in accepted-by lines of `06-SECURITY.md` and `07-VERIFICATION.md`. The leak gate does not flag it: the denylist scan exit 0 shows the address is not a denylisted term, and gitleaks does not detect addresses. No ADR, plan or security report records an acceptance. Rule R0.1 of the operator's instructions names "commits" among the artifacts that must not carry PII. Publication is irreversible. In addition, the untracked `09-REVIEW.md` of this phase quotes the address verbatim at line 75 and would copy it into the tracked tree when committed (not caught by the hooks, since the term is not denylisted). Required: an explicit operator decision recorded in the phase plan or this file as an accepted risk (identity published deliberately), or remediation (history rewrite to the no-reply address with a mailmap, followed by a full re-run of the gitleaks and denylist scans and by new object ids in every document that cites a hash). In either case the verbatim address must be removed from `09-REVIEW.md` before it is committed. Severity is set to `high` on policy grounds and irreversibility, not on exploitability; an explicit acceptance closes it. | **open (blocking)** |
| T-09-06 | Information disclosure | GitHub-side content that becomes public with visibility | medium | mitigate | 9 workflow run logs downloaded and scanned (denylist exit 0, gitleaks no leaks, only the hosted-runner path `/Users/runner`); the single run artifact (`gitleaks-results.sarif`, 6.8 KB) holds a scan with zero results; no issues, pull requests, releases, environments, deploy keys, webhooks or repository variables exist; wiki enabled but empty; the push activity feed shows 8 fast-forward pushes and 1 branch creation, no force push, so no orphaned server-side object exists; remote carries only `main` (b2e5f06), no `refs/pull`. The repository secret list holds `DENYLIST` only. | closed |
| T-09-07 | Spoofing / elevation of privilege | Site access decision on the served path | high | mitigate | L3. In-process ASGI probes without client-side normalization: `.`/`..` segments, `?`, `#`, backslash and NUL return 400 (`app.py:484-503`); double-slash and encoded-slash forms return 401/403/404, never content; a symlink inside the served tree pointing to an outside file or directory returns 404; restricted readers are confined to their prefix (`/aluna//x`, `//aluna/x`, case variants, sibling prefix, `/videos`, `/simulado` all 403 or redirect); anonymous HEAD/OPTIONS/DELETE/TRACE on the exempt paths return 405 with `Allow`, on any other path 401. `/openapi.json` and `/docs` return 401 anonymously. `tests/test_site_*.py`: pass. | closed |
| T-09-08 | Spoofing | Session token, password storage | high | mitigate | HMAC-SHA-256 token keyed per person from the server secret plus the stored hash, expiry checked, constant-time comparison over bytes (`sessions.py:52-76,104-112`); secret read refuses fewer than 32 bytes (`signing.py:31-50`); PBKDF2-HMAC-SHA-256 with 16-byte salt and `compare_digest` (`access.py:27-91`); every candidate hashed so timing does not reveal list position (`access.py:161-177`); forged cookie, garbage Basic header and wrong Basic credentials all 401; cookie `__Host-pos_session` is `HttpOnly; Secure; Path=/; SameSite=Lax` (`app.py:790-800`). Advisories A-2 (iteration count, lifetime). | closed |
| T-09-09 | Tampering | Login CSRF and state-changing endpoints | medium | mitigate | Login POST without the nonce cookie, with a wrong token, and with a mismatched token: 403; valid pair: 303 (`login_csrf.py:30-59`, `app.py:631-638`); nonce cookie `__Host-` with `HttpOnly; Secure; SameSite=strict`; oversize login body 413. The only other state-affecting routes are sign-out (GET, accepted trade-off documented at `app.py:599-606`) and quiz grading (read-only compute behind authentication and a per-person delay). | closed |
| T-09-10 | Tampering | Open redirect through `next` | medium | mitigate | 17 hostile `next` values (`//host`, tab and newline variants, encoded slash, backslash, absolute URL, `/sair` spellings, `javascript:`) all resolve to a same-site path; the tab-prefixed form is rejected and `RedirectResponse` percent-encodes control characters (`app.py:820-872`). | closed |
| T-09-11 | Information disclosure | Security headers and CSP on every response | medium | mitigate | `SecurityHeadersMiddleware` is the outermost wrapper (`app.py:537`); the login form and the 200, 400, 401, 404, 405 and forced-500 responses all carry the five headers (probed), including CSP (hash-only inline, no `unsafe-inline`, `base-uri 'none'`, `form-action 'self'`, `frame-ancestors 'none'`), nosniff, `Referrer-Policy: same-origin`, HSTS and `X-Frame-Options: DENY`; authorized responses add `Cache-Control: private, no-store` and `Vary: Cookie`; markdown rendered with `html: False`; the JSON search index escapes `<` (`renderer.py:415`); quiz and login output escaped. | closed |
| T-09-12 | Denial of service | Unhandled exceptions on malformed credential input | medium | mitigate (gap) | Reproduced independently in-process: a session cookie whose expiry field is a run of more than 4300 ASCII digits, or an `Authorization: Basic` header with non-ASCII bytes, raises `ValueError` out of `identify()` (`sessions.py:87` `isdigit()` then `int()`; `app.py:896-897` catches `binascii.Error` and `UnicodeDecodeError` but not the `ValueError` raised by `b64decode` on non-ASCII text). `admit_authorized_person` catches only `CredentialsError` and `TooManyAttempts` (`app.py:560-563`), so the client receives the generic 500 and a traceback is logged. The same `isdigit()` then `int()` pattern exists in the quiz field parser (`quiz.py:202-208`, authenticated callers) and in the `Content-Length` check (`app.py:881`, shielded by the server). Impact: no authentication bypass, no data exposure, no secret in the exception text, fail-closed; availability and log noise only. Rated medium here; the parallel code review rates the same defect High. A fix of three lines (ASCII-only digit test with a length cap, `ValueError` in the Basic handler) plus regression tests is recommended before the switch. | **open (non-blocking)** |
| T-09-13 | Denial of service | Password-check abuse limits | medium | mitigate | Growing per-address wait, global waiting room of 24, 2 concurrent checks and 8 queued, bounded tracking table (`throttle.py:40-47,127-147`); admission is a synchronous reservation; shed attempts are rate-limited in the log. Residual: an anonymous flood can saturate the room and cause 429 for password sign-ins while it lasts; accepted and documented by ADR 0039 and `.planning/codebase/CONCERNS.md`. | closed (residual accepted by ADR) |
| T-09-14 | Tampering | Path traversal through identifiers and static serving | high | mitigate | `SafeIdentifier` (`^[A-Za-z0-9_-]+$`, `domain/models.py:28`) rejects `..`, `/`, spaces, NUL, CR, trailing newline and non-ASCII (probed); every reader of a per-lecture artifact validates with it (dozens of sites across `src` and `scripts`) including the quiz route (`quiz.py:110-121`) and the demo tooling (`demo/lecture_id.py`, which closes Phase 7 finding UF-1); the site generator does not follow symlinks when clearing or reading materials (`generator.py:174-178`, `catalog.py:392`); static serving refuses a symlink that escapes the tree (probe: 404). | closed |
| T-09-15 | Tampering | Command injection and unsafe deserialization | high | mitigate | No `shell=True`, `os.system`, `os.popen`, `eval`, `exec`, `pickle`, `marshal`, `yaml.load`/`unsafe_load`/`full_load`, `tarfile`, `extractall` or `verify=False` anywhere in `src`, `scripts` or `infra`; every process is started from an argument list with a resolved regular executable (`process_environment.py:57-85`) through `run_bounded_process` or `create_subprocess_exec` with timeout, output caps and a new session; YAML goes through `ops/strict_yaml.py` or `safe_load`. | closed |
| T-09-16 | Information disclosure | Credential inheritance by child processes | high | mitigate | Child environment is an explicit allowlist (`LANG`, `LC_ALL`, `LC_CTYPE`, `PATH`, `TEMP`, `TMP`, `TMPDIR`) and the sensitive-name blocklist (`PORTAL_`, `LECTURE_STUDY_QUALITY_`, `*_API_KEY`, `*_TOKEN`, `*_SECRET`, cloud names, `SSH_AUTH_SOCK`) is re-applied inside `run_bounded_process` (`process_lifecycle.py:176`); the Codex child adds `HOME` only, runs `--sandbox read-only` in a scratch directory (`codex_adapter.py:105-125`); the llama.cpp child is offline by flag and variable (`llama_cpp_adapter.py:50,208`). Advisory A-1 on what the read-only sandbox still permits. | closed |
| T-09-17 | Information disclosure | Remote egress of lecture content | high | mitigate | L3. Egress policy is consulted before the executable is resolved (`llm/factory.py:43-45`); live: `demo_run.py --adapter codex` without the confirmation flag exits 2 with "refused"; with the flag on a lecture not flagged `remote_llm_allowed` the preflight prints `refused ... not flagged remote_llm_allowed (ADR 0045 section 2)`; gateway, local-lecture, demo and CLI tests (714) pass. The provider's retention terms and patient-identifier detection remain procedural and are labelled so in the README and `docs/operations.md`. | closed |
| T-09-18 | Tampering | Python dependency supply chain | medium | mitigate | `uv.lock`: 90 packages, 89 from `pypi.org/simple`, none from git or URL, every artifact carries a hash; CI installs with `uv sync --group dev --locked` (`ci.yml:73`); OSV batch query for all 89 locked name-version pairs returned zero advisories. | closed |
| T-09-19 | Tampering | CI action and tool pinning | medium | mitigate | `actions/checkout` pin `11d5960` resolves to upstream tag `v4.4.0` (the `v4` tag); `astral-sh/setup-uv` pin `d0cc045` resolves to `v6.8.0`; gitleaks 8.30.1 `linux_x64` SHA-256 in `ci.yml:19` equals the digest in the upstream release checksum file and is verified with `sha256sum --check --strict` before use (`ci.yml:30`). | closed |
| T-09-20 | Tampering | Site container image supply chain | medium | mitigate (gap) | `Dockerfile.site:20` uses the moving tag `python:3.12-slim` without a digest; `:24` installs five top-level packages by version without hash verification, leaving every transitive dependency (Starlette, AnyIO, h11, Click, pydantic-core and others) unpinned and divergent from `uv.lock`. Already recorded as a candidate in `.planning/codebase/CONCERNS.md`. The image runs as uid 10001 and copies only the site package and `domain/models.py`. Remedy: pin the base by digest and install from `uv export --frozen --hash` with `--require-hashes`. | **open (non-blocking)** |
| T-09-21 | Tampering | Engine and model provisioning | medium | mitigate | whisper.cpp and llama.cpp are fetched by full commit id and checked out detached; Whisper model and each of three example-model shards verified by SHA-256 before use, `.part` file removed on mismatch; download is HTTPS-only with TLS 1.2 minimum and a pinned Hugging Face revision (`scripts/01_provision_engines.sh:17-45,98-160`). Homebrew formulae are unpinned (advisory A-6). | closed |
| T-09-22 | Elevation of privilege | CI token and trigger safety | high | mitigate | Workflow token `contents: read` at workflow level (`ci.yml:8-9`) and repository default `read`, with approval of pull-request reviews disabled; triggers are `push` to `main` and `pull_request` only (no `pull_request_target`, `workflow_run` or comment triggers); every checkout sets `persist-credentials: false` (`:24,44,69`); the only `${{ }}` expression is the secret in a step `env` (`:47`), so no event data is interpolated into a script. | closed |
| T-09-23 | Information disclosure | `DENYLIST` secret handling | high | mitigate | Secret passed through step `env`, written to the runner temp directory, never echoed; job fails closed when empty (`ci.yml:49-52`); the scanner prints term positions only and receives the secret file by path; fork pull requests cannot read the secret (job fails by design); the secret exists (`updatedAt` 2026-10-14T15:11:09Z, after the local file's last modification at 10:47 local that day); the latest run on `main` finished with `denylist-scan`, `secret-scan` and `test` all successful. The secret value cannot be read, so equality with the local list is circumstantial (advisory A-7). | closed |
| T-09-24 | Tampering | Local leak-gate hooks in force | medium | mitigate | Wrappers for `pre-commit`, `commit-msg`, `pre-merge-commit` and `pre-push` exist in `.git/hooks` and execute the versioned hooks; `core.hooksPath` is deliberately unused (ADR 0044 section 3), which corrects Phase 8 advisory A-4; the private denylist file exists, mode 600; `tests/test_git_hooks.py` and `tests/test_denylist_scan.py` pass. Hooks are bypassable and absent from clones; CI is the backstop. | closed |
| T-09-25 | Information disclosure | Gitignore coverage of workspace, credentials, keys, local configuration | high | mitigate | `git check-ignore -v` confirms an ignore rule for 40 probed paths: `workspace/`, `tmp/`, `engines/`, `config/site-credentials.txt`, `config/secrets/`, `config/session-key/`, `config/*.local.yaml` and `.json`, browser profile and cookie jars, `*.pem`, `*.key`, `*.p12`, `.netrc`, caches, graphify and GSD runtime state, and the media, document and database extensions; the environment-file patterns and the template exception are present in `.gitignore`. `git status --ignored` lists only expected local files, and nothing untracked is unignored. | closed |
| T-09-26 | Information disclosure | Agent-tool configuration directory not ignored by the repository | low | mitigate (gap) | `.claude/settings.local.json` is ignored only by the operator's global ignore file, not by `.gitignore`; `.claude/` as a whole and `.claude/settings.json` have no rule. On another machine or account the file would be staged by `git add -A`. Currently the directory holds that one local file. Add `.claude/` to `.gitignore`. | **open (non-blocking)** |
| T-09-27 | Information disclosure | Planning documents published wholesale | medium | mitigate (decision) | Scanned: no secret, path, host or personal datum beyond the operator's name and the purpose statement in `PROJECT.md` ("public portfolio artifact for an AI-engineering job search"); 25 plan files reference the operator's tool installation as `~/.claude-google/gsd-core/...`; `.planning/codebase/CONCERNS.md` publishes a candid list of known weaknesses (oversized modules, broad exception handlers, per-request secret read, image pin drift); `STATE.md` body contradicts its front matter. Nothing blocks publication, but the operator must decide consciously that `.planning/` ships and review `PROJECT.md:9` and `CONCERNS.md`. | **open (non-blocking)** |
| T-09-28 | Repudiation | Accuracy of security claims in public documents | medium | mitigate | Phase 8 advisory A-5 is resolved: `README.md:47` now reads "passes no network flags (by construction, no OS sandbox)" and `docs/operations.md:61` carries the ADR 0046 qualification; the live egress refusals match the README. `.planning/codebase/INTEGRATIONS.md` still describes a retired CI layout (gitleaks action, single scan job) and a "session key" distinct from the signing secret; informational. | closed |
| T-09-29 | Elevation of privilege | Repository settings and disclosure channel at visibility change | low | mitigate (gap) | Pending for the switch: Dependabot alerts disabled; `security_and_analysis` reports nothing for secret scanning and push protection; no branch protection on `main`; repository Actions permission allows all actions and does not require SHA pinning; no `SECURITY.md`; wiki, projects and issues enabled while unused. All are free on a public repository. | **open (non-blocking)** |

*Severity: critical > high > medium > low. Only open threats at or above `block_on: high` count toward `threats_open`.*

---

## Accepted Risks Log

No new risk was accepted by this audit. Carried from earlier phases and re-checked: AR-06-01, AR-06-02 (Phase 6), AR-08-01, AR-08-02 (Phase 8), the throttle-flood residual of ADR 0039 (T-09-13), and the documented absence of an operating-system sandbox for the local adapter (ADR 0046; wording verified in the README).

T-09-05 is **not** an accepted risk: no operator acceptance exists. Recording one is the operator's act and cannot be done by the auditor.

---

## Advisories (unregistered, non-blocking, not counted in `threats_open`)

| ID | Severity | Observation | Suggested action |
|----|----------|-------------|------------------|
| A-1 | low | The Codex child receives `HOME` and a read-only sandbox, which forbids writes but not reads; lecture text (transcript, slide OCR) is untrusted input to a remote model. A prompt-injection passage in a lecture could ask the model to read files under the home directory and echo them into generated material. No document mentions this residual (search for "injection" across README, operations and ADRs 0045/0046 found nothing). The prompt is also passed as a positional argument without a `--` terminator (`codex_adapter.py:105-117`); the prompt text starts with trusted instruction text, so no practical argument injection. | State the residual in `docs/operations.md`; consider `--` before the prompt. |
| A-2 | low | PBKDF2-HMAC-SHA-256 uses 240,000 iterations (above the ASVS 100,000 floor, below the 600,000 of current OWASP guidance). Sessions last 30 days, are stateless, and sign-out only clears the browser cookie; a copied cookie stays valid until expiry, a password change or removal of the person's line. Design choice of ADR 0039/0044. | Raise the iteration count with a re-hash on next sign-in; document the revocation model. |
| A-3 | low | Phase 8 advisories A-1 and A-2 on `scripts/check_documentation.py` remain (symlinked Makefile or decision file followed; symlink loop prints a traceback with the repository path). Committer-controlled input only. | As recorded in `08-SECURITY.md`. |
| A-4 | low | No deployment guidance states that TLS must terminate in front of the container (HTTP on `0.0.0.0:8000`, `Secure` cookies, Basic Auth accepted as a second door that sends credentials on every request) or that `make site` runs without TLS. | Add a "Deploying the site" paragraph to `docs/operations.md`. |
| A-5 | info | Local `main` is 59 commits ahead of `origin/main` (b2e5f06); the three `gsd/phase-*` branches are fully merged. A push of only `main` publishes everything; `git push --all` would add three stale branch names. The 59 commits were scanned here but have not passed CI. | Push `main` only; confirm CI green before the switch. |
| A-6 | info | CI hygiene: no `timeout-minutes`, `curl` for the gitleaks archive without `--proto '=https'`, Homebrew formulae and the uv version unpinned in the `test` job, no Dependabot configuration, action pins one major behind current. | Optional hardening. |
| A-7 | info | Equality of the `DENYLIST` secret with the local list cannot be verified from outside; only timing is consistent. | Operator confirms at the final check (PUBG-03); re-set the secret from the local file if in doubt. |
| A-8 | info | Seven unreachable commits (stashes and WIP) exist in the local object store; they were scanned clean, were never pushed (all remote pushes are fast-forward) and are not part of any clone. The demo credential writer opens files with `O_TRUNC` without `O_NOFOLLOW` inside `workspace/` (local, operator-owned). The `Server: uvicorn` header is sent. ffmpeg reads operator-registered media without a protocol whitelist. | None required. |
| A-9 | info | The parallel code review (`09-REVIEW.md`) raises licence, provenance and branding items (GPL-3.0 dependencies under an MIT repository, claim wording about "real" lectures in fixtures, hard-coded programme name) that are outside the security scope of this report and were not assessed here. | Resolve under PUBG-02 code review. |

---

## Verification Evidence (commands run by the audit)

| Check | Result |
|-------|--------|
| `gitleaks git --log-opts=--all` (8.30.1) | 148 commits, no leaks |
| same over reflog plus 7 unreachable commits | 150 commits, no leaks |
| `gitleaks dir` on `git archive HEAD` export; on 9 CI run logs | no leaks |
| `denylist_scan.py` on CI-equivalent history stream, all file names, reflog and unreachable commits, CI logs | exit 0 on all |
| Pattern sweeps (keys, tokens, paths, e-mail, CPF, phone, IPv4, URLs, entropy, credentialed URLs) over tree (hidden included) and history | placeholders only |
| `git ls-files -s`: symlinks, submodules; `.gitattributes` | none; none; absent |
| `git check-ignore -v` on 40 sensitive paths | all ignored, except the agent-tool directory (T-09-26) |
| In-process site probes (about 120 requests; raw ASGI scope and client) | no bypass; parser exceptions reproduced (T-09-12) |
| `pytest` security modules (site, hooks, denylist, process, paths, layering) | 352 passed |
| `pytest` gateway, local lectures, demo, CLI, terminology | 714 passed, 1 skipped |
| OSV batch query of 89 locked packages | 0 advisories |
| Action pins and gitleaks digest against upstream | match |
| `gh api` repository state (visibility private, secrets, runs, artifacts, activity, protection) | as recorded in T-09-06, T-09-22, T-09-23, T-09-29 |
| Live egress refusal (`demo_run.py --adapter codex`) | exit 2 without flag; preflight refused with flag on unflagged lecture |

Not done, stated so the gap is not read as a pass: no dynamic test against a real uvicorn socket or a TLS proxy; the 3,500-line and 2,800-line quality modules were pattern-scanned, not read line by line; the `DENYLIST` secret value and the private denylist contents were deliberately not read; model and dependency licences were not assessed; CI was not re-run on the 59 unpushed commits.

---

## Required actions to reach SECURED

1. T-09-05 (blocking): the operator records an explicit decision. Either accept the published identity as a risk (entry in this file or the Phase 9 plan, naming the acceptor and date), or rewrite the history to a no-reply address and re-run `gitleaks`, the denylist scan over all refs and the hook tests on the rewritten history.
2. Before `09-REVIEW.md` is committed, redact the verbatim address at its line 75.
3. Recommended before the switch (non-blocking): T-09-12 fix and regression tests; T-09-26 ignore rule; decision on `.planning/` (T-09-27); T-09-29 repository settings; T-09-20 image pins.
4. After any corrective change, re-run this audit against the corrected diff (rule R0.4).

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-10-16 (HEAD 0ba2051) | 29 | 23 | 6 (1 blocking, 5 non-blocking) | gsd-security-auditor |
| 2026-10-16 (HEAD cf9c8f2, re-audit of main..HEAD) | 29 | 25 | 4 (1 blocking, 3 non-blocking) | gsd-security-auditor |

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer)
- [x] Accepted risks documented in the Accepted Risks Log (none new)
- [ ] `threats_open: 0` confirmed (1 open: T-09-05)
- [x] `status` set in frontmatter (`open_threats`)

**Approval:** not granted. Persistence and commit of this file are owned by the orchestrator.

---

## Re-audit of corrected diff (HEAD cf9c8f2, range main..HEAD)

Requested by the caller after the corrective commits; rule R0.4 requires the review to cover the phase-final diff. Verdict: **OPEN_THREATS**. 25 of 29 register entries are closed, 4 are open: T-09-05 (blocking, `high`) and T-09-20, T-09-27, T-09-29 (non-blocking, below `block_on: high`). `threats_open` is **1**.

Scope: `git diff main..HEAD` is the four commits e479b0b (fix), 45d802c (docs), 1120efa (chore), cf9c8f2 (style): 8 files, 117 added and 9 removed lines. `main` is 0ba2051, the HEAD of the first audit, so the range is exactly the change since that audit. Parameters are unchanged (ASVS L2, `block_on: high`); T-09-12 was verified at L3 (data flow plus adversarial probes, including a live socket). No `SUMMARY.md` exists in the phase directory, so there are no executor threat flags to map. Implementation files were not modified and nothing was committed; probe scripts and baseline copies live in the session scratchpad only.

### Status changes

| Threat ID | Severity | First audit | Re-audit | Basis |
|-----------|----------|-------------|----------|-------|
| T-09-12 | medium | open (non-blocking) | **closed** | Fix present in code, no bypass found, regression tests fail on the old code and pass on the new (below) |
| T-09-26 | low | open (non-blocking) | **closed** | `.gitignore:100` rule `.claude/` (below) |
| T-09-20 | medium | open (non-blocking) | **open (non-blocking)**, documentation partial and partly inaccurate | Technical mitigation still absent (below) |
| T-09-05 | high | open (blocking) | **open (blocking)** | Unchanged; operator decision pending |
| T-09-27, T-09-29 | medium, low | open (non-blocking) | open (non-blocking), carried forward | No change in the range; GitHub-side settings were not re-queried |
| All other entries | | closed | closed | The `app.py` hunks are confined to the import and two helper functions; path decision, middleware order and cookie attributes are untouched, and the site, hook and denylist suites pass (390 tests) |

### T-09-12 - closed (medium, mitigate)

Mitigation present, at the correct boundary (the parser, before any decision is taken):

- `sessions.py:93-102` `_parse_expiry`: ASCII-only digit test, then `int()` inside `try ... except ValueError`; `_parse_token` (`:79-90`) returns `None` for any malformed expiry, which `session_email` turns into "no session".
- `app.py:903-915` `_parse_basic_auth`: catches `ValueError`, the common base of `binascii.Error`, `UnicodeDecodeError` and the error `b64decode` raises for non-ASCII text; the now unused `binascii` import was removed (cf9c8f2), so no narrower handler remains.
- `app.py:874-900` `_read_bounded_body` with `_declares_more_than`: a malformed `Content-Length` is read as undeclared; the streamed byte count (`:883-886`) still enforces the bound.
- `quiz.py:202-210` `_parsed_question_index`: same ASCII test plus guarded `int()`.
- Every parser failure resolves to `None` or `False`, which `identify()` (`app.py:354-371`) and `admit_authorized_person` (`:557-566`) treat as "no credential": denial. No path treats a parse failure as authorization.

Verification, all against the corrected code:

1. **Raw-ASGI probes, in process (105 probes plus 2 informational), HEAD against the main baseline.** Baseline: 11 probes raised an unhandled `ValueError` (non-ASCII and raw 0xff Basic headers, a 64 KiB non-ASCII Basic header, raw 0xb2 and 5000-digit and 4301-digit session expiries, a 5000-digit `Content-Length` on login and quiz, hostile quiz field names). HEAD: 0 exceptions, 0 responses with status 500 or above. Of 22 Authorization header variants, 19 returned 401 (no scheme, wrong case, wrong scheme, non-ASCII and raw 0xff bytes, bad padding, invalid UTF-8, surrogate payload, no colon, empty e-mail or password, NUL in the e-mail, a 64 KiB value, a wrong password); the other 3 returned 200 because they carry the correct password in a lenient spelling (A-11). All 28 malformed session cookies returned 401 (superscript, Arabic-Indic and full-width digits, 4300, 4301 and 5000 digit expiries, negative, signed, underscored, spaced and empty expiries, wrong, empty, extended and upper-case signatures, an expired genuine signature, undecodable or unknown e-mail part, e-mail swapped to another person's, raw 0xff and 0xb2 bytes, a 60 KiB value, value-less and empty-valued names, stray semicolons).
2. **No bypass.** The 200 responses at HEAD are the positive controls, the in-scope restricted reader, the three lenient valid-credential spellings, and genuine tokens in quoted or duplicate-name cookie forms; none came from invalid credential material. A malformed cookie combined with wrong Basic credentials returned 401; combined with correct credentials it returned 200 only through the full PBKDF2 check. A restricted reader remains confined to the prefix through both doors (403 out of scope). A token for one person with another person's e-mail returned 401 (the signature is keyed per person).
3. **Content-Length.** With 11 malformed spellings (superscript, 5000 digits, letters, `-1`, empty, `5, 5`, `0x10`, `1e3`, `+5`, Arabic-Indic digit, padded) and with the header absent, a small login body was processed normally (403, the CSRF refusal, since the probe carries no token), and an oversize login body (4096-byte bound) returned 413 both as one chunk and as several chunks. On the authenticated quiz endpoint (16384-byte bound) an oversize body returned 413 with the three spellings tried (superscript, 5000 digits, letters). The malformed header therefore cannot be used to skip the bound. A correctly declared oversize length still returns 413.
4. **Live socket.** A real uvicorn server on loopback (throwaway key and credentials, stopped afterwards; this closes the "no dynamic test against a real uvicorn socket" gap of the first audit for this subset; a TLS proxy was still not tested). Twenty requests with raw header bytes: the baseline answers 500, with tracebacks in the server log, for the raw UTF-8 and raw 0xff Basic headers, the raw 0xb2 expiry and the 5000-digit expiry; HEAD answers 401 for all of them and logs no traceback. Uvicorn itself answers 400 for four of the five malformed `Content-Length` spellings sent (the fifth, `5, 5`, reached the application and got the 403 CSRF refusal), so the application check is defence in depth. Over-bound login bodies return 413; liveness controls pass before and after.
5. **Regression tests.** `TestMalformedCredentialsNeverRaise` and the quiz parser test fail on the baseline (4 failed) and pass at HEAD. `tests/test_site_*.py`, `test_git_hooks.py` and `test_denylist_scan.py`: 390 passed; `ruff check`, `ruff format --check` and `mypy` on the site package: clean.

Residual points, none blocking (advisories A-11 and A-12 below): the regression tests encode the superscript and Arabic-Indic cases as UTF-8, which the ASGI layer decodes as two latin-1 characters, so those parameters pass on the old code too and only the 5000-digit and non-ASCII Basic cases discriminate; the fix itself was confirmed with raw bytes. Behaviours that look like bypasses but are not are listed in A-11.

### T-09-26 - closed (low, mitigate)

`.gitignore:100` now carries `.claude/` (commit 1120efa). `git check-ignore -v` resolves `.claude/settings.local.json`, `.claude/settings.json` and `.claude/anything/x` to that repository rule, so the protection no longer depends on the operator's global ignore file. `git ls-files .claude` is empty, and the range adds no `.claude` path. The sibling `.serena/` rule (`.gitignore:96`) was already repository-local.

### T-09-20 - remains open (non-blocking, medium)

The range does not touch `Dockerfile.site`: line 20 is still `FROM python:3.12-slim` (moving tag) and line 24 still installs five top-level packages by version through `pip`, with no digest, no hash verification and no link to `uv.lock`. The added section (`docs/operations.md:216-218`) documents the missing base-image digest accurately and gives an interim procedure, but:

- The sentence "its Python dependencies are pinned by version" is true only of the five top-level packages. The transitive set (for example starlette 1.3.1, anyio 4.14.2, h11 0.16.0, click 8.4.2, pydantic-core 2.46.4 in `uv.lock`) is resolved by `pip` at build time and is neither pinned nor hash-verified, so the sentence overstates and the second half of the finding is undocumented.
- The stated reason ("this repository change was made without network access") describes the circumstance of one change, not a property of the repository, and will age in a durable document.
- The section records a limitation, not an acceptance. No accepted-risk entry names an acceptor, so the `accept` route does not close the entry either.

To close: pin the base by digest and install from `uv export --frozen --hash` with `--require-hashes`, or reword the section to say that only the top-level packages are pinned and that the base image and transitive dependencies are neither pinned nor hash-verified, and record the operator's acceptance in the Accepted Risks Log.

### README and documentation claims

| Claim | Location | Result |
|-------|----------|--------|
| The example gateway configuration sets `default_adapter: codex` | `README.md:168` | Verified: `config/llm-gateway.example.yaml:22`; built-in default is also codex (`llm/config.py:71`) |
| `LECTURE_STUDY_LLM_ADAPTER=llama-cpp` selects the local adapter per run | `README.md:168` | Verified: `llm/config.py:39`; replaces the default and every stage entry (ADR 0046, decision 2; `docs/operations.md:61`) |
| The remote adapter refuses lectures not recorded as eligible | `README.md:168` | Consistent with ADR 0046 (lines 279-291) and ADR 0045; the range does not touch `src/lecture_study/llm`, so the live refusal checked for T-09-17 stands; not re-run |
| The local adapter "sends nothing off the machine" | `README.md:168` | Consistent with ADR 0046 "by construction"; omits the qualifier that README line 47 and `docs/operations.md:61` carry (A-10) |
| Licence table rows | `README.md:316-330` | Verified against installed metadata and `uv.lock`: phonemizer-fork 3.3.2 GPL-3.0-or-later classifier; espeakng-loader 0.2.4 declares no licence; mlx-audio 0.5.4 MIT; misaki 0.9.4 Apache classifier; Qwen2.5-14B Apache-2.0 at `scripts/01_provision_engines.sh:31` and ADR 0046; "not recorded" rows for Kokoro, Whisper and llama.cpp hold; versions match `uv.lock` and the installed metadata |
| GPL components are not vendored | `README.md:330` | Verified: `git ls-files` lists no espeak, phonemizer, shared-library or model file |
| `docs/operations.md` limitation text | `docs/operations.md:216-218` | Partly inaccurate; see T-09-20 |

`scripts/check_documentation.py` reports 2 documents, 21 command lines, 0 findings (it does not check root-level files such as `Dockerfile.site`, as the README states). T-09-28 stays closed with A-10 recorded.

### Leak scans over the corrected range

| Check | Result |
|-------|--------|
| `gitleaks git --log-opts="main..HEAD"` (8.30.1) | 4 commits, no leaks, exit 0 |
| `gitleaks git --log-opts="--all"` | 152 commits (6.5 MB), no leaks, exit 0 |
| `gitleaks dir` on `git archive HEAD`; on the two untracked phase documents | no leaks, exit 0 on both |
| `denylist_scan.py`, CI-equivalent stream (all refs, separated merge diffs, author, committer and message fields, ref names; 7.49 MB) | exit 0 |
| `denylist_scan.py`, range-only stream (13 KB); range file names; all tracked file names; `09-REVIEW.md` plus `09-SECURITY.md` | exit 0 on each |
| Scanner fail-closed check (missing list) | exit 2 |
| Pattern sweep of the 117 added lines (e-mail shapes, URLs, home paths, key and token shapes, long hex and base64, IPv4) | one `example.com` placeholder; nothing else |

The private denylist was not read (position-only output; file present, mode 600).

### T-09-05 - remains open (blocking, high)

All 152 commits on all refs (304 author and committer fields) carry the one personal address, including the 8 fields of the 4 new commits; no `noreply`-style identity exists on any ref. No acceptance is recorded in this file, the phase plan or an ADR. Required action 2 of the first audit is met: neither `09-REVIEW.md` nor this file contains the address on any line, the tracked tree has no file containing it, and no added line of the range does. The address is not reproduced here. The decision (accept the published identity as a recorded risk, or rewrite history to a no-reply address and re-run both scanners and the hook tests on the new object ids) belongs to the operator; the auditor does not record it. The 4 additional commits add 4 objects to the scope of any rewrite.

### New advisories (unregistered, non-blocking, not counted in `threats_open`)

| ID | Severity | Observation | Suggested action |
|----|----------|-------------|------------------|
| A-10 | low | `README.md:168` states that the local adapter "is the choice that sends nothing off the machine" without the qualifier "by construction, no OS sandbox" used at `README.md:47`, `docs/operations.md:61` and ADR 0046. | Add the qualifier or point to ADR 0046's wording. |
| A-11 | info | Pre-existing and unchanged by the range (identical at the baseline): credential leniencies that all require the correct password, so none is a bypass. A Basic value with extra non-alphabet characters decodes to the same pair (non-strict `b64decode`); a trailing NUL byte on the password verifies as the plain password (HMAC zero-pads short keys, so PBKDF2 treats them as equal); surrounding whitespace on the e-mail is stripped by `normalize_email`; a genuine token with leading zeros in the expiry is accepted (the signature covers the integer, not the text). Non-ASCII digits cannot arrive in a header value other than the latin-1 superscripts, which `int()` rejects; that was the source of the original crash. | None required; reject NUL in `verify_credentials` and require canonical expiry text if strictness is wanted. |
| A-12 | low | Test coverage gap: the superscript and Arabic-Indic parameters of `TestMalformedCredentialsNeverRaise` pass on the pre-fix code because the client encodes them as UTF-8 (two latin-1 characters on arrival). `sessions._parse_expiry` has no direct unit test of the superscript vector. | Add a unit test calling `_parse_expiry("²")` and an ASGI case that sends the raw byte `b"\xb2"`. |

### Required actions to reach SECURED (updated)

1. T-09-05 (blocking): the operator records an explicit decision (acceptance with acceptor and date, or history rewrite followed by a full re-run of gitleaks, the denylist scan over all refs and the hook tests).
2. Recommended before the switch (non-blocking): resolve T-09-20 (pin or reword and record acceptance), decide on `.planning/` publication (T-09-27), apply the repository settings of T-09-29, optional A-10 to A-12.
3. Not re-verified here: CI on the 63 commits ahead of `origin/main` (59 at the first audit; push `main` only and confirm the three jobs green); the `DENYLIST` secret value; GitHub-side settings.
4. Any further corrective change, including documentation edits that alter a security claim, requires a new run of this audit over the then-final diff (rule R0.4).
