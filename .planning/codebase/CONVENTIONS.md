---
last_mapped_commit: 0256659c3fcdb7371d6f16cef4aabba42a0a53e9
last_mapped_at: 2026-10-14
---
<!-- refreshed: 2026-10-14 -->

# Coding Conventions

**Analysis Date:** 2026-10-14

## Naming Patterns

**Files:**
- Python modules use `snake_case.py`, grouped by pipeline phase in packages under `src/lecture_study/` (e.g., `src/lecture_study/extraction/ocr_worker.py`, `src/lecture_study/quality/delivery.py`).
- Each package exposes its public surface through `__init__.py` (e.g., `src/lecture_study/quality/__init__.py`).
- Tests mirror the module under test with a `test_` prefix in `tests/` (e.g., `tests/test_site_throttle.py` for `src/lecture_study/site/throttle.py`). Some test files cover a whole phase (`tests/test_extraction.py`).
- Scripts in `scripts/` use `snake_case.py`, with a numeric prefix only for shell provisioning (`scripts/01_provision_engines.sh`).
- Architecture decisions are recorded as `docs/decisions/NNNN-kebab-case-title.md`.

**Functions:**
- `snake_case` for all functions and methods.
- Module-private helpers are prefixed with a single underscore: `_load_cg_image` in `src/lecture_study/extraction/ocr_worker.py`, `_process_lecture` in `src/lecture_study/extraction/pipeline.py`.
- Async entry points are plain `async def` methods (67 async definitions in `src/`); concurrency is bounded with `asyncio.Semaphore` (e.g., `self._lecture_semaphore` in `src/lecture_study/extraction/pipeline.py`).
- Factory-style constructors are not used; classes are instantiated directly.

**Variables and constants:**
- Module-level constants are `UPPER_SNAKE_CASE`: `FREE_ATTEMPTS`, `MAX_DELAY_SECONDS` in `src/lecture_study/site/throttle.py`.
- Private module constants add a leading underscore: `_MINIMUM_KEY_BYTES` in `src/lecture_study/quality/trust.py`.
- Environment variable names are constants: `QUALITY_HMAC_KEY_ENV` in `src/lecture_study/quality/trust.py`.
- Module loggers are named `_logger` in pipeline modules (`src/lecture_study/extraction/pipeline.py`) and `_LOGGER` in `src/lecture_study/site/app.py`. Prefer `_logger` for new modules; the mixed casing is an existing inconsistency.

**Types and classes:**
- `PascalCase` classes. Exceptions end in `Error` (`QualityTrustError` in `src/lecture_study/quality/trust.py`, `LectureNotFoundError` in `src/lecture_study/domain/errors.py`).
- Pipeline outcomes use `PascalCase` with a domain suffix: `LectureExtractionOutcome`, `SectionTitleEcho`.
- String enumerations inherit from `StrEnum` (e.g., `ClauseReconciliationOutcome` in `src/lecture_study/domain/clause_reconciliation.py`).
- Typed value objects are frozen, slotted dataclasses: `@dataclass(frozen=True, slots=True)` (e.g., `PublicQuestion` in `src/lecture_study/site/quiz.py`, `HmacQualityTrust` in `src/lecture_study/quality/trust.py`).
- Data crossing module boundaries and validated inputs use Pydantic models with strict mode: `class LectureMetadata(BaseModel)` with `model_config = ConfigDict(strict=True)` in `src/lecture_study/domain/models.py`. About 200 `BaseModel` usages exist in `src/`.

## Code Style

**Formatting:**
- Formatter: `ruff format` (enforced by `make lint` and CI, see `Makefile` and `.github/workflows/ci.yml`). No `[tool.ruff]` section exists in `pyproject.toml`; ruff defaults apply (88-character line length, double quotes, trailing commas).
- Every module starts with a docstring, followed by `from __future__ import annotations` (56 of the source modules use it).

**Linting:**
- Linter: `ruff check .` with default rule set (no custom `select`/`ignore` configured). No `# noqa` suppressions exist in `src/` or `tests/`.
- Type checker: `mypy` in `strict = true` mode (`[tool.mypy]` in `pyproject.toml`), run as `uv run mypy src`.
- Known exceptions to strict typing: the Apple Vision/Quartz modules may import without stubs (`[[tool.mypy.overrides]]` in `pyproject.toml`). `src/lecture_study/extraction/ocr_worker.py` returns `Any` from pyobjc-bound helpers and documents why in a comment; do not extend `Any` beyond such boundary code.
- Type-ignore comments are not used in `src/`; avoid introducing them.

## Import Organization

**Order:**
1. `from __future__ import annotations`
2. Standard library (`import asyncio`, `from collections.abc import Mapping`, `from dataclasses import dataclass`)
3. Third-party (`from pydantic import BaseModel`, `from dotenv import dotenv_values`)
4. First-party (`from lecture_study.quality import ...`)

