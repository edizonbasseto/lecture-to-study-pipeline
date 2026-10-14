---
last_mapped_commit: 98206c18263d7561530c2e9ff7700e155c18d661
last_mapped_at: 2026-10-14
---
<!-- refreshed: 2026-10-14 -->

# Testing Patterns

**Analysis Date:** 2026-10-14

## Test Framework

**Runner:**
- pytest 9.x (`pytest>=9.1.1` in the `dev` group of `pyproject.toml`)
- Config: `[tool.pytest.ini_options]` in `pyproject.toml`. `testpaths = ["tests"]` restricts discovery so vendored engine checkouts under `engines/` are not collected. Registered marker: `real_certification` (run with production certification guards enabled).
- Async plugin: `pytest-asyncio>=1.4.0`.
- Coverage: `pytest-cov>=7.1.0`.

**Assertion Library:**
- Plain `assert` statements (pytest rewriting). `pytest.raises` is the exception assertion (62 test files/usages).

**Run Commands:**

```bash
make test                          # uv run pytest --cov (all tests with coverage)
uv run pytest -q -rs              # Quiet run with skip reasons, as in CI
uv run pytest tests/test_site_throttle.py   # Single module
uv run pytest -k "throttle"       # Filter by name
make lint                          # ruff check, ruff format --check, mypy src
```

CI (`.github/workflows/ci.yml`) runs on `macos-15`, because OCR uses Apple Vision (pyobjc) and TTS uses MLX. The test job installs `ffmpeg`, `typst`, `poppler`, and `gitleaks` via Homebrew and runs `uv sync --group dev --locked` before `uv run pytest -q -rs`.

## Test File Organization

**Location:**
- All tests live in the top-level `tests/` directory, separate from `src/`. There are no co-located tests.

**Naming:**
- Files: `tests/test_<module_or_phase>.py`, matching `src/lecture_study/<package>/<module>.py` where possible (e.g., `tests/test_site_quiz.py`, `tests/test_generate_codex_podcast.py`).
- Test functions: `test_` followed by a sentence describing behavior, often in plain English prose: `test_the_wait_grows_with_each_further_failure`, `test_one_address_never_delays_another` (`tests/test_site_throttle.py`). Parameterized tests use the subject and the property: `test_generate_deck_neutralizes_every_formula_trigger_character` (`tests/test_flashcards.py`).
- Test classes group behaviors of one unit: `class TestAttemptDelay:` (136 `class Test...` definitions across `tests/`).

**Structure:**

```
tests/
├── conftest.py                 # Shared fixtures (legacy_certification_bypass, etc.)
├── test_<module>.py            # One file per module or pipeline phase
└── ...                         # ~1,700 test functions total
```

## Test Structure

**Suite Organization:**

```python
"""Module docstring: states the requirement ID or behavior under test
(e.g., "Abuse-limit tests (SITE-04)")."""

import pytest

from lecture_study.site.throttle import AttemptDelay, MAX_DELAY_SECONDS

class TestAttemptDelay:
    def test_the_first_attempts_wait_for_nothing(self):
        delay = AttemptDelay(free_attempts=3)

        assert delay.seconds_before("aluna@example.com", now=0.0) == 0.0
```

**Patterns:**
- Setup: construct the unit directly with explicit parameters. Dependencies that must not run for real are replaced with `monkeypatch`.
- Teardown: rely on `tmp_path` and `monkeypatch`, which are reverted automatically. No manual teardown is used.
- Assertions: one behavior per test; state the expected value explicitly. Name the invariant in the test name rather than in a message argument.
- Time is injected, not read: pass `now=` explicitly to time-sensitive APIs (see `src/lecture_study/site/throttle.py`).

**Autouse fixtures:**
- Four autouse fixtures exist. Example: `brief_delays` in `tests/test_site_app.py` shortens throttle waits so the suite does not sleep. Prefer an explicit fixture when the setup is not needed by every test in the file.

## Fixtures

**Shared fixtures (`tests/conftest.py`):**
- `legacy_certification_bypass(monkeypatch)` patches the certification gate for publishing, flashcards, and TTS so that pipeline tests can run without a full quality certificate. Use it only for tests of the downstream behavior, not for the certification logic itself, which is covered by `tests/test_certification.py` and `tests/test_quality_trust.py`.

**Module fixtures:**

```python
@pytest.fixture
def site_directory(tmp_path: Path) -> Path:
    site_dir = tmp_path / "site"
    site_dir.mkdir()
    (site_dir / "index.html").write_text("<h1>Biblioteca</h1>", encoding="utf-8")
    return site_dir
```

(`tests/test_site_app.py`). Build filesystem state under `tmp_path`; never write into the repository's `workspace/` or `engines/`.

**Test data:**
- Lecture identifiers use the form `aula-001` and person-like fixtures use placeholder names (`aluna@example.com`). Keep test data synthetic; do not copy real lecture content, credentials, or PII into fixtures.
- Build domain objects inline with small helper functions prefixed with `_make_` (e.g., `_make_fact` in `tests/test_flashcards.py`) rather than shared factories.
- Trust/key material in tests is generated at runtime from helpers (e.g., `_trust()` in `tests/test_quality_trust.py`). Do not commit key-shaped literals, even for tests.

