# Phase 6: Pluggable LLM gateway - Pattern Map

**Mapped:** 2026-10-14
**Files analyzed:** 27 (15 new, 12 modified or retargeted)
**Analogs found:** 25 / 27 (all analog paths verified git-tracked; no gitignored mirror paths used)

Scope note: file list derived from CONTEXT.md (D-01..D-24) and the first 448 lines of RESEARCH.md (Q1-Q8, structure, Pitfall 1). Line numbers refer to the tracked files at the time of mapping.

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `src/lecture_study/llm/__init__.py` | config (re-export) | n/a | `src/lecture_study/ops/__init__.py` | exact |
| `src/lecture_study/llm/gateway.py` (port, `LlmRequest`, `LlmResponse`, `Stage`) | model/port | request-response | `src/lecture_study/quality/prompt_budget.py` (frozen slots dataclass) | role-match |
| `src/lecture_study/llm/errors.py` | utility | n/a | `ops/process_lifecycle.py` (`ProcessOutputLimitExceeded`), `quality/prompt_budget.py` (`PromptBudgetExceededError`) | role-match |
| `src/lecture_study/llm/config.py` | config | file-I/O | `study_materials/local_lectures.py` (`LocalLecture`, `load_local_lectures`) | exact |
| `src/lecture_study/llm/egress.py` | utility (Protocol + enforcement) | request-response | `study_materials/local_lectures.py` (fail-closed `unpublished_lecture_ids`) | partial |
| `src/lecture_study/llm/json_completion.py` (`complete_json`) | service | request-response | `scripts/generate_codex_semantic_ledgers.py::_generate_ledger` lines 108-134 | exact |
| `src/lecture_study/llm/codex_adapter.py` | service (adapter) | request-response, file-I/O | `scripts/generate_codex_semantic_ledgers.py::_call_codex_for_ledger` lines 153-186 | exact |
| `src/lecture_study/llm/llama_cpp_adapter.py` | service (adapter) | request-response, batch | codex adapter source above + `ops/process_environment.py`, `quality/prompt_budget.py` | role-match |
| `src/lecture_study/llm/factory.py` | service | request-response | `scripts/generate_codex_semantic_ledgers.py::_run` lines 74-83 (executable and env setup) | partial |
| `src/lecture_study/llm/run_record.py` (sidecar) | utility | file-I/O | `scripts/generate_codex_personal_study_flashcards.py` lines 225-234 (atomic write) | role-match |
| `src/lecture_study/study_materials/remote_llm_policy.py` | service | CRUD (read) | `study_materials/local_lectures.py::unpublished_lecture_ids` lines 150-153 | role-match |
| `src/lecture_study/study_materials/local_lectures.py` (add `remote_llm_allowed`) | model | CRUD | itself, field `publish` lines 93, 338-340 | exact |
| `scripts/register_local_lecture.py` (add `--remote-llm-allowed`) | controller (CLI) | request-response | itself, `--publish` lines 42, 105 | exact |
| `config/llm-gateway.example.yaml` | config | file-I/O | `config/targets.example.yaml` | exact |
| `.gitignore` (check `config/*.local.yaml` already covers `llm-gateway.local.yaml`) | config | n/a | `.gitignore` line 33 | exact |
| `scripts/generate_codex_{semantic_ledgers,drafts,critic_review,mock_exam,podcast,personal_study_flashcards}.py` (migrate) | controller (script) | request-response | `scripts/generate_codex_semantic_ledgers.py` (pilot) | exact |
| `src/lecture_study/cli/orchestrator.py` (preflight, env forwarding) | controller / composition root | request-response | itself: `_CODEX_PHASE_ENV_KEYS` lines 103-122, `_run_phase_5..7` lines 393-414 | exact |
| `scripts/01_provision_engines.sh` (opt-in model step, check new binaries) | config (shell) | batch, file-I/O | itself: `download_whisper_model` lines 128-136, `verify_sha256` | exact |
| `tests/conftest.py` or `tests/llm_gateway/conftest.py` (stub binaries) | test | request-response | `tests/conftest.py` fixtures | role-match |
| `tests/llm_gateway/test_*` (contract, adapter, config, egress, layering) | test | request-response | `tests/test_generate_codex_semantic_ledgers.py` lines 83-140 | role-match |
| `tests/test_generate_codex_*.py` (6 files, retarget) | test | request-response | same files, current patch-style | exact |
| `tests/test_cli.py::TestCodexScriptPhases` (env assertion) | test | request-response | itself lines 708-738 | exact |
| `tests/test_local_lectures.py` (flag default and idempotence) | test | CRUD | itself | exact |
| `docs/decisions/0046-*.md` and amendment notes on 0038, 0045 | doc | n/a | `docs/decisions/0045-*.md`, `0038-*.md` | exact |