Within each group, `import x` and `from x import y` lines are sorted alphabetically (ruff's isort rules are not enabled by default, so this ordering is convention, not enforcement).

**Path Aliases:**
- None. Imports use absolute package paths rooted at `lecture_study` (76 source files import from `lecture_study`). Relative imports are not used in `src/`.

## Error Handling

**Domain exceptions:**
- Exceptions are defined next to the code that raises them, subclassing `RuntimeError` (e.g., `QualityTrustError` in `src/lecture_study/quality/trust.py`).
- Shared, cross-phase exceptions live in `src/lecture_study/domain/errors.py` (`LectureNotFoundError`, `GlobalIndexNotFoundError`, `ScriptNotFoundError`, `PipelineExecutionError`). New handoff-validation errors go there rather than into a new local class; the file's module docstring records this rule and its ADR origin (`docs/decisions/0008-document-assembly-and-domain-error-taxonomy.md`).
- Chain re-raised errors with `raise ... from error` (15 occurrences in `src/`).

**Isolation in batch pipelines:**
- A failing lecture must not stop the queue. Catch `Exception`, log the traceback with `_logger.exception(...)`, and return the failure as data:

```python
try:
    result = await self._extract_lecture(lecture)
except Exception as error:
    _logger.exception("extraction failed for lecture %s", lecture.id)
    return LectureExtractionOutcome(
        lecture_id=lecture.id, result=None, error=str(error)
    )
```

  (`src/lecture_study/extraction/pipeline.py`). Broad catches are limited to these isolation points (9 in `src/`); do not add new broad catches elsewhere.

**Security-sensitive failures:**
- Fail closed. Verification functions raise on invalid input rather than returning booleans (`require_valid` in `src/lecture_study/quality/trust.py`).

## Logging

**Framework:** standard library `logging`, one logger per module: `_logger = logging.getLogger(__name__)`.

**Patterns:**
- Use `%s`-style lazy arguments, not f-strings: `_logger.error("%s", error)` (`src/lecture_study/cli/main.py`).
- Use `_logger.exception(...)` inside `except` blocks to keep tracebacks.
- Do not log secrets, lecture content, or identifiers that map to people. Logging is limited to lecture IDs and error messages.
- Do not use `print()` in `src/`; output for the CLI goes through `rich` (`rich>=15.0.0` in `pyproject.toml`).

## Comments

**When to Comment:**
- Comments explain *why* a constraint exists, especially security or timing decisions. Examples: the rationale for per-address waits in `src/lecture_study/site/throttle.py`, and the note about resolving defaults at call time because argument defaults bind at import in `AttemptDelay.__init__`.
- Architecture decisions are referenced by ADR number inside docstrings (e.g., "ADR 0006" in `src/lecture_study/domain/errors.py`). Cite the ADR under `docs/decisions/` instead of restating its reasoning.

**Docstrings:**
- Triple-double-quoted docstrings on modules, public classes, and most functions (about 1,200 docstring delimiters in `src/`).
- Module docstrings are long and descriptive; they state responsibilities and invariants. Keep them in that style for new security or pipeline modules.
- Docstrings are not Google/NumPy formatted; use plain prose, with bullet lists only when enumerating trigger conditions.

## Function Design

**Size:** keep functions focused; helper functions (`_load_cg_image`, `_join_recognized_lines`) are extracted rather than nesting logic. Long files exist (e.g., `src/lecture_study/quality/expert_corpus.py`); do not grow them further when a new module fits the phase layout.

**Parameters:**
- Use keyword-only arguments for options that callers must name: `*, expected_output_bytes: Mapping[str, bytes]` in `tests/conftest.py` and `src/lecture_study/quality/delivery.py`.
- Optional constructor overrides default to `None` and resolve to module constants inside `__init__`, not in the signature (`src/lecture_study/site/throttle.py`).
- Use `Path` for filesystem paths, never bare strings.

**Return Values:**
- Return typed objects (dataclasses, Pydantic models) rather than dicts. `dict[str, ...]` appears only at JSON boundaries.
- Return `None` for "nothing to do" in validation gates; raise for failures.

**Literals:**
- Use `Literal[...]` for closed string sets (21 source files) and `StrEnum` for named outcomes.

## Module Design

**Exports:**
- Public names are re-exported from the package `__init__.py` (e.g., `from lecture_study.quality import DeliveryArtifactKind, write_delivery_outputs` as used in `tests/conftest.py`). Three modules declare `__all__`; new public modules should declare `__all__` too.

**Barrel Files:**
- Every package has an `__init__.py` that aggregates its public API. Import from the package (`lecture_study.quality`) in consumers, and from the submodule (`lecture_study.site.throttle`) inside tests when internals are needed.

**Dependency direction:**
- `domain/` holds models and errors with no pipeline dependencies (see ADR 0006 references). Phases import from `domain` and do not import sibling phase orchestrators, as noted in `src/lecture_study/publishing/pipeline.py`.

**Security-sensitive modules:**
- Trust, signing, and access code (`src/lecture_study/quality/trust.py`, `src/lecture_study/site/signing.py`, `src/lecture_study/site/sessions.py`) are constructed from runtime-only material. Do not add defaults, fixtures, or committed keys for them.

---

*Convention analysis: 2026-10-14*