**Location:**
- Fixtures are defined in the test module that uses them, and in `tests/conftest.py` only when shared across modules.

## Mocking

**Framework:** `pytest`'s `monkeypatch` (34 test files use it). `unittest.mock` is used in 6 test files; prefer `monkeypatch` for new tests.

**Patterns:**

```python
def test_generate_deck_neutralizes_every_formula_trigger_character(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, trigger_char: str
):
    _setup_generator_dirs(monkeypatch, tmp_path)
    ...
```

```python
monkeypatch.setattr(
    publishing_pipeline_module,
    "require_publication_ready",
    verify_publishing,
)
```

**What to Mock:**
- External processes and engines: `ffmpeg`, `mlx_audio`, `whisper.cpp`, Codex CLI invocations. Replace the subprocess boundary, not the parser or validator behind it.
- Module-level gates that require certified artifacts (certification, publication readiness). Use `legacy_certification_bypass` for downstream tests.
- Filesystem roots and output directories: monkeypatch the module constants (e.g., `FLASHCARDS_OUTPUT_DIR`) to `tmp_path`.
- Time: pass `now=` or monkeypatch the clock rather than calling `time.sleep`. `time.sleep` appears in 4 test files; do not add new real sleeps.

**What NOT to Mock:**
- Pure domain logic, Pydantic validation, and the signing/verification code in `src/lecture_study/quality/trust.py`. These are tested directly with real HMAC keys generated in the test.
- Filesystem I/O within `tmp_path`; use real files.
- SQLite-backed stores (`src/lecture_study/quality/expert_corpus_sqlite.py`, `calibration_promotion_sqlite.py`); tests create real databases in `tmp_path`, including corruption and truncation cases (`tests/test_calibration_promotion_sqlite.py`).

## Async Testing

Async tests use the `pytest.mark.asyncio` marker:

```python
@pytest.mark.asyncio
async def test_succeeds_when_the_outcome_reports_no_error(...) -> None:
    ...
```

(`tests/test_cli.py`, 8 files total). Async primitives such as `asyncio.Semaphore` and queue-based limiters are tested by running real coroutines with small concurrency limits (see `src/lecture_study/site/throttle.py` for `PasswordCheckLimiter`).

## Fixtures and Factories

Use `@pytest.mark.parametrize` for input matrices (39 files). Name the ids through the parameter values, which are short literals:

```python
@pytest.mark.parametrize("change", ["payload", "signature", "key"])
def test_verification_rejects_changed_identity(change: str) -> None:
    ...
```

## Coverage

**Requirements:** None enforced in configuration. `make test` produces a coverage report via `pytest --cov`; no `fail_under` threshold is set.

**View Coverage:**

```bash
uv run pytest --cov
uv run pytest --cov --cov-report=term-missing
```

## Test Types

**Unit Tests:**
- The majority of the suite (~1,700 test functions). Each module's behavior is tested in isolation with explicit inputs and `tmp_path` storage.

**Integration Tests:**
- Pipeline tests exercise several modules together against the filesystem layout under `tmp_path`, e.g., `tests/test_site_app.py` (FastAPI routes via `httpx`), `tests/test_git_hooks.py` (runs the repository's hooks end to end), and `tests/test_cli.py`.

**Security and Regression Tests:**
- Leak gates and denylist behavior: `tests/test_denylist_scan.py`, `tests/test_student_facing_text.py`, `tests/test_exam_echo_scan.py`.
- Trust and certification tampering: `tests/test_quality_trust.py`, `tests/test_delivery_manifest.py` (parametrized over `"missing"`/`"extra"` filesystem changes).
- Each security fix should add a regression test named after the behavior it protects (see `tests/test_site_throttle.py`).

**E2E Tests:**
- Not used as a separate suite. Real media toolchains are not part of the default run; the `real_certification` marker gates tests that require production certification guards.

## Common Patterns

**Error Testing:**

```python
with pytest.raises(QualityTrustError):
    trust.require_valid(payload=payload, signature=signature, key_id=key_id)
```

Match on the exception type; add `match=` when the message is part of the contract.

**Asserting that a side effect did not happen:**
- Assert on the filesystem or the returned outcome (e.g., `error is None`), not on mock call counts, following the style of `tests/test_cli.py`.

**Guarded-behavior tests:**
- Name the test after the requirement it guards and state the reason in the module or test docstring, as in `tests/test_site_throttle.py`: "An unbounded wait would be a lockout by another name."

## Rules for New Tests

- Place the file at `tests/test_<module>.py`, use plain `assert`, and group related behaviors in a `Test<Unit>` class.
- Use `tmp_path` and `monkeypatch`; never write to `workspace/`, `engines/`, or the user's home directory.
- Keep fixtures and sample data synthetic. Do not use real keys, tokens, or lecture content.
- Pass `now=` or equivalent to time-dependent code; do not sleep.
- Run `uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q -rs` before committing, matching CI.

---

*Testing analysis: 2026-10-14*