## Pattern Assignments

### `src/lecture_study/llm/codex_adapter.py` (service, request-response + file-I/O)

**Analog:** `scripts/generate_codex_semantic_ledgers.py` (identical block in all six scripts)

**Imports pattern** (lines 29-55):
```python
import os
import tempfile
from pathlib import Path

from lecture_study.ops.process_environment import (
    resolve_trusted_executable,
    sanitized_child_process_environment,
)
from lecture_study.ops.process_lifecycle import run_bounded_process
```

**Constants owned by the adapter now** (lines 59-60):
```python
_SUBPROCESS_TIMEOUT_SECONDS = 900.0
_MAXIMUM_PROCESS_OUTPUT_BYTES = 16 * 1024 * 1024
```

**Environment pattern** (lines 75-83): sanitized env plus `HOME` only (codex needs `~/.codex`).
```python
environment = dict(sanitized_child_process_environment())
home_directory = os.environ.get("HOME")
if home_directory is not None:
    environment["HOME"] = home_directory
```

**Core pattern, argv must stay byte-identical** (lines 162-186):
```python
with tempfile.TemporaryDirectory(prefix="codex-semantic-") as raw_scratch_dir:
    scratch_dir = Path(raw_scratch_dir)
    output_path = scratch_dir / "response.txt"
    run_bounded_process(
        [codex_executable, "exec", "--skip-git-repo-check", "--sandbox", "read-only",
         "-C", str(scratch_dir), "-o", str(output_path), prompt],
        env=environment, cwd=scratch_dir,
        timeout_seconds=_SUBPROCESS_TIMEOUT_SECONDS,
        maximum_stdout_bytes=_MAXIMUM_PROCESS_OUTPUT_BYTES,
        maximum_stderr_bytes=_MAXIMUM_PROCESS_OUTPUT_BYTES,
        check=True,
    )
    output_bytes = output_path.read_bytes()
```
Return the bytes as `LlmResponse.content` (D-01: the adapter does not parse). Add the argv-size guard before spawning (D-23, raise typed prompt-too-large).

**Error handling pattern:** wrap failures the way the podcast script does, never interpolating the exception (its `cmd` embeds the prompt). `scripts/generate_codex_podcast.py` lines 293-309:
```python
except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ProcessOutputLimitExceeded) as error:
    # Never interpolate `error` itself: ... renders the full `cmd` list ...
    raise LlmResponseContractError(f"codex exec failed: {_describe_subprocess_failure(error)}", retry_category="contract") from error
```
Convert to a typed `LlmCallError` (D-22, no retry) instead of `LlmResponseContractError`; reuse `_describe_subprocess_failure` logic (read it in the podcast script before porting).

---

### `src/lecture_study/llm/llama_cpp_adapter.py` (service, request-response, batch)

**Analog:** the Codex adapter above for process/scratch shape; no in-repo analog for the llama.cpp argv (D-05, history absent). Build from RESEARCH.md Pattern 3 and Q2 (binary is `llama-completion`, per D-17).

**Executable and env pattern:** `ops/process_environment.py` lines 49-91 (`sanitized_child_process_environment`, `resolve_trusted_executable`). Env is an explicit allowlist plus `LLAMA_ARG_OFFLINE=1`; no `HOME`, no `HF_*`.
```python
def resolve_trusted_executable(executable: str | Path, *, search_path: str | None = None) -> str:
    ...  # resolves symlinks to a regular, executable file (stubs must be distinct files)
```

**Budget pattern (D-08, D-23):** `src/lecture_study/quality/prompt_budget.py` lines 12-51.
```python
budget = PromptBudget(input_tokens=n, output_reserve_tokens=cap, context_window_tokens=ctx)
if not budget.fits:  # convert to the typed prompt-too-large error naming stage and sizes
    ...
```
`PromptBudgetExceededError` (lines 8-9, 45-51) is the existing exception style; the gateway error must name the stage and sizes, so wrap instead of reusing `require_fits()` directly.

**Process call:** same `run_bounded_process(..., check=True)` call as the Codex adapter, but stdout is the response (`completed.stdout`), prompt delivered with `-f <file in scratch>`, never argv. Retry-only `--repeat-penalty 1.3 --repeat-last-n 256` keyed on `LlmRequest.is_retry`.

---

### `src/lecture_study/llm/json_completion.py` (service, request-response)

**Analog:** `scripts/generate_codex_semantic_ledgers.py::_generate_ledger` (lines 108-134) plus `_parse_ledger` (lines 189-195).

