# 0041. Server-graded interactive mock exam

## Status

Accepted.

## Context

Phase 13's `MockExam` (`domain/models.py`, `exams/generator.py`) is compiled by
`ExamCompiler` (`exams/compiler.py`) into two artifacts per lecture, both under
`workspace/data/14_mock_exams/{lecture_id}/`: `exam.json` (the full structured
data, correct answers and explanations included) and `simulado.md` (a prose
view, questions followed by a collapsed "Gabarito comentado" answer key,
consumed by `site.generator._write_reading_pages` and
`_COLLAPSED_SECTION_BY_STEM`). The site pipeline copies `simulado.md`/
`simulado.pdf` into `workspace/site/` for the existing passive reading page and
never reads `exam.json` at all.

The operator asked for an interactive, on-site version of the simulado: pick
an answer per question, submit, see a score and the comentado gabarito --
without removing or altering the existing passive reading-mode page, which
stays exactly as it is. `exam.json` already carries everything an interactive
quiz needs; no new question/answer schema was required.

## Decisions

1. **Grading runs entirely server-side.** `site/quiz.py`'s `public_view()`
   strips `correct_answer`/`explanation` before a question ever reaches an
   HTTP response; `GET /simulado/{lecture_id}` can only ever render
   `PublicQuestion` values. Grading happens in `grade()`, called only from
   `POST /simulado/{lecture_id}`, and its `GradedQuestion`/`GradedExam`
   result -- the only shape carrying an answer key -- is built and rendered
   strictly after that POST, never before.
2. **`exam.json` is read directly from `workspace/data/14_mock_exams/`, never
   copied into the served site tree.** `site/quiz.py`'s `MOCK_EXAMS_DIR`
   follows the exact pattern `site/access.py`'s `CREDENTIALS_PATH` and
   `site/signing.py`'s `SIGNING_SECRET_PATH` already use: a path read from an
   environment variable with a workspace-relative default, read directly off
   disk by the route handler, never mounted as `StaticFiles`. `app.py` mounts
   exactly two trees (`SITE_OUTPUT_DIR` at `/` and `VIDEO_LIBRARY_DIR` at
   `/videos`); the mock-exams directory is a third, disjoint tree with no
   mount at all, so there is no static route `exam.json` -- or the
   `correct_answer` values inside it -- could ever be reached through.
