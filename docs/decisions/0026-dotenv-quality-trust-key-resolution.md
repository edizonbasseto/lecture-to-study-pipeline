# 0026. Dotenv resolution for the quality trust key

## Status

Accepted; amended by ADR 0044.

## Amendment: working-directory `.env` only, single key (see ADR 0044)

The `load_dotenv(find_dotenv(usecwd=True))` call described below is replaced.
When the process environment does not hold the key,
`HmacQualityTrust.from_environment()` now reads only
`LECTURE_STUDY_QUALITY_HMAC_KEY_B64` from the current working directory's own
`.env` file through `dotenv_values`:

- it never searches parent directories for a `.env` file;
- it reads that single key and ignores every other entry in the file;
- it never writes to `os.environ`.

Reason: `find_dotenv` walks up the directory tree, so a run started in a
directory without its own `.env` would accept one from a parent or otherwise
untrusted directory -- possibly belonging to an unrelated project -- as the
source of the key that signs and verifies quality certificates. That file must
not be able to supply the trust root. Loading the whole file into
`os.environ` also handed unrelated values to every later reader of the process
environment. The resolution order is otherwise unchanged: process environment
first, then the working directory's `.env`, then the fail-closed
`QualityTrustError`. An explicit `environ` mapping still consults no file.
References below to `load_dotenv`, `find_dotenv`, or loading the key into the
parent process's environment are historical.

## Context

Plan `01-13` halted at `HmacQualityTrust.from_environment()`, called with no
`environ` argument from both `PersonalStudyFlashcardGenerator._resolve_trust`
and `DerivativeQualityCertificateVerifier._resolve_trust`. That call reads
`LECTURE_STUDY_QUALITY_HMAC_KEY_B64` from `os.environ` alone. Phases 13, 14
and 15 of the personal-study route, and the flashcard-deck generator, all
depend on it and were blocked.

The key exists: the operator generated it and stored it in the project's
dotenv file at the project root. It is not reachable by the executing agent
through any interactive means. Every agent in this environment is blocked by
a secret-read guard from reading or sourcing the dotenv file directly, by
design. Exporting the variable in the launching shell does not help either,
because the tooling's shell initializes from the user profile rather than
inheriting its parent's exported variables. The credential exists on disk;
the process that needs it has no path to it other than reading the file
itself, in-process, the way `python-dotenv` does.

## Decision

`HmacQualityTrust.from_environment()` resolves the key in this order:

1. The process environment (`os.environ`), exactly as before.
2. The project's dotenv file, only when step 1 finds nothing and only when
   no explicit `environ` mapping was supplied to the method.
3. Otherwise, the existing typed, fail-closed `QualityTrustError`, unchanged.

This follows the single existing precedent for dotenv-sourced credentials in
the original codebase: an authentication-credentials module of the private
deployment (not included in this repository), whose module docstring
described dotenv as the deliberate single non-interactive credential path for
its operator UI, invoking
`load_dotenv(find_dotenv(usecwd=True))` against the already-declared
`python-dotenv>=1.2.2` dependency in `pyproject.toml`. `quality/trust.py` now
calls the identical function, not a second helper or a second search
strategy — one dotenv vocabulary in this repository.

`load_dotenv`'s own default behavior is to never override a value already
present in `os.environ`, which is exactly the process-environment-first
ordering this decision requires without any extra conditional in the trust
module beyond gating the call on `environ is None`. `find_dotenv` returning
an empty path when no dotenv file exists leaves the existing fail-closed
error as the outcome, with no change to that error's message or its
triggering condition.

Every existing validation and every existing typed failure survive
unchanged: the presence check and its message, the canonical-Base64 decode
and its message, and the 32-byte minimum enforced in `__post_init__`. No
code path generates, defaults, pads, or substitutes key material from any
source. An explicit `environ` mapping argument is honoured exactly as
before and triggers no dotenv lookup at all, so every existing caller and
every existing test that injects a mapping keeps its current contract
unmodified.

### The security control this decision leaves standing

The deliberate control in this repository is not a restriction on where the
parent process reads its key from. It is
`sanitized_child_process_environment()` in
`src/lecture_study/ops/process_environment.py`, which strips every
`LECTURE_STUDY_QUALITY_*` variable, and the common credential variables
beside them, by prefix, from the environment handed to any spawned child
process such as `llama.cpp`. `tests/test_process_environment.py` pins that
behavior with cases asserting the quality key is excluded from the
sanitized environment even when present in the source mapping.