**Core retry pattern** (validation sits inside the retry boundary; retry also validated, second failure propagates):
```python
try:
    ledger = _call_codex_for_ledger(..., prompt=prompt)
    _validate_traceability(ledger, pack)
    return ledger
except (LlmResponseContractError, LlmParsingError) as error:
    retry_prompt = prompt + build_bounded_retry_prompt_section(
        heading="Retry feedback:",
        feedback=build_llm_retry_feedback(error),
    )
    ledger = _call_codex_for_ledger(..., prompt=retry_prompt)
    _validate_traceability(ledger, pack)
    return ledger
```
**Parse pattern** (the caller's `parse` callable):
```python
parse_llm_json_response(output_bytes, expected_kind="object",
                        response_adapter=_LEDGER_ADAPTER, declared_type=SemanticLedger)
```
Imports for the helper: `from lecture_study.ops.llm_response_contract import LlmResponseContractError, build_bounded_retry_prompt_section, build_llm_retry_feedback, parse_llm_json_response`. Generic signature uses `<T>` (global rule: no `unknown` in signatures). `retry_on` must be a parameter because only the semantic script retries its domain error (`LlmParsingError`).

---

### `src/lecture_study/llm/config.py` (config, file-I/O)

**Analog:** `src/lecture_study/study_materials/local_lectures.py`

**Strict model pattern** (lines 83-94):
```python
class LocalLecture(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    lecture_id: SafeIdentifier
    publish: bool = False
```
**Loader pattern, fail closed with typed error** (lines 132-147):
```python
def load_local_lectures(path: Path = LOCAL_LECTURES_PATH) -> list[LocalLecture]:
    if not path.is_file():
        return []
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            raise LocalLectureError("raiz do arquivo deve ser um mapa")
        return _LOCAL_LECTURES_ADAPTER.validate_python(payload.get("lectures", []), strict=False)
    except (yaml.YAMLError, ValidationError, UnicodeDecodeError) as error:
        raise LocalLectureError(f"aulas locais inválidas em {path}: {error}") from error
```
Gateway differences: a missing file means defaults (adapter `codex`), a present-but-invalid file raises; path constant `Path("config/llm-gateway.local.yaml")`; env override `LECTURE_STUDY_LLM_ADAPTER` replaces default and per-stage entries (D-20); `LECTURE_STUDY_LLM_CONFIG` selects an alternate path. Both env names pass the credential blocklist (`ops/process_environment.py` lines 34-46).

---

### `src/lecture_study/study_materials/local_lectures.py` (modify: add `remote_llm_allowed`)

**Analog:** the existing `publish` field in the same file.

Field (line 93): add next to it
```python
publish: bool = False
remote_llm_allowed: bool = False   # fail closed (D-15)
```
**Idempotence pattern to extend** (line 338): the rerun comparison only tolerates a `publish` change, so include the new field or a flag-only rerun is refused:
```python
if existing.model_copy(update={"publish": lecture.publish}) != lecture:
```
`_upsert_local_lecture` writes via `model_dump()`, so persistence is automatic. Read the guard at lines 340+ (`existing.publish and not lecture.publish`) for the downgrade-refusal convention and decide whether the new flag needs a symmetric guard.

### `scripts/register_local_lecture.py` (modify)

**Analog:** itself. `publish=args.publish` at line 42 and the `--publish` argparse flag at line 105; add `--remote-llm-allowed` the same way.

### `src/lecture_study/study_materials/remote_llm_policy.py` (service, CRUD read)

**Analog:** `unpublished_lecture_ids` (lines 150-153):
```python
def unpublished_lecture_ids(lectures: list[LocalLecture]) -> frozenset[str]:
    return frozenset(item.lecture_id for item in lectures if not item.publish)
```
Same shape inverted: allowed ids = `{l.lecture_id for l in lectures if l.remote_llm_allowed}` union `remote_llm_allowed_lecture_ids` from the gateway YAML (D-19). Classify local vs portal via `is_reserved_local_id` (lines 71-76). An unreadable registry (`LocalLectureError`) is a refusal, not a pass. Implements the `RemoteEgressPolicy` Protocol defined in `llm/egress.py`; `llm/` must not import `study_materials/`.

---

### `src/lecture_study/llm/run_record.py` (utility, file-I/O)

**Analog:** `scripts/generate_codex_personal_study_flashcards.py` lines 225-234 (atomic write):
```python
output_dir.mkdir(parents=True, exist_ok=True)
tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
tmp_path.write_text(deck.model_dump_json(indent=2), encoding="utf-8")
tmp_path.replace(output_path)
```
Target path per D-18: `workspace/data/llm_runs/<lecture_id>/<script_stage>.json` (never inside a stage output directory). Use a pydantic model with `model_dump_json(indent=2)`.

---

### `config/llm-gateway.example.yaml` (config)

**Analog:** `config/targets.example.yaml` (header-comment style: purpose, copy-to `*.local.yaml` (git-ignored), readers, strict-validation note, key-by-key documentation). `.gitignore` line 33 `config/*.local.yaml` already ignores `config/llm-gateway.local.yaml`. Only synthetic values; no real paths (leak-gate, ADR 0044 section 3).

---

### Six migrated scripts (controller, request-response)

**Analog (pilot):** `scripts/generate_codex_semantic_ledgers.py`

Keep: prompt builders (`_build_prompt` lines 137-150), root constants (lines 57-58, pinned by `tests/test_cli.py::TestCodexStageRootsMatchScriptDefaults`), argparse (keep `--codex-executable` as an override of `codex.executable`, line 227), domain validation, writers, `main()` error mapping (lines 65-71, add the typed gateway errors to the caught tuple).

Delete: `_SUBPROCESS_TIMEOUT_SECONDS`, `_MAXIMUM_PROCESS_OUTPUT_BYTES` (lines 59-60), `_call_codex_*`, HOME restoration (lines 75-83), `tempfile`, `run_bounded_process`, `sanitized_child_process_environment` imports.

Gate ordering constraint: build the gateway AFTER the existing gates (critic gate, lecture-id validation) and BEFORE the first call; tests assert a blocked or malformed lecture never resolves `codex` (RESEARCH Anti-Patterns; comment at `generate_codex_podcast.py` line 175; `test_main_refuses_a_malformed_lecture_id_before_locating_codex`).

Per-script specifics (from RESEARCH Q1 table):
- critic: `scripts/generate_codex_critic_review.py::_evaluate_with_repair` lines 144-182 stays in the script; it takes `gateway` instead of `codex_executable`/`environment`, and calls `generate_codex_drafts._generate_draft(...)` with a `drafting` gateway (D-21), review calls with a `critic` gateway.
- exam and flashcards: validation errors raised as `LlmResponseContractError` inside the retry boundary; flashcards `_build_deck` `ValidationError` wrap stays outside it.
- podcast: `_strip_execution_mode_echo` goes into the `parse` callable; per-call `timeout_seconds` override (D-03); process-failure retry removed (D-22).

---

### `src/lecture_study/cli/orchestrator.py` (composition root)

**Analog:** itself.

**Env allowlist** (lines 103-122):
```python
_CODEX_PHASE_ENV_KEYS = ("HOME", "LANG", "LC_ALL", "LC_CTYPE", "PATH", "TEMP", "TMP", "TMPDIR")

def _codex_phase_environment() -> dict[str, str]:
    return {name: os.environ[name] for name in _CODEX_PHASE_ENV_KEYS if name in os.environ}
```
Add exactly `LECTURE_STUDY_LLM_ADAPTER` and `LECTURE_STUDY_LLM_CONFIG`; rename to a neutral name and update `tests/test_cli.py` accordingly.

**Phase runners** (lines 393-414): `_run_codex_phase("generate_codex_semantic_ledgers.py", lecture_id, phase_number=5, phase_name="semantic")`. The preflight maps phases to gateway stages with an explicit table `{5: SEMANTIC, 6: DRAFTING, 7: CRITIC+DRAFTING, 13: EXAMS, 14: PODCAST}`, never by `PhaseSpec.name` (phase 12 is also "flashcards"). Place at the top of `run_all`, `run_phase`, `run_personal_study`; failures raise `PipelineExecutionError` so `main()` exits 1 (`cli/main.py` lines 98-110 per research).

---

### `scripts/01_provision_engines.sh` (modify)

**Analog:** itself.

**SHA-256 helpers** (lines 53-79): `sha256_of`, `verify_sha256`, `sha256_matches`.
**Download pattern** (lines 128-136):
```bash
download_whisper_model() {
    if sha256_matches "${WHISPER_MODEL_FILE}" "${WHISPER_MODEL_SHA256}"; then
        echo "==> ... already present with matching checksum, skipping"
        return
    fi
    ...
    verify_sha256 "${WHISPER_MODEL_FILE}" "${WHISPER_MODEL_SHA256}"
}
```
Build check (line 124): `build_if_stale ... "${LLAMA_DIR}/build/bin/llama-cli"` only checks `llama-cli`; also verify `llama-completion` and `llama-tokenize`. The unused `LLM_MODEL_SHARDS` array (around line 30) already holds the Qwen2.5-14B-Instruct Q4_K_M shard hashes (D-24). Model step is opt-in. Update the stale header comment (lines 7-11).

---

### Tests

**Script tests (retarget):** `tests/test_generate_codex_semantic_ledgers.py` lines 83-140 are the current style (patch `run_bounded_process`, write the `-o` file). The scratch-cwd test (lines 83-108) becomes an adapter contract test:
```python
def fake_run_bounded_process(args, *, cwd, **kwargs):
    captured["cwd"] = cwd
    captured["dash_c"] = args[args.index("-C") + 1]
    Path(args[args.index("-o") + 1]).write_bytes(_LEDGER_JSON)
...
assert Path(captured["cwd"]).resolve() != repo_root
assert str(captured["cwd"]) == captured["dash_c"]
```
Retry test style (lines 129-140): script-level tests inject a fake gateway returning scripted bytes instead of patching `_call_codex_for_*`.

**Orchestrator env test:** `tests/test_cli.py` lines 708-738 (`captured["command"] == [sys.executable, f"scripts/{script_filename}", LECTURE.id]`; env equality against the allowlist). Update the env equality to include the two new keys; add a negative test that an ambient `OPENAI_API_KEY`-style variable is not forwarded.

**Stub binaries:** `tests/conftest.py` holds shared fixtures (basenames must be unique, `tests/` has no `__init__.py`). Per RESEARCH Q6: write real executable stubs (`#!<sys.executable>`, mode 0o755) into `tmp_path`, distinct files for `llama-completion`, `llama-tokenize`, `codex` (no symlinks, since `resolve_trusted_executable` resolves them). Parametrized shared suite over `("codex", "llama_cpp")`; argv/env tripwire stub exits 3 on any download flag.

**Layering test:** new `tests/test_llm_layering.py`, `ast` scan (no existing analog; see below).

## Shared Patterns

### Bounded process
**Source:** `src/lecture_study/ops/process_lifecycle.py` lines 156-215 (`run_bounded_process`: keyword-only `env`, `timeout_seconds`, `maximum_stdout_bytes`, `maximum_stderr_bytes`, `cwd`, `check`; `start_new_session=True`, `stdin=DEVNULL`, re-sanitizes `env` at line 176).
**Apply to:** both adapters. Never call `subprocess` directly.

### Typed errors never render the command
**Source:** `scripts/generate_codex_podcast.py` lines 293-309.
**Apply to:** every adapter failure path and the scripts' `main()` mapping to exit 1.

### Strict pydantic + yaml.safe_load, fail closed with domain error
**Source:** `study_materials/local_lectures.py` lines 83-94, 132-147.
**Apply to:** `llm/config.py`, run-record model.

### Dependency rule
`domain <- quality <- ops <- llm <- {cli, scripts}`. `llm/` imports only `domain`, `quality.prompt_budget`, `ops.process_lifecycle`, `ops.process_environment`, `ops.llm_response_contract`. `ops/` and `site/` must not import `llm/`; `llm/` must not import `study_materials/`. Mypy strict on `src`, no `type: ignore`; no `unknown` in signatures (use generics, global rule).

### Leak gate
Repository-relative paths and synthetic values only in the YAML example, ADR 0046 and tests (ADR 0044 section 3).

## No Analog Found

| File | Role | Data Flow | Reason |
|---|---|---|---|
| `src/lecture_study/llm/llama_cpp_adapter.py` (argv contract) | service | batch | llama.cpp text route absent from git history (D-05); use RESEARCH.md Pattern 3 and Q2 flag table |
| `tests/test_llm_layering.py` | test | static analysis | No import-boundary test exists (only `tests/test_site_image.py`); build from RESEARCH Q7 item list |
| Stub-executable fixtures | test | request-response | No real-process stub exists; existing tests patch `run_bounded_process` or `asyncio.create_subprocess_exec` (`tests/test_captioning.py` lines 116-145) |

## Metadata

**Analog search scope:** `scripts/generate_codex_*.py`, `src/lecture_study/{ops,quality,study_materials,cli}`, `config/`, `scripts/01_provision_engines.sh`, `tests/`
**Files read in full or by targeted range:** semantic script, prompt_budget, local_lectures (ranges), process_environment (range), process_lifecycle (range), orchestrator (ranges), critic/podcast/flashcards (ranges), provisioning script (ranges), test_cli and semantic test (ranges)
**Not read:** RESEARCH.md lines 449-673 (Pitfalls 2+, Validation Architecture, Open Questions, Assumptions Log); planner should read them directly.
**Pattern extraction date:** 2026-10-14