3. **No signed token, no stored quiz-attempt state.** A submission is graded
   by re-reading the lecture's own `exam.json` from disk, using only the
   `lecture_id` URL path parameter (validated with the same `SafeIdentifier`
   `TypeAdapter` every other per-lecture disk reader in this codebase uses,
   e.g. `quality/delivery.py`'s `_LECTURE_ID_ADAPTER`) and scoring each
   submitted answer strictly by its position in that exam's own question
   list. A submission's form fields can assert `{index: letter}` pairs
   (`parse_submitted_answers`) but can never name a different lecture or a
   different exam; there is nothing for an HMAC-signed token to protect that
   a fresh, trusted-by-path-parameter disk read does not already guarantee.
4. **The GET/POST routes reuse the existing session/Basic-Auth middleware
   unchanged.** `/simulado/{lecture_id}` is not added to the middleware's
   unauthenticated path list (`/up`, `/entrar`, `/sair`), so both verbs
   require a valid session cookie or Basic credentials exactly like every
   other route, including the static file trees.
5. **Grading submissions are rate-limited per authenticated person per
   lecture**, reusing `site/throttle.py`'s `AttemptDelay`/`WaitingRoom` --
   the same growing-wait, never-a-lockout shape ADR 0039 built for password
   checking, in a new `_QuizGate` class. It omits `PasswordCheckLimiter`/
   `SuppressingLogGate`: there is no PBKDF2 cost to amortize per grading
   attempt, so the only two pieces still needed are the wait itself and the
   bound on how many waits may be outstanding at once. The account key is
   `f"{email}:{lecture_id}"`, not the bare authenticated email: keying by
   email alone (this ADR's first implementation) throttled ordinary study
   behavior -- retaking one lecture's quiz, or moving on to a different
   lecture's quiz shortly after -- because `AttemptDelay`'s free-attempts
   budget was shared across every lecture one person ever opens. Scoping the
   key per lecture keeps the growing wait aimed at what it exists to slow
   (resubmitting the same answer key over and over to fish out the correct
   answers) without also punishing normal cross-lecture practice within one
   sitting. This mirrors ADR 0039's own rationale for keying by identity
   rather than network address: every visitor sits behind the same TLS
   proxy address, so an IP-keyed limit would again be one shared bucket.
6. **The identity used to key the quiz throttle is read once, by the
   middleware, not re-derived per route.** `_AccessGate.authorizes` was
   refactored into `_AccessGate.identify`, returning the authorized email;
   the middleware now calls `identify` (not a separate bool check) and
   stores the result on `request.state.pos_email` before dispatching to any
   route. Re-running Basic Auth's PBKDF2 check a second time inside
   `submit_quiz` to learn "whose attempt is this" would double that check's
   cost for no additional assurance the middleware had not already provided.

## Rationale

- **Why server-side grading over shipping the answer key to the client (with
  a signed reveal, or with client-side scoring) or over reusing the existing
  passive markdown page as-is.** The operator explicitly required the
  correct answer to never reach the client before submission -- a
  client-side-scored quiz (JS holds the answer key at page-load time) or a
  "ship a signed encrypted key, decrypt client-side" design both put the
  answer key in the browser before or during the attempt, which is exactly
  the property this feature must not have. Keeping the passive markdown page
  unmodified was the operator's explicit second requirement, so this is an
  additional route rather than a replacement.
- **Why no HMAC-signed submission token (`signing.py`'s pattern), unlike
  session cookies.** A signed token exists in this codebase to let a
  stateless server trust a claim it cannot otherwise re-verify (a cookie
  claims "this person was authenticated a while ago"; the server cannot
  re-check that itself). A quiz submission makes no claim the server cannot
  independently verify in the same request: the `lecture_id` path parameter
  is already trusted the same way every other per-lecture route in this
  codebase trusts it, and grading always re-reads that lecture's own
  `exam.json` fresh rather than trusting anything about question-to-lecture
  association from the request body. Signing would protect against a threat
  (a forged lecture/question association) that does not exist here, since
  nothing about that association is ever taken from the client.
- **Why no persisted quiz-attempt history.** The operator explicitly scoped
  this out unless a stateless design proved unsafe; a stateless per-request
  grade is sufficient here for the reason above (no association trust gap to
  close with server-side state), so nothing was added.
- **Why the mock-exams directory is read directly rather than synced into
  `workspace/site/` by `site.generator`.** Syncing it into the served tree
  would put a file containing every `correct_answer` inside the same
  directory `StaticFiles` mounts at `/`, one misconfigured route or future
  refactor away from being served raw. Reading it from its own,
  never-mounted location -- exactly how `access.py`/`signing.py` already keep
  the credentials file and the signing secret out of the served tree -- means
  there is no static route to misconfigure in the first place.
- **Why the throttle key was changed from `email` to `email:lecture_id`
  during review.** `principal-code-reviewer`'s first pass on this feature
  found the email-only key shared one free-attempts budget across a
  person's entire study session, throttling legitimate repeated use;
  `security-reviewer`'s re-review confirmed the per-lecture key does not
  weaken the throttle against an attacker confined to one lecture, since the
  growth curve, free-attempts count and cap are unchanged -- only the key's
  second component was added.

## Consequences

- `workspace/data/14_mock_exams/{lecture_id}/exam.json` gains a second
  reader (`site.quiz.read_exam`, alongside `ExamCompiler` as writer), still
  never copied or mounted into `workspace/site/` -- an unencrypted plain
  file, covered by the same ADR-0010 at-rest risk acceptance as every other
  pipeline artifact, not a new exposure category.
- `_AccessGate.authorizes` is now `identify() is not None`; `identify`'s
  return value is relied on by a second consumer (`request.state.pos_email`)
  beyond the boolean the middleware used before. Existing callers of
  `authorizes` are unaffected; its behavior is unchanged.
- `AttemptDelay`'s account-key cardinality for the quiz gate grows with
  `email × lecture_id` rather than `email` alone, bounded by the same
  `MAX_TRACKED_ACCOUNTS` eviction the login throttle already uses; this is
  an authenticated-only surface (every request reaching `_QuizGate` has
  already passed the auth middleware), so this growth is not an
  unauthenticated amplification vector.
- **Update, 2026-09-18:** the gap above was closed the same day it was
  found -- the route existed with no reachable link to it, so the
  interactive quiz was unusable from the site. `site.generator`'s
  `_write_lecture` now checks `(mock_exams_dir / lecture_id /
  "exam.json").is_file()` at generation time and threads the result
  (`has_interactive_quiz`) into `render_lecture` down to the simulado
  card, which shows "Responder simulado" only when that file exists --
  never a link the running site could not satisfy. `mock_exams_dir`
  defaults to this ADR's own `site.quiz.MOCK_EXAMS_DIR`
  (`LECTURE_STUDY_SITE_MOCK_EXAMS`), the same directory `site.quiz.read_exam`
  reads at request time, so generation and serving agree on one source of
  truth. The private deploy script (not included in this repository) gained
  a matching rsync of
  `workspace/data/14_mock_exams/` to the server plus a read-only mount and
  the same env var into the `generate`, serving, and `rollback` container
  runs alike -- the `generate` step only ever calls `.is_file()` on the
  path, never opens or parses `exam.json`, so `correct_answer` still never
  enters `workspace/site/` or the generation output. The simulado card also
  dropped its passive "Ler" reading-page link entirely (operator
  instruction): only "Responder simulado" and "Baixar PDF" remain.
- The passive markdown reading page (`simulado.md` via
  `_write_reading_pages`/`_COLLAPSED_SECTION_BY_STEM`) is untouched by this
  change; both routes coexist.

## Alternatives Considered

- **Ship the answer key to the client, revealed via CSS/JS after
  submission.** Rejected outright: the operator's own requirement was that
  the correct answer must never reach the client before submission, which
  this design would violate the moment the page loaded.
- **HMAC-sign the submitted question set (mirroring `signing.py`), binding
  it to the claimed lecture.** Considered as the operator's own suggested
  fallback if a stateless design proved unsafe. Not needed: grading never
  trusts any client-supplied lecture/question association in the first
  place, so there is no forgeable claim left for a signature to protect.
- **Persist a quiz-attempt history (a new small database/table).** Rejected
  per the operator's explicit scope; the stateless design carries no safety
  gap that persistence would have closed.
- **Sync `exam.json` into `workspace/site/` next to `simulado.md`/
  `simulado.pdf`.** Rejected: would place a file containing every
  `correct_answer` inside the directory tree `StaticFiles` mounts at `/`,
  one misconfiguration away from being served raw -- exactly the exposure
  `access.py`/`signing.py` already avoid for the credentials file and the
  signing secret by keeping them outside the served tree.
- **Key the quiz throttle by network address, matching a first-glance read
  of "prevent brute-forcing."** Rejected for the same reason ADR 0039
  rejected it for password checking: every visitor sits behind the same TLS
  proxy address, so an IP-keyed limit is one shared bucket that would
  throttle every authenticated person together.
- **Key the quiz throttle by email alone (this ADR's first implementation).**
  Rejected on `principal-code-reviewer` review: shared one free-attempts
  budget across a person's entire study session instead of per lecture,
  throttling ordinary repeated practice. See Rationale.
