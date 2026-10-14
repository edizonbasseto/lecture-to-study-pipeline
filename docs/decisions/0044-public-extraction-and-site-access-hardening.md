# 0044. Public extraction of the pipeline and study-site access hardening

## Status

Accepted. Amends ADR 0016 (the `discover` command is removed), ADR 0031 (the
figure crop default is now no crop), ADR 0026 (dotenv resolution of the
quality trust key), and ADR 0039 (site access decisions and hardening). Scopes
ADR 0010 to local storage.

ADR numbers 0017 to 0019 are absent from this repository: those records
described portal acquisition, which is not part of this repository (section 1).

## Context

The pipeline was developed inside a private deployment that also acquired
lecture recordings from an authenticated source portal, held the credentials
for that portal, and hosted the study site. The processing stages -- extraction,
transcription, evidence packs, semantic ledgers, drafting, critic review,
assembly, study materials, exams, podcast, and the study site application --
do not depend on any of that. They read a lecture catalog, a queue, and media
files from `workspace/`.

Publishing the processing stages as a public repository raised three
questions: what stays out, how private terms are kept from leaking into
history, and which behavior encoded one program's particulars rather than a
general rule. A security review of the study site during the extraction also
found an access-control bypass that had to be fixed before publication.

## Decision

### 1. Scope of the public repository

- **Excluded:** portal discovery and acquisition, portal authentication and
  credential storage, and the hosting and deploy tooling of the private
  deployment. These are specific to one institution's systems and one host,
  carry credentials or infrastructure details by nature, and are not needed to
  run or evaluate the processing stages.
- **Entry point:** lectures enter through `scripts/register_local_lecture.py`,
  which registers an operator-owned video under a synthetic identifier and
  writes its `queue.json` row (ADR 0042, ADR 0043). The interactive `discover`
  command of `cli/main.py` is removed (ADR 0016 amendment).
- **Lecture catalog:** `config/targets.example.yaml` documents the catalog
  file the scripts read as `config/targets.local.yaml`. The `.local.yaml`
  files are git-ignored.
- **Source documents not included:** earlier ADRs cite the original phase
  instructions under `.prompts/` and an architecture guidelines file,
  `DIRETRIZES_ARQUITETURA.txt`. Neither is included in this repository. Those
  citations are kept as historical provenance of each decision; the ADRs
  themselves record the requirements that were taken from them.

### 2. Synthetic identifiers

Every lecture identifier in code, tests, fixtures, and documentation is
synthetic (10001 and above for catalog-style examples, plus the reserved local
ranges of ADR 0042). No real course, discipline, activity, or lecture
identifier appears in the repository.

### 3. Leak gate

Three layers keep private terms and secrets out of history:

- **Local hooks** in `.githooks/`, installed with `make hooks`. The target
  writes thin wrappers into the repository's hooks directory (resolved with
  `git rev-parse --git-path hooks`, so linked worktrees are covered) that
  execute the versioned hooks; `core.hooksPath` is not used, and the
  installed wrappers never go stale when a versioned hook changes. Four hooks
  are installed:
  - `pre-commit` rejects staged additions (content and file names) that match
    any term in a private denylist and then runs `gitleaks` on the staged
    changes. Binary content is scanned as text, and external diff and
    textconv drivers are bypassed so no local configuration can hide content.
  - `pre-merge-commit` applies the same staged-content gate to merge commits,
    which skip `pre-commit`.
  - `commit-msg` applies the denylist to the commit message.
  - `pre-push` scans every commit about to be pushed, whatever hooks did or
    did not run when those commits were made: content including merge diffs,
    ref names, tag names and annotated tag messages, author and committer
    identities, and commit messages, against the denylist and `gitleaks`.

  Every git command in the hooks and in CI that prints file paths (`diff`,
  `log -p`) sets `core.quotePath=false`, so non-ASCII file names reach the
  scanner as written rather than as octal escapes that would hide a term.
  The hook scripts are exercised end to end by `tests/test_git_hooks.py`.