This decision does not touch that control. `sanitized_child_process_environment()`
and `tests/test_process_environment.py` are unchanged — a git-diff gate
in plan `01-17`'s Task 1 and Task 3 fails the plan if either file moves.
Loading the key into the parent process's environment through dotenv has no
effect on what the sanitizer strips, because the sanitizer filters by prefix
at the moment a child environment is built, regardless of how the parent's
own environment was populated. This is proven, not merely asserted:
`tests/test_quality_trust.py::test_sanitized_child_environment_excludes_key_after_dotenv_resolution`
resolves the key from a dotenv file into a parent environment mapping and
then asserts that `sanitized_child_process_environment()` over that same
mapping still excludes `LECTURE_STUDY_QUALITY_HMAC_KEY_B64`. Plan `01-17`'s
Task 3 repeats the same proof empirically, in one process, against the real
production call sites.

### Distinguishing ADR 0023's registry-root prohibition

ADR 0023 forbids an environment fallback, but for a different artifact.
Its prohibition is scoped explicitly to the `VisualObservationReceiptRegistry`
and `SemanticInvocationRegistry` inward ports: "The caller must inject the
exact registry root. There is no production default, artifact-root
derivation, environment fallback, or implicit temporary registry," and the
parallel statement for the semantic invocation registry, that "the
production composition has no default path, environment fallback, temporary
root, service locator, or in-memory backend." Both registries are
persistent, append-only stores whose exact root must be the one explicitly
injected authority, because reconstructing or defaulting that root would let
a caller fabricate receipt or invocation history.

The quality HMAC key is not a registry root. It is runtime-only trust
material used to sign and verify certificates; ADR 0023 itself states, in
its publication-gate section, that "every publication, derivative, and
delivery certificate must be authenticated by runtime trust material that is
not stored in the workspace." The dotenv file sits at the project root,
outside `workspace/`, and stays untracked — so that requirement remains
satisfied exactly as before. This decision neither amends nor relaxes ADR
0023's registry-root prohibition; it resolves a different kind of artifact
that ADR 0023 does not govern.

## Alternatives Considered

1. **Export the variable in the launching shell.** Rejected: the tooling's
   shell initializes from the user profile rather than inheriting the
   parent shell's exported variables, so this does not reach the process
   that needs the key.
2. **A key file path passed by flag or explicit argument at each call
   site.** Rejected: this would introduce a second credential vocabulary
   beside the existing dotenv one, for no benefit over extending the
   single vocabulary the credentials module already established.
3. **A keychain lookup.** Rejected as the same interactive path the
   operator UI already removed in favor of the dotenv-only credential source
   the credentials module's own docstring records.

## Consequences

- **Positive:** the resolution that blocked plan `01-13`'s exam, podcast,
  audio, and flashcard-deck generation for lecture `10002` now succeeds,
  through the same dotenv vocabulary already established for credentials,
  with no new search strategy introduced.
- **Positive:** every existing validation, typed error, and the
  child-process sanitization control are all provably unchanged, not merely
  asserted unchanged.
- **Negative, accepted:** a secret is now read from a file on disk into the
  parent process's environment. The mitigations are: the dotenv file stays
  untracked (verified by a `git ls-files` gate); `.env.example` documents
  only the variable's name, never a value; the trust module carries no
  output or logging path (verified by a gate over its non-comment lines);
  and the unchanged child-process sanitizer, which is what actually governs
  what an external process can observe, regardless of how the parent's
  environment was populated.
- **Neutral:** the resolution order — process environment first, dotenv
  second, fail-closed otherwise — mirrors the credentials module's own
  contract exactly, so a future reader of either module recognizes the same
  pattern.

## Rationale

Following the exact precedent the credentials module already established,
rather than inventing a second dotenv-adjacent mechanism, keeps this
repository at one credential-resolution vocabulary instead of two. Gating
the dotenv lookup on `environ is None` keeps the change minimal and
behaviorally inert for every existing caller that already injects an
explicit mapping. Locking the sanitizer and its tests with a git-diff gate,
and proving the sanitized-after-dotenv-resolution property with a dedicated
test rather than an inline assertion, keeps the one property this decision
must not compromise verifiable independently of this document's own claims.
