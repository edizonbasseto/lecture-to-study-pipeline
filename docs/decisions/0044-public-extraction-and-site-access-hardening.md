# 0044. Public extraction of the pipeline and study-site access hardening

## Status

Accepted. Amends ADR 0016 (the `discover` command is removed), ADR 0031 (the
figure crop default is now no crop), and ADR 0039 (site access decisions and
hardening). Scopes ADR 0010 to local storage.

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

- **Local hooks** in `.githooks/`: a `pre-commit` hook rejects staged
  additions (content and file names) that match any term in a private
  denylist and then runs `gitleaks` on the staged changes; `commit-msg` and
  `pre-push` hooks apply the same denylist to commit messages and to the
  commits being pushed. The denylist is kept outside the repository (by
  default under the user's configuration directory, overridable through
  `LECTURE_STUDY_DENYLIST`), because committing it would publish the very
  terms it protects. A missing or unreadable denylist blocks the commit
  rather than skipping the check.
- **CI:** the `secret-scan` job checks out the full history and runs the
  `gitleaks` action on every push to `main` and every pull request.
- The hooks are local and bypassable; CI is the backstop for secrets, and the
  denylist check depends on each contributor installing the hooks
  (`git config core.hooksPath .githooks`).

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
- **Security headers** are set on every response.
- **Login CSRF:** the login form carries a CSRF token that `POST /entrar`
  verifies.
- **Cookie prefix:** the session cookie uses the `__Host-` prefix, so it is
  bound to the exact host, requires `Secure`, and cannot be set by a sibling
  domain or with a `Domain` attribute.
- **Authentication logging** records outcomes without personal data (no email
  address or password material in the logs).

### 5. Behavior changes made general

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
- **Negative, accepted:** the denylist check runs only where the hooks are
  installed and the private denylist is present; CI enforces only the secret
  scan.
- **Neutral:** historical ADR sections that mention `discover`, the
  `ingestion/` package, `.prompts/`, or `DIRETRIZES_ARQUITETURA.txt` are kept
  as provenance and annotated where they would otherwise read as current
  behavior.