- **Denylist scanner** (`scripts/denylist_scan.py`), shared by the hooks and
  CI. The denylist is kept outside the repository (by default under the
  user's configuration directory, overridable through
  `LECTURE_STUDY_DENYLIST`), because committing it would publish the very
  terms it protects. Terms and text are folded before comparison (NFKD
  decomposition, case folding, accents and invisible format characters
  stripped), and the text is compared both as written and percent-decoded
  and HTML-unescaped. A single-word term matches as a substring of the
  folded text. A multi-word term also matches when its words are joined by
  up to eight separator characters, optionally continued on the next line
  after up to forty characters of indentation or diff marker, so hyphenated,
  URL-encoded, and wrapped spellings are caught while words of unrelated text
  are never fused into a term. Knowingly not caught: separators inside a
  single word, double-encoded text, look-alike letters from other scripts,
  and encodings such as base64 -- the gate targets accidental leaks, not a
  deliberate adversary. A match is reported by the term's position among the
  denylist terms, never by the term, so a failure never echoes a protected
  term into a terminal or a CI log. A missing, unreadable, or empty denylist
  fails the scan rather than skipping it.
- **CI** (`.github/workflows/ci.yml`), on every push to `main` and every pull
  request:
  - `secret-scan` checks out the full history and runs the `gitleaks` action.
  - `denylist-scan` writes the `DENYLIST` repository secret to a temporary
    file and runs the scanner over the full history of every ref (merge diffs,
    author and committer identities, commit messages) plus every ref name,
    tag name, tagger, and annotated tag message. When the secret is absent -- including on pull requests from
    forks, where repository secrets are not exposed -- the job fails closed
    instead of passing vacuously.
  - `test` runs `ruff` (lint and format check), `mypy`, and `pytest` on macOS
    with `ffmpeg`, `typst`, and `poppler` installed, since OCR and TTS depend
    on macOS-only frameworks.
  - Every third-party action is pinned by commit SHA, the workflow token is
    read-only, and every checkout sets `persist-credentials: false`.
- The hooks are local and bypassable; CI is the backstop for both secrets and
  denylisted terms.

### 4. Study-site access hardening

- **Access is decided on the served path.** `request.url` is rebuilt by
  re-parsing the decoded scope path, so an encoded `?` or `#` truncated
  `request.url.path` while the static-file handler still resolved the full
  scope path; combined with dot segments, the truncated prefix could satisfy
  a permitted-path check while the file served lay elsewhere. The access
  middleware now decides on the decoded scope path -- the path the router and
  static files actually serve -- and refuses with 400 any path containing `?`,
  `#`, a backslash, NUL, or a `.` or `..` segment, since another layer could
  interpret such a path differently.
- **Unauthenticated exemptions match method and path.** The few routes
  reachable without credentials (the health check, the login form, sign-out)
  are exempted only for the exact method and path each one serves, so an
  exemption cannot be reused by another method on the same path.
- **Security headers on every response.** `SecurityHeadersMiddleware` sets
  `Content-Security-Policy`, `X-Content-Type-Options`, `Referrer-Policy`,
  `Strict-Transport-Security`, and `X-Frame-Options`, overriding any value an
  inner layer chose. It is the outermost wrapper of the application, so early
  refusals, static files, and unhandled-error 500 responses all carry the
  same headers.
- **Hash-based Content-Security-Policy.** Inline scripts and styles are
  admitted only by their SHA-256 hash, computed from the same constants that
  render them; there is no `'unsafe-inline'`. The policy also sets
  `base-uri 'none'`, `form-action 'self'`, and `frame-ancestors 'none'`.
  Changing an inline script or style changes the policy with it, so pages
  generated before the change must be regenerated, or the browser blocks
  their now-unlisted inline code.
- **Session cookie prefix:** the session cookie is renamed to
  `__Host-pos_session`. The `__Host-` prefix makes a browser accept it only
  when it is `Secure`, has `Path=/`, and carries no `Domain`, so neither a
  plain-HTTP response nor a sibling subdomain can plant or shadow a session.
  The rename signs every existing session out once.
- **Login CSRF:** stateless signed double submit. The form page sets a random
  nonce in a `__Host-` cookie (`HttpOnly`, `Secure`, `SameSite=Strict`) and
  embeds an HMAC of that nonce, keyed with the server's signing secret, as a
  hidden field. `POST /entrar` accepts a submission only when the field is the
  HMAC of the nonce cookie sent with it. An attacker can neither plant the
  cookie nor compute the field for a nonce of their own choosing.
- **Pseudonymized authentication logging.** Authentication log lines carry
  outcomes and a pseudonymized account reference, never an email address or
  password material. The reference is a truncated HMAC of the address keyed
  with the signing secret, so it cannot be recomputed from a guessed address
  without that secret, stays stable for one person to keep the audit trail,
  and changes whenever the secret is rotated. It remains pseudonymous
  personal data, not anonymous data: whoever holds the signing secret can
  link a reference back to a known address.
- **Non-root container.** `Dockerfile.site` runs the application as an
  unprivileged user with the fixed uid and gid 10001, so host volumes can be
  granted to it by number. The mounted workspace volumes, the credentials
  file, and the signing secret must be readable by that uid, and writable by
  it wherever the application writes (the `generate` mode writes to the
  workspace volumes).

### 5. Quality trust key resolution

`HmacQualityTrust.from_environment()` (`src/lecture_study/quality/trust.py`)
no longer calls `load_dotenv(find_dotenv(usecwd=True))`. When the key is not
in the process environment, it reads only `LECTURE_STUDY_QUALITY_HMAC_KEY_B64`
from the current working directory's own `.env` file through
`dotenv_values`. It never searches parent directories and never writes to
`os.environ`; every other entry in the file is ignored. A `.env` in a parent
or otherwise untrusted directory must not be able to supply the key that
signs and verifies quality certificates, and loading a whole file into the
process environment would hand unrelated values to every later reader of
`os.environ` (ADR 0026 amendment).

### 6. Behavior changes made general

- **Figure crop:** `scripts/generate_personal_study_guides.py
  --figure-crop-rect` defaults to no crop. The previous default rectangle was
  measured on one program's `2560x1080` recordings with a webcam pillarbox
  and silently mis-crops any other layout (ADR 0031 amendment). An explicit
  `WIDTH:HEIGHT:X:Y` is still accepted.
- **Chapter plans:** chapter plans are no longer hard-coded per lecture.
  `--chapter-plan <yaml>` optionally supplies a list of entries with `title`,
  `first_unit`, and `last_unit`; the plan is validated (non-empty titles,
  `first_unit <= last_unit`, no overlapping ranges). Without a plan, units are
  grouped into numbered "Parte N" chapters.
- **Removed:** `scripts/audit_semantic_ledgers.py` (a one-off audit), the
  `src/lecture_study/ingestion/` package, and
  `ops.catalog_bridge.catalog_to_lectures` (dead code once discovery left the
  repository).

## Alternatives considered

1. **Publish the full private deployment with secrets redacted.** Rejected:
   acquisition and hosting code describe a specific institution's portal and
   a specific host; redaction would leave them non-functional for anyone
   else while still exposing their structure.
2. **Commit the denylist in hashed form.** Rejected: the terms are short and
   guessable, so hashes offer little protection, and substring matching of
   staged content needs the clear terms.
3. **Normalize ambiguous paths instead of refusing them.** Rejected: any
   normalization must match what every downstream layer does; refusing a path
   whose meaning depends on the reader removes the disagreement instead of
   trying to predict it.
4. **Keep the crop rectangle as the default and document it.** Rejected: a
   wrong default fails silently, while the absence of a crop is visible and is
   corrected with one flag.

## Consequences

- **Positive:** the public repository runs end to end on operator-owned
  videos without any external portal, credential, or host.
- **Positive:** a request can no longer be authorized for one path and served
  another.
- **Negative, accepted:** figures from recordings with on-screen chrome are
  embedded uncropped unless a rectangle is passed explicitly.
- **Negative, accepted:** guides compiled without a chapter plan use generic
  "Parte N" chapter titles.
- **Negative, accepted:** locally, the denylist check runs only where the
  hooks are installed and the private denylist is present. CI enforces both
  the secret scan and the denylist scan; because repository secrets are not
  exposed to pull requests from forks, the denylist job fails on such a pull
  request by design.
- **Negative, accepted:** deploying the `__Host-` session cookie signs every
  existing session out once.
- **Negative, accepted:** changing any inline script or style requires
  regenerating the site pages, because the Content-Security-Policy lists
  inline code by hash.
- **Negative, accepted:** rotating the signing secret changes every
  pseudonymized account reference, so log lines from before and after a
  rotation cannot be correlated by reference.
- **Deployment consideration:** the container runs as uid 10001; host
  volumes, the credentials file, and the signing secret must grant that uid
  the access the application needs.
- **Neutral:** historical ADR sections that mention `discover`, the
  `ingestion/` package, `.prompts/`, or `DIRETRIZES_ARQUITETURA.txt` are kept
  as provenance and annotated where they would otherwise read as current
  behavior.
