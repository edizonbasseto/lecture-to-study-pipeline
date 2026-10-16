# Phase 6: Pluggable LLM gateway - Research

**Researched:** 2026-10-14
**Domain:** Python port-and-adapter refactor of six Codex-CLI generation scripts, plus a new local llama.cpp adapter, configuration, egress policy and contract tests
**Confidence:** HIGH for the in-repo findings (every script, test and module was read this session); MEDIUM for llama.cpp runtime behavior (read from source at the pinned commit, never executed here: no binary and no model exist on this machine)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Port shape and layering**
- **D-01:** The gateway has two layers. The port is a raw-text call (prompt in, response bytes out). A thin shared helper on top of any port performs the parse with `parse_llm_json_response` and the single bounded retry with `build_bounded_retry_prompt_section`. The six scripts keep prompt building and their domain-specific validation (for example traceability checks) and drop their own call, parse and retry boilerplate. The critic repair loop (ADR 0036) stays in its script.
- **D-02:** The port and adapters live in a new package `src/lecture_study/llm/`. The port is the inner element; the Codex and llama.cpp adapters are the outer ones. `scripts/` and `ops/` depend on `llm/`, never the reverse.
- **D-03:** Bounded-process limits (timeout 900 s, 16 MiB output cap), the read-only sandbox and the scratch working directory are owned by each adapter as defaults. Callers pass the prompt only. A per-call timeout override is allowed for the large podcast prompt.

**Local adapter**
- **D-04:** `LlamaCppAdapter` invokes `llama-cli` once per call as a bounded process through `run_bounded_process`. No `llama-server`, no listener. The contract is rebuilt from ADR 0020 (mandatory `-n` completion cap, sized against the context budget), ADR 0022 (schema embedded as literal prompt text, `--single-turn`, no `--json-schema-file`) and ADR 0034 (repeat penalty on the feedback-carrying retry only).
- **D-05:** The llama.cpp text route does not exist in this repository's git history (history starts at `9322e91` without it). GW-03's "restored from the history" cannot be done from git. The adapter is written new from the three ADRs; `scripts/01_provision_engines.sh` already pins llama.cpp.
- **D-06:** The model is path-configured with no pinned default and no weights in the repository. The model path and family come from configuration. The new ADR documents one tested example model, and provisioning offers it with a SHA-256 check as done for the Whisper model.
- **D-07:** Done bar for the adapter in this phase: GW-06 contract tests with a stub binary in CI, plus one documented manual real-model run of stages 5, 6 and 7 on a pack. No quality numbers are produced here.
- **D-08:** When a prompt does not fit the local context window, the adapter raises a typed prompt-too-large error naming the stage and the sizes, before any model call. No truncation and no chunking.
- **D-09:** "No network egress" is shown, not claimed: the adapter accepts only a local model path, rejects URL and Hugging Face repo style arguments, sets offline environment variables, and a test asserts the argv and the environment. No OS sandbox.

**Selection and fail-closed configuration**
- **D-10:** Configuration lives in a YAML file with an environment override. `config/llm-gateway.example.yaml` is versioned; the local copy is git-ignored, the same pattern as `config/targets.example.yaml`. An environment variable (name to be fixed at planning, expected `LECTURE_STUDY_LLM_ADAPTER`) overrides for a single run. The orchestrator forwards the resolved choice to child processes.
- **D-11:** A per-stage override is supported now: a default adapter plus optional overrides keyed by stage name (semantic, drafting, critic, exams, podcast, flashcards).
- **D-12:** Bad configuration is detected twice. The CLI preflight resolves and validates the configuration for all selected stages before the first phase runs. Each script re-validates before its first call. Checks: adapter name is known, executable and model path exist. Nothing is contacted. An unknown or misconfigured adapter exits non-zero before any model call (GW-04, success criterion 2).
- **D-13:** Provenance is a sidecar run record beside the stage output (adapter name and model identifier). Artifact schemas stay unchanged. Reversibility: reversible - a sidecar file can be added or dropped without touching shared contracts.

**Egress enforcement (ADR 0045 section 2)**
- **D-14:** Section 2 is closed by code, not by procedure. The lecture registry gains a per-lecture field `remote_llm_allowed`. The gateway refuses the Codex adapter for a lecture that does not carry `remote_llm_allowed: true`, before any call. Detecting patient-identifying data stays the operator's responsibility; the flag is the operator's recorded verification.
- **D-15:** Operator override: a lecture with no flag is remote NOT allowed (fail closed). The demo catalog sets `remote_llm_allowed: true` explicitly. Consequence: the configured default adapter stays Codex (GW-04), but a Codex run on an unflagged lecture is refused, so every existing registry entry needs the flag added to keep using Codex. Reversibility: costly - flipping the default later changes behavior for every registered lecture and every test that assumes Codex is reachable.
- **D-16:** The new ADR amends ADR 0038 (local text-LLM route returns as a gateway adapter), closes ADR 0045 section 2 with the flag rule, amends baseline requirement GEN-07 (GW-05), and keeps the provider's retention and training terms as a procedural check.

### Claude's Discretion
- Exact module names inside `llm/`, the env variable name, the YAML schema keys beyond adapter, per-stage overrides and model path.
- Contract-test layout, and how the stub llama-cli binary is built for CI.
- Migration order of the six scripts.
- Where the sidecar run record is written and its file name.

### Deferred Ideas (OUT OF SCOPE)
- Chunking or hierarchical splitting of prompts that exceed the local context window - new capability, own phase.
- `llama-server` on loopback for faster repeated calls - revisit if per-call model reload makes Phase 7 runs impractical.
- Adapter name recorded inside artifact payloads - rejected for now to avoid touching shared schemas.
- OS-level network sandbox for the local adapter.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| GW-01 | `LlmGateway` port inward of the scripts; six stages call it | Q1 (script anatomy, migration shape), Q5 (stage 18), Q7 (layering), Architecture Patterns |
| GW-02 | `CodexCliAdapter` preserves bounded process, sanitized env, read-only sandbox, failure capture | Q1 (exact argv and env), Q3 (child env), Pitfall 3 (argv limit) |
| GW-03 | `LlamaCppAdapter`, local GGUF, no network egress | Q2 (llama.cpp findings: binary choice, flags, offline), Pitfall 1, Pitfall 2 |
| GW-04 | Adapter selected by configuration, default Codex, unknown/misconfigured fails closed | Q3, Q4 (egress), Standard Stack (config), Validation Architecture |
| GW-05 | New ADR amends ADR 0038, closes ADR 0045 section 2, amends GEN-07 | Q8 |
| GW-06 | Both adapters pass the same contract tests, incl. critic repair loop; CI uses a stub binary | Q6, Validation Architecture |
</phase_requirements>

## Summary

The six `scripts/generate_codex_*.py` files already repeat one call shape (`codex exec --skip-git-repo-check --sandbox read-only -C <scratch> -o <response.txt> <prompt>` through `run_bounded_process`, then `parse_llm_json_response`, then one retry with bounded feedback). Moving that shape behind a port is mechanical. The risky parts are not the Codex side. They are: (a) the pinned llama.cpp commit no longer behaves like the `llama-cli` that ADRs 0020, 0022 and 0034 describe, (b) the existing JSON contract parser rejects any output holding more than one JSON candidate, so a prompt echo would make every local response fail, (c) the whole-lecture podcast prompt can exceed the macOS argv limit, (d) the natural place for the sidecar (inside the stage output directory) breaks two existing consumers, and (e) D-14/D-15 do not say where the egress flag lives for lectures that come from the portal catalog rather than the local registry.

Findings that change the locked design as written (flagged for the planner and, per the global "never assume" rule, for operator confirmation before they become plans):

1. **D-04 names `llama-cli`, but at commit `91d2fc3` (the pin) `llama-cli` is an interactive chat client that starts an embedded `llama-server` thread bound to `127.0.0.1` on a random port, prints a banner and echoes the prompt.** That contradicts "no listener" and pollutes stdout. The non-listening, non-chat binary at the same commit is `llama-completion` (flags `-st/--single-turn`, `--no-display-prompt`, `--no-escape`, `-f`, `-n`, `--offline` all exist). ADR 0020's own 2026-07-28 erratum already moved to `llama-completion` plus `llama-tokenize`. Recommendation: the adapter runs `llama-completion`, keeping D-04's intent (one bounded process per call, no server) and its `--single-turn` flag. [VERIFIED: raw source at the pinned commit, see Q2]
2. **`--no-escape` is mandatory.** llama.cpp processes `\n`, `\"` and `\\` escape sequences in the prompt by default, which corrupts the embedded JSON Schema text (ADR 0022 contract). The ADR 0020 erratum says "escape processing disabled" but the D-04/D-09 text does not list it. [VERIFIED: `common/arg.cpp:764-765`, `common.h:564`]
3. **The sidecar cannot sit inside the per-lecture stage directories.** `scripts/generate_progress_dashboard.py::_count_entries` counts every directory entry, and `quality/delivery.py` fingerprints and rewrites whole output directories (podcast, exam). Recommendation: a separate tree `workspace/data/llm_runs/<lecture_id>/<script_stage>.json`. [VERIFIED: code read, Q1/Pitfall 4]
4. **Stage 18 is outside the orchestrator** (ADR 0045 section 5; `PHASE_REGISTRY` has no phase 18), so the CLI preflight never covers it. Its own script-level re-validation (D-12 second layer) is the only gate; the orchestrator-forwarding requirement does not apply to it.
5. **`Phase 12` in `PHASE_REGISTRY` is also named `"flashcards"`** (the deterministic Anki deck). The preflight must map phases to gateway stage names with an explicit table, never by `PhaseSpec.name`.
6. **GW-03's wording ("restored from the repository history") is not achievable (D-05)**; REQUIREMENTS.md GW-03 should be reworded by the same ADR/commit that records GW-05.

**Primary recommendation:** Build `src/lecture_study/llm/` first with both adapters, the shared `complete_json` helper, the configuration resolver, and the stub-based contract suite (no script touched), then migrate the scripts in the order semantic, drafts then critic, exam and flashcards, podcast, then wire the orchestrator preflight and env forwarding, the `remote_llm_allowed` flag and the ADR.

## Architectural Responsibility Map

This project is a file-based batch CLI, not a web tier stack; the tiers below are the clean-architecture rings applied to it.

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| `LlmGateway` port, request/response types, typed errors | Inner (`llm/` core modules) | - | Contract that scripts depend on; must import no adapter, no stage package |
| JSON parse plus single retry (`complete_json`) | Application helper (`llm/`) | - | Wraps any port; reuses `ops/llm_response_contract` |
| Stage prompt building and domain validation (traceability, student-facing guard, count checks) | Scripts (`scripts/`) | - | D-01: stays in the scripts |
| Codex process spawn (argv, scratch dir, env) | Adapter (`llm/codex_adapter.py`) | `ops/process_lifecycle` | D-03: limits owned by the adapter |
| llama.cpp process spawn, token preflight, offline guards | Adapter (`llm/llama_cpp_adapter.py`) | `ops/process_lifecycle` | D-04, D-08, D-09 |
| Configuration load/validate/resolve (YAML + env) | `llm/config.py` | CLI preflight, scripts | One resolver used twice (D-12) |
| Remote-egress decision per lecture | Policy port in `llm/` (Protocol) | Registry-backed implementation in `study_materials/` | Keeps `llm/` independent of the academic registry |
| Preflight and child-env forwarding | Composition root (`cli/orchestrator.py`) | `cli/main.py` | The orchestrator already owns `_codex_phase_environment` |
| Run record (sidecar) | `llm/run_record.py` | scripts call it | Provenance without touching artifact schemas |
| Registry flag `remote_llm_allowed` | `study_materials/local_lectures.py` | `register_local_lecture.py` | The registry already owns per-lecture operator flags (`publish`, `kind`) |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pydantic | `>=2.13.4` (already a dependency) | Strict config models | Same pattern as `LocalLecture`, `AcademicLesson` (`strict=True, extra="forbid", frozen=True`) [VERIFIED: pyproject.toml, local_lectures.py:86] |
| pyyaml | `>=6.0.3` (already a dependency) | `yaml.safe_load` of `config/llm-gateway*.yaml` | Same loader style as `load_local_lectures` [VERIFIED: local_lectures.py:132-147] |
| `ops/process_lifecycle.run_bounded_process` | in repo | Bounded child process (timeout, caps, new session, stdin DEVNULL) | D-04; every existing caller [VERIFIED: process_lifecycle.py:156-215] |
| `ops/process_environment` | in repo | `sanitized_child_process_environment`, `resolve_trusted_executable` | D-09 [VERIFIED: process_environment.py:49-91] |
| `ops/llm_response_contract` | in repo | `parse_llm_json_response`, `build_bounded_retry_prompt_section`, `build_llm_retry_feedback`, `LlmResponseContractError` | D-01 [VERIFIED: llm_response_contract.py:44,114,139,36] |
| `quality/prompt_budget.PromptBudget` | in repo | Token budget arithmetic for D-08 | Already has `required_tokens`, `fits`, `require_fits`, `PromptBudgetExceededError`, default 128-token margin [VERIFIED: prompt_budget.py:20-55] |
| llama.cpp `llama-completion`, `llama-tokenize` | pinned commit `91d2fc387529940230555abd297a8b5e99737d3f` | Local text generation and exact token count | Both built by the existing `cmake` invocation (`tools/CMakeLists.txt` adds `completion` and `tokenize` unconditionally) [VERIFIED: raw `tools/CMakeLists.txt` at the pin] |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest, monkeypatch, tmp_path | `pytest>=9.1.1` | Contract and wiring tests | All new tests; `monkeypatch` is the repo's preferred mocking tool [CITED: .planning/codebase/TESTING.md] |
| Codex CLI | 0.155.1 on this machine | Real `codex exec` | Only for the operator's runs; tests use a stub [VERIFIED: `codex --version`] |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `llama-completion` per call | `llama-cli` (D-04 as written) | Starts an embedded loopback HTTP server, prints banner and echoes the prompt; breaks "no listener" and the single-candidate parser |
| `llama-completion` per call | resident `llama-server` | Deferred by CONTEXT (faster reuse, but a listener) |
| `llama-tokenize` preflight (exact) | byte-length upper bound | No second binary and no model load, but over-rejects; see Open Question 5 |
| New dependency for token counting / templating | none | Not needed |

**Installation:** no new Python packages. `uv sync --group dev --locked` is unchanged.

**Version verification:** no package is added, so no registry lookup applies. llama.cpp is pinned by commit in `scripts/01_provision_engines.sh:23` (`LLAMA_REPO_COMMIT="91d2fc387529940230555abd297a8b5e99737d3f"`); that commit exists upstream (author date 2026-07-20, message "llama_dsv4: write only used rows in state (#25325)") [VERIFIED: GitHub API commit lookup].

## Package Legitimacy Audit

No external package is installed in this phase. The only new third-party artifact is the optional example GGUF model, which is a data file verified by a SHA-256 value, not a package.

| Package | Registry | Age | Downloads | Source Repo | Verdict | Disposition |
|---------|----------|-----|-----------|-------------|---------|-------------|
| (none) | - | - | - | - | - | - |

**Packages removed due to [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none

## Architecture Patterns

### System Architecture Diagram

```
operator
  |  run-all / run-phase / run-personal-study <lecture>        (stage 18: script run by hand)
  v
cli/main.py -> cli/orchestrator.py
  |  1. resolve_gateway_configuration(env, YAML)   -- fail closed, nothing contacted
  |  2. per selected LLM stage: adapter known? executables + model path exist?
  |     if adapter == codex: lecture carries remote_llm_allowed ?  (policy)
  |  3. child env = allowlist + LECTURE_STUDY_LLM_ADAPTER / LECTURE_STUDY_LLM_CONFIG
  v
scripts/generate_codex_*.py  (child process, or standalone for stage 18)
  |  re-run the same resolver + checks AFTER the critic gate / lecture-id validation
  |  gateway = build_gateway(stage, lecture_id, ...)
  |  prompt = stage-specific builder
  v
llm.complete_json(gateway, stage, prompt, parse=..., validate=...)
  |  attempt 1 -> gateway.complete(request) -> bytes -> parse_llm_json_response -> validate
  |  on LlmResponseContractError (or stage-specific domain error): attempt 2 with
  |  prompt + build_bounded_retry_prompt_section(...), request.is_retry = True
  v
LlmGateway (port)
  |-- CodexCliAdapter:  scratch dir, `codex exec ... -o response.txt <prompt>`, env = sanitized + HOME
  |-- LlamaCppAdapter:  scratch dir, prompt file, llama-tokenize preflight -> PromptTooLargeError,
  |                     `llama-completion -m <local gguf> -f prompt ... --offline`, env = sanitized + LLAMA_ARG_OFFLINE
  v
bytes -> script validation -> artifact write -> llm_runs/<lecture>/<stage>.json (sidecar)
```

### Recommended Project Structure

```
src/lecture_study/llm/
  __init__.py            # re-export surface (repo convention)
  gateway.py             # LlmGateway Protocol, LlmRequest, LlmResponse, Stage (StrEnum)
  errors.py              # LlmGatewayError and subclasses (inner; imports nothing outward)
  config.py              # GatewayConfiguration models, load + env override, resolve(stage)
  egress.py              # RemoteEgressPolicy Protocol + enforcement function (no registry import)
  json_completion.py     # complete_json (parse + single retry)
  codex_adapter.py       # CodexCliAdapter
  llama_cpp_adapter.py   # LlamaCppAdapter (+ stage budget table)
  factory.py             # build_gateway(stage, lecture_id, configuration, policy)
  run_record.py          # sidecar writer
src/lecture_study/study_materials/remote_llm_policy.py   # registry/catalog-backed RemoteEgressPolicy
config/llm-gateway.example.yaml
tests/llm_gateway/                    # unique file basenames (tests/ has no __init__.py)
```

Dependency rule: `domain <- quality <- ops <- llm <- {cli, scripts}`. `llm/` imports only `domain`, `quality.prompt_budget` and `ops/{process_lifecycle, process_environment, llm_response_contract}`. `ops/` must NOT import `llm/` (D-02's "scripts/ and ops/ depend on llm/" is satisfied by scripts and cli only; an `ops -> llm` edge would create a cycle with `llm -> ops`). `study_materials/` may import `llm/` types; `llm/` must not import `study_materials/`.

### Pattern 1: Port and shared helper (D-01)
**What:** `LlmGateway.complete(request) -> LlmResponse` is raw text in, bytes out. `complete_json` owns parse plus the single retry.
**When to use:** every one of the six scripts.
**Example (illustrative shape, not final code):**
```python
# Source: shape derived from the six scripts' common code; names are Claude's discretion (D-01, D-02)
class Stage(StrEnum):
    SEMANTIC = "semantic"; DRAFTING = "drafting"; CRITIC = "critic"
    EXAMS = "exams"; PODCAST = "podcast"; FLASHCARDS = "flashcards"

@dataclass(frozen=True, slots=True)
class LlmRequest:
    stage: Stage
    prompt: str
    is_retry: bool = False
    timeout_seconds: float | None = None   # per-call override (podcast), D-03

@dataclass(frozen=True, slots=True)
class LlmResponse:
    content: bytes
    adapter_name: str
    model_identifier: str

class LlmGateway(Protocol):
    def complete(self, request: LlmRequest) -> LlmResponse: ...

def complete_json[T](
    gateway: LlmGateway, *, stage: Stage, prompt: str,
    parse: Callable[[bytes], T],
    validate: Callable[[T], None] | None = None,
    retry_on: tuple[type[Exception], ...] = (LlmResponseContractError,),
    timeout_seconds: float | None = None,
) -> T:
    """Attempt once; on a retryable error re-ask once with bounded feedback."""
```
Retry shape to preserve: the first attempt runs call, parse and `validate` inside the `try`; the retry runs the same three steps and its failure propagates (see the semantic script, where traceability errors are retried).

### Pattern 2: Codex adapter argv (must stay byte-identical, GW-02)
```python
# Source: scripts/generate_codex_semantic_ledgers.py:162-186 (identical in all six scripts)
[codex_executable, "exec", "--skip-git-repo-check", "--sandbox", "read-only",
 "-C", str(scratch_dir), "-o", str(scratch_dir / "response.txt"), prompt]
# run_bounded_process(..., env=environment, cwd=scratch_dir, timeout_seconds=900.0,
#                     maximum_stdout_bytes=16 MiB, maximum_stderr_bytes=16 MiB, check=True)
```
`environment = dict(sanitized_child_process_environment())` plus `HOME` copied from `os.environ` (codex needs `~/.codex`). The response is read from `response.txt`, not from stdout.

### Pattern 3: llama.cpp adapter argv (recommended contract)
```python
# Source: flags verified in common/arg.cpp and tools/completion/completion.cpp at the pinned commit (Q2)
[llama_completion, "-m", str(model_path), "-f", str(prompt_file),
 "-c", str(context_tokens), "-n", str(completion_tokens), "--temp", temperature,
 "--single-turn", "--no-display-prompt", "--no-escape", "--simple-io", "--offline",
 "--reasoning", "off",
 *(["--repeat-penalty", "1.3", "--repeat-last-n", "256"] if request.is_retry else [])]
# env = sanitized PATH/locale/TMP + {"LLAMA_ARG_OFFLINE": "1"}; no HOME, no HF_*, no other LLAMA_ARG_*
```
stdout holds only generated text (plus a trailing ` [end of text]` marker, harmless to the parser); logs go to stderr. [VERIFIED: `common/log.cpp:89-92` routes level NONE to stdout and every other level to stderr; `completion.cpp:971` prints the marker]

### Anti-Patterns to Avoid
- **Passing `-hf`, `-mu`, `-dr`, `--hf-file`, `-hft` or any `LLAMA_ARG_*` other than `LLAMA_ARG_OFFLINE`:** these enable downloads or alter the run; the env is built explicitly, never inherited.
- **Putting the sidecar in a stage directory:** breaks the progress dashboard and delivery fingerprints.
- **Importing `llm/` from `site/`, or `study_materials/` from `llm/`:** breaks the site image and the dependency rule.
- **Resolving the Codex executable before the critic gate / lecture-id validation:** existing tests assert a blocked or malformed lecture never resolves `codex` (`test_main_refuses_a_malformed_lecture_id_before_locating_codex`, podcast comment at `generate_codex_podcast.py:175`).
- **Deriving the gateway stage from `PhaseSpec.name`:** phase 12 is also `"flashcards"`.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Bounded child process | New `subprocess` wrapper | `run_bounded_process` | Timeout, output caps, process-group kill, stdin DEVNULL already tested |
| Child environment | `os.environ.copy()` | explicit dict through `sanitized_child_process_environment` | Blocklist covers `*_TOKEN`, `*_API_KEY`, `PORTAL_*`, `LECTURE_STUDY_QUALITY_*` |
| Executable lookup | `shutil.which` + trust | `resolve_trusted_executable` | Resolves symlinks to a regular, executable file |
| JSON extraction from model text | regex/`json.loads` | `parse_llm_json_response` | Duplicate keys, ambiguity, non-finite numbers, undeclared fields |
| Retry feedback text | free-form feedback | `build_llm_retry_feedback` + `build_bounded_retry_prompt_section` | Never replays model bytes (2 KiB bound) |
| Token counting | bytes/4 heuristic as the primary check | `llama-tokenize --show-count` | Exact for the chosen GGUF (ADR 0020 erratum precedent) |
| Budget arithmetic | custom math | `PromptBudget` | Already includes the output reserve and a 128-token margin |
| YAML schema checks | manual dict probing | strict pydantic models | Same loader pattern as the registry |
| Atomic file write | `write_text` | tmp file plus `Path.replace` | Pattern used by the flashcards script (`generate_codex_personal_study_flashcards.py:231-233`) |

**Key insight:** every hazard in this phase (prompt echo, escapes, argv size, env leaks) is already guarded by an existing repository helper or a one-line llama.cpp flag; the work is wiring and tests, not new machinery.

## Q1. The six scripts: commonalities, differences, tests, migration

### Identical across all six (VERIFIED by reading each file)
- Codex argv, scratch `tempfile.TemporaryDirectory(prefix="codex-...")` as both `cwd` and `-C`, `-o response.txt`, `check=True`, `_SUBPROCESS_TIMEOUT_SECONDS = 900.0`, `_MAXIMUM_PROCESS_OUTPUT_BYTES = 16 * 1024 * 1024`.
- `environment = dict(sanitized_child_process_environment())`, then `HOME` restored from `os.environ`.
- `--codex-executable` argparse flag (default `"codex"`) and `resolve_trusted_executable(args.codex_executable)`.
- One retry on `LlmResponseContractError` with heading `"Retry feedback:"` and `build_llm_retry_feedback(error)`.
- `main()` catches `(LlmResponseContractError, FileNotFoundError)` (exam, podcast, flashcards add `ValidationError` and the critic-gate errors) and returns 1.

### Differences that the migration must preserve or consciously change

| Script (stage) | Output | Parse kind | Post-parse validation (inside retry boundary) | Retry trigger | Extra flags | Notes |
|---|---|---|---|---|---|---|
| `generate_codex_semantic_ledgers.py` (5) | `ledger_NNN.json` per pack | object `SemanticLedger` | `_validate_traceability` (raises `LlmParsingError`) | `LlmResponseContractError` **and** `LlmParsingError` | `--pack-sequence`, `--packs-root`, `--output-root` | Validation sits inside the `try`; the retry is also validated |
| `generate_codex_drafts.py` (6) | `draft_NNN.json` + `depth_report.md` | object `LearningUnitDraft` | none | contract error | `--packs-root --ledgers-root --output-root` | `_generate_draft(..., feedback)` is imported and called by the critic script; the critic feedback section uses `build_bounded_retry_prompt_section` too |
| `generate_codex_critic_review.py` (7) | `unit_NNN.json` | object `CriticReview` | none | contract error | `--drafts-root --packs-root --ledgers-root --output-root --max-retries` | Repair loop `_evaluate_with_repair` (ADR 0036) stays in the script; `import generate_codex_drafts` at module level |
| `generate_codex_mock_exam.py` (13) | `exam.json` | array `list[MockQuestion]` | student-facing guard (ADR 0027) and count check raise `LlmResponseContractError` | contract error | `--ledgers-root --validated-units-root --drafts-root --output-root --questions-per-unit-cap --minimum-total-questions` | One call per unit plus supplemental single-question calls; critic-gate errors checked before codex is resolved |
| `generate_codex_podcast.py` (14) | via `PodcastScriptCompiler` | object `PodcastScript` | `_strip_execution_mode_echo` before parse; `_normalize_echoed_unit_ids`, `_validate_traceability`, listener guard after parse | contract error, **and** subprocess failures (`CalledProcessError`, `TimeoutExpired`, `ProcessOutputLimitExceeded` are wrapped into `LlmResponseContractError("codex exec failed: ...")` without echoing `cmd`) | `--validated-units-root --drafts-root` only | One whole-lecture prompt (no size check, ADR 0037 known gap) |
| `generate_codex_personal_study_flashcards.py` (18) | `deck.json` (atomic) | array `list[PersonalStudyFlashcard]` | student-facing guard and count check | contract error | `--ledgers-root --validated-units-root --drafts-root --output-root --cards-per-unit-cap --minimum-total-cards` | Standalone, not orchestrated; `_build_deck` wraps `ValidationError` as a `schema` contract error (raised outside the retry) |

Behavior differences to decide, not to lose silently:
1. **Process failures:** only the podcast script retries after a failed `codex exec`; the other five let `CalledProcessError` propagate (a traceback whose argv embeds the whole prompt, which the orchestrator's persisted-log design already anticipates). Recommendation: adapters raise one typed `LlmCallError` that never renders `cmd`, scripts' `main()` map it to exit 1, and `complete_json` retries only on parse and validation errors. This changes podcast (no retry after a process failure) and makes the other five safer. Record it in the ADR. (Open Question 4)
2. **Critic redraft:** the critic script redrafts through `generate_codex_drafts._generate_draft`. Under per-stage overrides the redraft call is a `drafting` call inside a `critic` run. Recommendation: the critic script builds two gateways (`critic` for reviews, `drafting` for redrafts), the CLI preflight validates both for stage 7, and the sidecar is keyed by the invoking script stage and lists every adapter used. (Open Question 3)
3. **Per-call timeout:** none of the scripts overrides 900 s today; D-03's override is for the podcast (value is Claude's discretion; ASSUMED, see Assumptions Log A6).

### Tests that pin the scripts today (all must be rewritten or retargeted)
- `tests/test_generate_codex_{semantic_ledgers,drafts,critic_review,mock_exam,podcast,personal_study_flashcards}.py` load the script by file path and patch private names: `_call_codex_for_ledger/_draft/_review/_questions/_cards/_script`, `run_bounded_process`, `resolve_trusted_executable`, and call `_generate_ledger(codex_executable=..., environment=..., pack=...)` style signatures. Every such signature changes. Tests worth keeping verbatim in intent: prompt content, traceability and guard behavior, critic-gate blocking, supplemental-question selection, duplicate-prompt deck error, execution-mode echo stripping.
- The "scratch cwd, not the repository root" tests (`test_call_codex_for_*_runs_with_a_scratch_cwd_not_the_repo_root`) become adapter contract tests (Q6).
- `tests/test_cli.py`: `TestCodexScriptPhases` (asserts the child command equals `[sys.executable, "scripts/<file>", lecture_id]` and `captured["env"] == {name: os.environ[name] for name in _CODEX_PHASE_ENV_KEYS if name in os.environ}`) and `TestCodexStageRootsMatchScriptDefaults` (pins `DEFAULT_*_ROOT` constants to the orchestrator constants; those script constants must stay). `test_rejects_the_removed_llm_model_flags` stays valid (config is not a CLI flag).
- CI precedent for CI-without-codex: commit `bc9b26a` changed one drafting test to stub `resolve_trusted_executable` so it passes where `codex` is absent. After the migration, script-level `_run` tests inject a fake gateway instead of stubbing a resolver.

### Minimal migration shape
- Each script keeps its prompt builders, root constants, argparse (including `--codex-executable`, kept as an override of `codex.executable` from the configuration), domain validation and writers. It deletes `_SUBPROCESS_TIMEOUT_SECONDS`, `_MAXIMUM_PROCESS_OUTPUT_BYTES`, the `_call_codex_*` and `_run_codex_*`/retry wrappers, the HOME restoration, and the `tempfile` use.
- `_run` builds the gateway after its existing gates, passes `gateway` (not `codex_executable`/`environment`) down, and calls `complete_json` with a `parse` lambda built on `parse_llm_json_response(..., response_adapter=..., declared_type=...)` and a `validate` callable.
- The podcast keeps `_strip_execution_mode_echo` inside its `parse` callable and the post-parse steps inside `validate`.

### Safest order
1. `llm/` package and the whole contract suite with stub binaries (no script changed; both adapters pass).
2. Semantic (smallest; the only script whose domain error is retried) as the pilot.
3. Drafts and critic together (critic imports drafts' function; one commit pair, one test file pair).
4. Exam and flashcards (structural twins; do them in one plan to keep the shared guard/count logic consistent).
5. Podcast last (largest prompt, error-wrapping change, echo stripping).
6. Orchestrator preflight and env forwarding; registry flag; sidecar; ADR and doc amendments.
Steps 2, 3, 4, 5 touch disjoint files and can run in parallel worktrees once step 1 is merged; step 3 is the only intra-step dependency.

## Q2. llama.cpp at the pinned commit

All claims below were read from raw source at `91d2fc387529940230555abd297a8b5e99737d3f` this session (not executed). No `llama-*` binary exists on this machine (`engines/` is absent), so every runtime claim stays unverified until the D-07 manual run.

### Binary choice (changes D-04's wording)
- `tools/cli/cli-context.cpp` (lines 100-134) either connects to `--server-base` or does `server.emplace(); server->start(params)`; `tools/cli/cli-server.h` documents `// spawn llama-server in a thread and interact with it via a random port` and `address()` returns `"http://127.0.0.1:" + std::to_string(port)`. `tools/cli/CMakeLists.txt` links `llama-server-impl`. `run()` prints an ASCII logo banner (`LLAMA_ASCII_LOGO`), a command list, echoes the user prompt (`user_turn.echo`), prints timings and `"\n\nExiting..."`.
- `tools/completion/` builds `llama-completion` (links only `llama-common` and `llama`), is added unconditionally (`tools/CMakeLists.txt`), and is the non-server tool. `tools/tokenize` builds `llama-tokenize`.
- Consequence: with `llama-cli`, the "no listener" claim (D-04, D-09) is false (loopback listener on a random port), the banner and the echoed prompt (which contains the embedded JSON Schema) reach stdout, and `parse_llm_json_response` would see several JSON candidates and raise `ambiguous_json`. With `llama-completion` none of that applies.
- `scripts/01_provision_engines.sh` already builds both through the default `cmake --build` (only `llama-cli` is checked by `build_if_stale`, so the script does not verify the other two exist; add a check or tolerate).

### Flags for the D-04/D-09 contract (llama-completion)
| Purpose | Flag | Evidence (pinned commit) |
|---|---|---|
| Completion cap (mandatory, ADR 0020) | `-n N` / `--n-predict` (default `-1` infinite; `-2` until context full) | `common/arg.cpp:1460-1469`; env `LLAMA_ARG_N_PREDICT` |
| Context size | `-c N` (`0` = from model) | `arg.cpp:1449-1457` |
| Prompt delivery | `-f FILE` (strips exactly one trailing `\n`); `-p` takes argv | `arg.cpp:1603-1613`; use `-f` from the scratch dir, never argv |
| Escapes | `--no-escape` required: `params.escape` defaults to `true` and `string_process_escapes(params.prompt)` runs after parsing | `common.h:564`, `arg.cpp:764-765`, flag at `arg.cpp:1653-1659` |
| Single turn | `-st, --single-turn` ("run conversation for a single turn only, then exit when done; will not be interactive if first turn is predefined with --prompt") | `arg.cpp:1715-1722`; `completion.cpp:406-409` sets `interactive=false` when `single_turn && !prompt.empty()` |
| Conversation mode | auto-enabled when the GGUF carries a chat template (`completion.cpp:211-219`); then the prompt is wrapped by the model's template (`completion.cpp:291-296`); `-no-cnv` instead passes the prompt raw (`completion.cpp:313-315`) | see Open Question 1 |
| Stop condition | EOG token prints `" [end of text]\n"` to stdout and breaks (`completion.cpp:969-972`) | parser tolerates it when exactly one valid JSON candidate exists |
| Prompt echo | `--no-display-prompt` | `arg.cpp:1302-1309`; `completion.cpp:554` |
| Sampling | `--temp T`; `--repeat-penalty P` (default 1.00 = off); `--repeat-last-n N` (default 64) | `arg.cpp:1817`, `1880`, `1892`; README defaults |
| Thinking models | `-rea, --reasoning on|off|auto` applies to `LLAMA_EXAMPLE_COMPLETION` | `arg.cpp:3407-3424` |
| Subprocess IO | `--simple-io` | `arg.cpp:3524-3530` |
| Offline | `--offline` ("forces use of cache, prevents network access"), env `LLAMA_ARG_OFFLINE`; when set the download tasks are skipped (`arg.cpp:589`) | `arg.cpp:3628-3634` |
| Logs | level NONE (generation) to stdout, everything else to stderr; do NOT pass `--log-disable` (it would hide the stderr diagnostics a failure needs) | `common/log.cpp:83-92` |
| Prompt too long | `if (embd_inp.size() > n_ctx - 4) { LOG_ERR("%s: prompt is too long ...") return 1; }` | `completion.cpp:342-345` |
| Token count | `llama-tokenize -m M -f F --no-escape --ids --show-count` prints `Total number of tokens: N`; `-f` honours escapes unless `--no-escape` | `tools/tokenize/tokenize.cpp:106-130,213-215`, `arg.cpp:2971-3003` |

### Download surfaces to reject and neutralize (D-09)
Argv: `-m` accepts any string; `-mu/--model-url`, `-dr/--docker-repo`, `-hf/-hfr/--hf-repo`, `-hff/--hf-file`, `-hft/--hf-token`. Env: `LLAMA_ARG_MODEL`, `LLAMA_ARG_MODEL_URL`, `LLAMA_ARG_DOCKER_REPO`, `LLAMA_ARG_HF_REPO`, `LLAMA_ARG_HF_FILE`, `HF_TOKEN` (`arg.cpp:2786-2840`). A child env built from an explicit allowlist contains none of these. The adapter must also refuse a model path that is not an existing regular file (a URL or `org/model` string fails `is_file()`), and pass the absolute resolved path so it can never begin with `-`.

### Stale text in the ADRs for this pin (do not copy forward)
- ADR 0022: "`--single-turn` ... llama-cli" and the claim that the old parser is "tolerant of banner/echoed-prompt noise": the current parser rejects multiple candidates, and `llama-cli` now echoes the prompt. The "Operational amendment (2026-07-28)" is the correct parser contract.
- ADR 0020: the table of `-c/-n` is the historic fixed-window decision; its 2026-07-28 erratum (smallest fitting window from per-stage pairs, `llama-tokenize` preflight, Qwen2-only envelope, `llama-completion`, escape processing disabled) is the closest prior art for this adapter. Exams and podcast rows were whole-lecture budgets; the Codex exam script is now per unit and flashcards have no history.
- ADR 0034: values (1.3 / 256) and temperatures (0.0 for semantic and critic, 0.2 for drafting, exams and podcast) remain the stated contract; defaults it quotes (1.00 and 64) match the pin's README.
- `scripts/01_provision_engines.sh:7-11` header and the unused `LLM_MODEL_SHARDS` array (Qwen2.5-14B-Instruct Q4_K_M, three shards with SHA-256 values) are leftovers of the removed route; the array is a ready-made, already pinned example model for D-06 (license not verified here, A7).

## Q3. How configuration reaches the child processes

Facts:
- `_CODEX_PHASE_ENV_KEYS = ("HOME","LANG","LC_ALL","LC_CTYPE","PATH","TEMP","TMP","TMPDIR")` and `_codex_phase_environment()` returns only those present in `os.environ` [VERIFIED: orchestrator.py:103-122]. `run_bounded_process` additionally applies the credential blocklist to whatever dict it receives [VERIFIED: process_lifecycle.py:176].
- The blocklist rejects names with prefixes `PORTAL_`, `LECTURE_STUDY_QUALITY_` and suffixes `_API_KEY`, `_COOKIE`, `_CREDENTIALS`, `_PASSWORD`, `_SECRET`, `_SESSION`, `_TOKEN` [VERIFIED: process_environment.py:34-46]. `LECTURE_STUDY_LLM_ADAPTER` and `LECTURE_STUDY_LLM_CONFIG` pass.
- The child command is exactly `[sys.executable, "scripts/<file>", lecture_id]` and tests pin it.

Recommended mechanism (keeps the command unchanged and the allowlist explicit):
1. Rename `_codex_phase_environment` to a neutral name (for example `_llm_phase_environment`) and add exactly two keys: `LECTURE_STUDY_LLM_ADAPTER` (single-run override) and `LECTURE_STUDY_LLM_CONFIG` (path of the YAML, only when non-default). The per-stage choice lives in the YAML, so forwarding the path forwards the per-stage choice. No other ambient variable is added.
2. One resolver function (`llm.config.resolve_gateway_configuration(environ, path)`) is used by the CLI preflight and by every script, so the two validation layers cannot drift (D-12).
3. The default YAML path is a module constant `Path("config/llm-gateway.local.yaml")`, resolved against the cwd like every other `config/*.local.yaml` (the children already inherit the repo-root cwd). A missing file means "defaults": adapter `codex`, which the egress policy still gates. A present but invalid file fails closed.
4. Update `tests/test_cli.py::TestCodexScriptPhases` (equality assertion on `captured["env"]`, plus the `<= _CODEX_PHASE_ENV_KEYS` subset check) and add one test that the two new keys are forwarded and that an ambient `OPENAI_API_KEY`-style variable is not.
5. Codex adapter env = sanitized plus `HOME`. Llama adapter env = sanitized plus `LLAMA_ARG_OFFLINE=1`; it gets no `HOME` (not needed for a local file; unverified for Metal shader caching, covered by the manual run).
6. Precedence (Open Question 2): recommended rule is that the env override replaces the default adapter AND per-stage entries for that run, so `LECTURE_STUDY_LLM_ADAPTER=llama-cpp` can never leave a stage silently on Codex; the sidecar records the resolved adapter per stage.

Preflight placement: at the top of `run_all`, `run_phase` and `run_personal_study` (they receive `lecture_id`), computing the LLM stages from the phases that will actually run via an explicit map `{5: SEMANTIC, 6: DRAFTING, 7: CRITIC (+DRAFTING), 13: EXAMS, 14: PODCAST}` intersected with `PHASE_REGISTRY` and the entry point's phase set (`PERSONAL_STUDY_PHASE_NUMBERS = (8, 9, 13, 14, 15)` gives only 13 and 14). Errors map to `PipelineExecutionError` so `main()` exits 1 (it catches `RuntimeError`/`ValueError`) [VERIFIED: main.py:98-110]. Existing orchestrator tests patch `PHASE_REGISTRY` with probing specs; because the stage set is derived from the registry actually run, non-LLM test registries do not trigger the policy.

## Q4. Where `remote_llm_allowed` lives (options; not decided)

Facts:
- `LocalLecture` is `ConfigDict(strict=True, extra="forbid", frozen=True)` with fields `lecture_id, title, discipline, section, scheduled_date, publish: bool = False, kind` [VERIFIED: local_lectures.py:83-94]. A new `remote_llm_allowed: bool = False` is therefore also the fail-closed default for every existing YAML entry, and `extra="forbid"` means the key is rejected until the model declares it.
- `register_local_lecture` idempotence compares `existing.model_copy(update={"publish": lecture.publish}) != lecture` and refuses with "id ... ja em uso (aulas locais)" otherwise [VERIFIED: local_lectures.py:338-339]; the new field must be added to that `update` (or a rerun that only changes the flag is refused). `_upsert_local_lecture` writes via `model_dump()`, so the key is persisted automatically. `scripts/register_local_lecture.py` needs a `--remote-llm-allowed` flag.
- Portal catalog lectures (`AcademicLesson`, strict/extra-forbid, fields `activity_id, moodle_order, section, display_title, url, enabled, discovery_timestamp` [VERIFIED: academic_catalog.py:76-87]) live in `config/targets.local.yaml`, are not in `config/local-lectures.local.yaml`, and have ids disjoint from the reserved local ranges `925100-925199`, `925200-925299` (`is_reserved_local_id`, enforced at registration by the `portal_ids` collision check). The gateway can therefore classify a lecture id as local (`is_reserved_local_id`) or portal without any new data.
- `config/local-lectures.local.yaml` does not exist in the repository; Phase 7's demo lecture will be the first registry the repository ships.

| Option | Where the flag for a portal lecture lives | Pros | Cons |
|---|---|---|---|
| O1: field on `AcademicLesson` as well | `config/targets.local.yaml` | One boolean per lecture beside its metadata; mirrors `publish` | Touches a second strict model and `generate_academic_catalog.py`/site generator readers; the example catalog and the docs change; `Dockerfile.site` copies only `site/` and `domain/models.py`, so `AcademicLesson` (study_materials) is not in the image, still must check no site import |
| O2: a policy list in the gateway YAML | `config/llm-gateway.local.yaml`: `remote_llm_allowed_lecture_ids: [...]` for ids outside the registry | Single operator-owned file already read by the gateway; no change to catalog schemas; works for any id | A second place to look for the flag (registry for local lectures, gateway file for portal ones); stale ids possible |
| O3: all lectures, local and portal, flagged only in the gateway YAML | gateway YAML | One source of truth; `LocalLecture` unchanged | Contradicts D-14 ("the lecture registry gains a per-lecture field"); registration CLI cannot record it |
| O4: portal lectures implicitly refused for Codex (flag impossible) | nowhere | Simplest, most conservative | Codex unusable for portal lectures, which are the project's main existing flow (10001-style ids); contradicts the "default stays Codex" intent |
| O5: portal lectures implicitly allowed | nowhere | Zero operator work | Fail-open, contradicts D-15 |

Common implementation points regardless of option: the `RemoteEgressPolicy` Protocol lives in `llm/` (`is_remote_allowed(lecture_id) -> bool`); the implementation lives in `study_materials/remote_llm_policy.py` and reads `load_local_lectures()` (and the chosen portal source); the factory refuses `CodexCliAdapter` construction when the policy says no (so refusal happens before any call and also at preflight); an unreadable registry is a refusal, not a pass. The demo catalog in Phase 7 sets the flag explicitly.

Operator consequence to document and test: after the change every existing registry entry is refused for Codex until flagged (D-15); `tests/test_local_lectures.py` fixtures that build `LocalLecture` keep working because the default is `False`, but any test that expects a Codex-reachable lecture must set the flag.

## Q5. Stage 18 (personal-study flashcards)

- `PHASE_REGISTRY` covers phases 2-15 only; `PERSONAL_STUDY_PHASE_NUMBERS = (8, 9, 13, 14, 15)`. Stage 18 is run by hand: `python scripts/generate_codex_personal_study_flashcards.py <lecture_id>` (ADR 0045 section 5; D-ADR0045-05) [VERIFIED: orchestrator.py:552-581; ADR 0045 lines 156-163].
- Gateway wiring therefore means: the script builds its own gateway for stage `flashcards` from the same resolver (env override or default YAML path), checks the egress policy for its lecture, and writes its sidecar. It receives no env from the orchestrator, so the standalone path must work with only the environment the operator exported plus the default config path. The CLI preflight does not cover it; the script's own pre-first-call validation (after its critic gate, before codex/llama is resolved) is the whole of D-12 for this stage. The success criterion text "all selected stages" should be read as stages selected for the orchestrator's entry point.
- Stage name collision: the key `flashcards` in the YAML is stage 18's; the deterministic Anki phase 12 never calls a gateway.

## Q6. Contract tests (GW-06) and stub binaries

Existing patterns: script tests patch `run_bounded_process` and write the response file from the fake (`args[args.index("-o") + 1]`); the vlm tests patch `asyncio.create_subprocess_exec` and `resolve_trusted_executable` (`tests/test_captioning.py:116-145`); there is no existing real-process stub. `tests/` has no `__init__.py` and no shared helper module, so shared fixtures go in a `conftest.py` and test file basenames must be unique.

Design:
1. **Stub executables written at test time** into `tmp_path` by a fixture, each a small script `#!<sys.executable>` marked `0o755`. Create two distinct files (`llama-completion`, `llama-tokenize`) and one `codex`; do NOT rely on symlinked names, because `resolve_trusted_executable` resolves symlinks to the target file and `argv[0]` would lose the symlink name [VERIFIED: process_environment.py:80-85].
2. **Scripted behavior** without extra env (the adapter env is an allowlist): the stub reads a queue file stored next to the model file (llama) or next to the executable (codex) and appends each call's `argv`, relevant `os.environ` keys and prompt bytes to a JSON-lines call log in the same directory. Test assertions read the log.
3. **Modes:** write a canned response to stdout plus ` [end of text]` (llama) or to the `-o` path (codex); exit non-zero; sleep past the timeout; emit more than the output cap; print the prompt echo (to prove the contract tolerates or the adapter forbids it); exit 1 with `prompt is too long` on stderr; refuse and exit 3 if any download flag or `LLAMA_ARG_HF_REPO`-style variable is seen (the tripwire for D-09).
4. **Shared parametrized suite** over `("codex", "llama_cpp")` via a fixture that returns a configured gateway plus a "script the next response(s)" function: raw response bytes returned verbatim; `complete_json` round trip for an object type and an array type; first failure then success sends `Retry feedback:` in the second prompt and `is_retry=True`; second failure propagates; nonzero exit, timeout and output-cap overflow raise typed errors whose message never contains the prompt; scratch cwd differs from the repository root and is empty at start; process group isolation inherited from `run_bounded_process`; a typed prompt-too-large error for an oversized prompt (llama: token count above budget; codex: bytes above the safe argv bound).
5. **Adapter-specific tests** (not parametrized): llama argv asserts the exact flag set and absence of every download flag and of `-p`; env asserts `LLAMA_ARG_OFFLINE=1`, no `HF_*`, no other `LLAMA_ARG_*`, no `HOME`; retry adds `--repeat-penalty 1.3 --repeat-last-n 256` and the first attempt does not; stage table picks `-n`/`-c`/`--temp`; model path validation rejects URL, `org/model`, directory, missing file, leading `-`. Codex argv asserted byte for byte against Pattern 2, HOME present, credential-like variables absent.
6. **Critic repair loop through both adapters (GW-06):** import the critic script like the sibling tests do and drive `_evaluate_with_repair` with a gateway over each stub and a scripted sequence review(fail), draft, review(pass); assert attempts, the feedback section reaching the redraft prompt, and the final review.
7. **Speed:** a Python stub starts in tens of milliseconds; keep the parametrized matrix to a few dozen real spawns.
8. **CI:** `.github/workflows/ci.yml` already runs `uv run pytest -q -rs` on `macos-15`; no workflow change is required because the stubs are created inside the test run. Contract tests must not skip silently (`-rs` prints skips).

## Q7. Dependency direction and layering

- `ARCHITECTURE.md` states `site/` "deliberately does not import the processing stages" and that `Dockerfile.site` copies only `src/lecture_study/site/` and `domain/__init__.py` plus `domain/models.py`; any new import from another package breaks the container [VERIFIED: ARCHITECTURE.md:101,194; Dockerfile.site lines 29-31]. `llm/` must never be imported by `site/`. No `Dockerfile.site` change is needed; do not add `llm/` to its copy list. There is no existing import-boundary test (only `tests/test_site_image.py`, which checks the USER instruction).
- Recommended guard: `tests/test_llm_layering.py`, an `ast` scan asserting (a) no module under `site/` imports `lecture_study.llm`, (b) no module under `ops/` imports `lecture_study.llm`, (c) modules under `llm/` import only `domain`, `quality.prompt_budget`, `ops.process_lifecycle`, `ops.process_environment`, `ops.llm_response_contract` and each other, (d) `gateway.py`, `errors.py`, `config.py`, `egress.py` import no adapter module.
- `ops -> quality` already exists; `llm -> quality.prompt_budget` is consistent with it. Importing `lecture_study.quality.prompt_budget` executes `quality/__init__.py` (large); scripts already import `quality.student_facing_text`, so there is no new cost for scripts, but the site must stay clear.
- Mypy runs on `src` only, strict, no `type: ignore` (CONVENTIONS); tests are not type-checked [VERIFIED: ci.yml `uv run mypy src`].
- Update `ARCHITECTURE.md`'s layer list (the `ops` description) when the phase completes; that file is regenerated by the map-codebase workflow, so a note in the handoff is enough.

## Q8. The new ADR and amendments (GW-05)

- Next free number: **0046** (files run `0001`..`0045`; `0017`-`0019` are intentionally absent per ADR 0044, so they are not reusable) [VERIFIED: directory listing; ADR 0044 status paragraph].
- File name convention: `docs/decisions/0046-<kebab-title>.md`; Status first (`Accepted. <date>. Amends ADR 0038 ... Closes ADR 0045 section 2 ...`), then Context, Decision, Alternatives considered, Consequences (format of ADR 0045 and 0038).
- Content the ADR must carry:
  1. The `LlmGateway` port, the two layers (port plus `complete_json`), package location and dependency rule (D-01..D-03).
  2. Adapter list, selection by YAML plus environment override, per-stage overrides, fail-closed preflight and script re-validation, resolved precedence (D-10..D-12; Open Question 2).
  3. Local adapter contract, with the binary correction (`llama-completion`, not `llama-cli`) and the full flag set, `--no-escape`, `--no-display-prompt`, offline posture and what "no network egress" means and does not mean (no OS sandbox; D-09), the typed prompt-too-large error with no truncation or chunking (D-08), stage budgets and temperatures, retry-only repeat penalty (ADR 0034 carried forward).
  4. The example tested model and its SHA-256 (D-06) and the manual real-model run record (D-07); no quality claims.
  5. The egress rule as code: field `remote_llm_allowed`, fail-closed default, where the flag lives for portal lectures (Q4 decision), who verifies patient-data absence (still the operator), provider retention and training terms still a procedural check (D-14..D-16).
  6. Provenance sidecar: location and fields (D-13). Phase 7 reads it for per-stage schema pass rate, first-attempt failures and wall time, so recording `calls`, `first_attempt_failures` and `wall_seconds` per stage is recommended here (A8).
  7. Amendments to GEN-07 and the explicit statement that the generator and evaluator independence question left by ADR 0045 section 4 is still open when one adapter serves both.
  8. Behavior changes made on purpose: typed call errors replacing raw `CalledProcessError`, podcast no longer retrying a failed process.
- Text that must be amended (older records get a forward-pointing amendment note, never a rewrite, per ADR 0045 section 6):
  - `docs/decisions/0038-...md`: add to Status an amendment line (local text-LLM route returns as a gateway adapter; decisions 7-9 "leaving local execution only for Whisper/Kokoro/vision" no longer exhaustive).
  - `docs/decisions/0045-...md`: Status line plus a note under section 2 and under the Consequence "the egress rule in section 2 is procedural, not enforced by code".
  - Optional but consistent: short pointers in the Status of ADRs 0020, 0022 and 0034 (they say "historical record ... route deleted"; the contract is rebuilt, not restored) and 0035/0036/0037 (Codex scripts now call the gateway).
  - `.planning/REQUIREMENTS.md`: GEN-07 (line 45) and the Out of Scope row (line 177) amended by GW-05; GW-03 wording ("restored from the repository history") reworded to match D-05.
  - `.planning/PROJECT.md`: D-READ-01, D-ADR0038-01, D-ADR0045-02 (lines 135, 171, 192) get a revision note.
  - Code comments/docstrings that become false: `scripts/generate_codex_drafts.py` docstring lines 20-24 (`--llm-model-path`), the "local qwen3-32b/llama.cpp" lines in the semantic and critic docstrings, `scripts/01_provision_engines.sh` header and the dead `LLM_MODEL_SHARDS` handling.
- Success criterion 4 ("ADR Accepted, ADR 0038 and 0045 carry amendment notes") is mechanically checkable (see Validation Architecture).

## Common Pitfalls

### Pitfall 1: Using `llama-cli` as written in D-04
**What goes wrong:** A listener starts on `127.0.0.1:<random>`, stdout carries a logo, the echoed prompt (with the whole JSON Schema) and "Exiting...", and the parser raises `ambiguous_json`.
**Why it happens:** Since the CLI/server split, `llama-cli` is a chat client over an embedded server (Q2).
**How to avoid:** `llama-completion` with `--single-turn --no-display-prompt`. Contract test: the stub prints a prompt echo and the adapter must still be built so that real llama-completion would not (argv contains `--no-display-prompt`).
**Warning signs:** `ambiguous_json` on a first manual run; a Metal process holding a TCP port.

### Pitfall 2: Prompt escapes mangled
**What goes wrong:** `\n` and `\"` inside the embedded JSON Schema are rewritten, the model sees a different schema.
**How to avoid:** `--no-escape` on both `llama-completion` and `llama-tokenize`; assert it in the argv test.

### Pitfall 3: argv limit on the podcast prompt (Codex adapter)
**What goes wrong:** `Popen` raises `OSError: [Errno 7] Argument list too long` for a prompt near 1 MiB; the podcast script's `except` list does not include `OSError`, so it surfaces as a traceback. The whole-lecture prompt concatenates every unit body (one real lecture had 93 packs).
**Evidence:** macOS `kern.argmax` is 1048576 (`getconf ARG_MAX`); a probe with `/bin/echo` succeeded at 1,048,000 bytes of argument and failed with `Errno 7` at 1,100,000.
**How to avoid:** the Codex adapter measures `len(prompt.encode()) + environment size + executable/flag bytes` against a safe bound (for example 900 KiB, a recommendation, A5) and raises `PromptTooLargeError` before spawning. Do not switch delivery to stdin in this phase: `run_bounded_process` hard-codes `stdin=subprocess.DEVNULL`, and GW-02 says preserve behavior (`codex exec` does accept `-`/stdin, `codex exec --help`, so this is a follow-up option).

### Pitfall 4: Sidecar placed inside a stage output directory
**What goes wrong:** `scripts/generate_progress_dashboard.py::_count_entries` returns `sum(1 for _ in directory.iterdir())`, so one extra file in `07_semantic_ledgers/<id>`, `08_drafts/<id>` or `09_validated_units/<id>` can flip `semantic_complete`/`drafts_complete`/`critic_complete`; `quality/delivery.py` fingerprints (`os.listdir`) and recursively removes output directories when it rewrites them (podcast and exam use `write_delivery_outputs`). Resume predicates use prefix globs (`ledger_*`, `draft_*`, `unit_*`, `pack_*`) and are safe.
**How to avoid:** write `workspace/data/llm_runs/<lecture_id>/<script_stage>.json`, atomically, outside every artifact directory. This is "beside" in spirit; D-13 leaves the location to the planner.

### Pitfall 5: Existing tests assert exact orchestrator env equality
**What goes wrong:** adding forwarded keys breaks `TestCodexScriptPhases` (`captured["env"] == {...}` and `<= _CODEX_PHASE_ENV_KEYS`).
**How to avoid:** update the constant and the assertions in the same plan as the forwarding change.

### Pitfall 6: Resolving the model or the executable before the critic gate
**What goes wrong:** blocked lectures now fail with a configuration error instead of the ADR 0045 block message, and tests that assert "never resolves codex" fail.
**How to avoid:** call the gateway build after `load_critic_approved_drafts` and lecture-id validation in each script.

### Pitfall 7: `LocalLecture` rerun comparison and demotion
**What goes wrong:** a rerun of `register_local_lecture.py` that only toggles `remote_llm_allowed` is rejected as "id already in use".
**How to avoid:** extend `model_copy(update=...)` in `_require_registrable`; adding permission may be allowed on rerun; removing it is always safe.

### Pitfall 8: Reasoning models and the single-candidate parser
**What goes wrong:** a thinking model emits `<think>` text containing braces; the parser sees extra spans.
**How to avoid:** `--reasoning off` in the argv; the manual run (D-07) must use an instruct model; record the family in the run record.

### Pitfall 9: Retry-only repeat penalty keyed on the wrong signal
**What goes wrong:** applying `--repeat-penalty` to critic-feedback redrafts (which are not parse retries) or to every attempt changes first-attempt reproducibility (ADR 0034).
**How to avoid:** `LlmRequest.is_retry` is set only by `complete_json` for its own retry.

## Code Examples

### Stub executable (test fixture, illustrative)
```python
# Source: pattern derived from process_environment.resolve_trusted_executable semantics (Q6)
def write_stub(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    path.chmod(0o755)
    return path
```
The body appends `{"argv": sys.argv[1:], "env": {k: v for k, v in os.environ.items() if k.startswith(("LLAMA_", "HF_", "HOME"))}}` to a call log beside the model file, reads the next canned response from a queue file, prints it plus `" [end of text]\n"`, and exits with the scripted code.

### Config shape (`config/llm-gateway.example.yaml`, relative placeholder paths only)
```yaml
default_adapter: codex          # codex | llama-cpp
stages:                         # optional per-stage overrides
  critic: llama-cpp
codex:
  executable: codex
llama_cpp:
  completion_executable: engines/llama.cpp/build/bin/llama-completion
  tokenize_executable: engines/llama.cpp/build/bin/llama-tokenize
  model_path: engines/models/example-instruct-q4_k_m.gguf
  model_family: qwen2
```
Stage keys: `semantic`, `drafting`, `critic`, `exams`, `podcast`, `flashcards`. The leak gate scans staged content against a private denylist, so no absolute home path may appear in any committed file.

### Stage budget table (llama adapter; values are recommendations, A4)
| Stage | `-c` | `-n` | `--temp` | Source |
|---|---|---|---|---|
| semantic | 12288 | 2048 | 0.0 | ADR 0020 erratum (larger supported pair); ADR 0034 temperature |
| drafting | 12288 | 2048 | 0.2 | same |
| critic | 12288 | 1024 | 0.0 | same |
| exams | 8192 | 4096 | 0.2 | ADR 0020 table (whole-lecture era; per unit now) |
| podcast | 8192 | 4096 | 0.2 | ADR 0020 table; whole lecture prompt will usually exceed the window and raise `PromptTooLargeError` by design |
| flashcards | 8192 | 2048 | 0.2 | no history |

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `llama-cli` as a one-shot completion tool (ADR 0020/0022) | `llama-completion` for scripted completion; `llama-cli` is a chat client over an embedded server | by the pinned commit (2026-07-20); first reflected in the repo by the ADR 0020 2026-07-28 erratum | Adapter binary choice (Q2) |
| `--json-schema-file` structured output | schema embedded in prompt text, strict post-parse | ADR 0022 | Keep; upstream grammar defect was open at the time |
| Fixed per-module `-c/-n` | smallest fitting window from tokenizer count | ADR 0020 erratum | Reuse `PromptBudget` |

**Deprecated/outdated:** `--llm-model-path` / `--llm-model-family` CLI flags stay removed (configuration replaces them); the `LLM_MODEL_SHARDS` array in the provisioning script.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `llama-completion --single-turn -f <prompt>` applies the GGUF's own chat template (conversation mode auto-enables when the template exists) and prints only the answer with `--no-display-prompt`; behavior on a GGUF without a template is raw continuation | Q2, Open Question 1 | Wrong output shape on the first real run; mitigated by the D-07 manual run and by the `-no-cnv` plus family-envelope fallback |
| A2 | `HF_HUB_OFFLINE=1` is harmless but unused by llama.cpp; `LLAMA_ARG_OFFLINE` plus `--offline` is the effective guard | Pattern 3, Q2 | None for security (no `HF_*` is passed); only cosmetic |
| A3 | The llama adapter works without `HOME` on Apple Silicon Metal | Q3 | Child fails to start; fix by passing `HOME`; found in the manual run |
| A4 | The stage budget table values (ADR 0020 erratum pairs for semantic, drafting, critic; table values for exams and podcast; invented values for flashcards) are adequate | Code Examples | Truncated output (`-n` too small) or memory pressure (`-c` large); config-overridable |
| A5 | A 900 KiB safe bound for Codex argv plus env is a good guard (hard limit measured at about 1.05 MB here) | Pitfall 3 | Too low rejects workable podcasts; too high allows E2BIG |
| A6 | A podcast timeout override of a few multiples of 900 s is needed | Q1 | Too small still times out on dense lectures |
| A7 | Qwen2.5-14B-Instruct Q4_K_M (the model whose three shard SHA-256 values remain in the provisioning script) is a suitable "tested example model" and its license permits this use | Q2 | The ADR would document an unverified or unsuitable example |
| A8 | Phase 7 (DEMO-03) wants per-stage first-attempt failure counts and wall time from the sidecar | Q8 | Extra fields are cheap to drop |
| A9 | A GGUF split into shards loads when `-m` points at the first shard | Q2 | Provisioning/validation of the example model needs a shard-aware check |

## Open Questions

1. **D-04 binary and prompt-template mode (needs operator confirmation).**
   - Known: `llama-cli` at the pin embeds a loopback server and echoes the prompt; `llama-completion` has the needed flags and no listener.
   - Unclear: `--single-turn` (GGUF template applied by llama.cpp) versus `-no-cnv` plus an adapter-rendered family envelope (deterministic, history-aligned, needs a family table).
   - Recommendation: `llama-completion --single-turn` with `--reasoning off`; keep `model_family` as validated metadata; fall back to `-no-cnv` plus an envelope only if the manual run shows template trouble.
2. **Environment-variable precedence.** Does `LECTURE_STUDY_LLM_ADAPTER` replace per-stage entries too? Recommendation: yes (a run that says "local" must be entirely local).
3. **Critic redraft stage.** Use the `drafting` stage for redrafts inside stage 7? Recommendation: yes, with both stages preflighted.
4. **Podcast process-failure retry.** Keep the old retry-on-process-failure or move to uniform typed errors without retry? Recommendation: uniform, recorded in the ADR.
5. **Token counting method.** Exact `llama-tokenize` (second binary, model load per call) versus a conservative byte bound. Recommendation: `llama-tokenize`, with `PromptBudget`, as in the ADR 0020 erratum; the stub covers both binaries.
6. **Where the flag lives for portal lectures (Q4, O1-O5).** Not decided here; the planner needs the operator's choice.
7. **Sidecar location.** Recommended `workspace/data/llm_runs/<lecture_id>/<script_stage>.json`; confirm the departure from "beside the stage output".
8. **Example model and its provisioning.** Confirm the model, the download source and license, and whether provisioning stays an opt-in script step (it needs network by design, unlike runtime).

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python / uv | tests, scripts | yes | uv 0.13.0; project pins Python 3.12 in `.python-version` | - |
| `codex` CLI | real Codex runs | yes | 0.155.1 | Stub in tests |
| `llama-completion`, `llama-tokenize` | real local runs (manual run, D-07) | no (`engines/` absent) | pinned build per `01_provision_engines.sh` | Stub binaries in CI and unit tests |
| GGUF example model | manual run | no | - | Provision with SHA-256 check |
| macOS argv limit | Codex adapter bound | yes | `ARG_MAX` 1048576 | - |
| gitleaks and denylist hooks | commits | per `make hooks` | - | CI secret-scan |

**Missing dependencies with no fallback:** none for CI.
**Missing dependencies with fallback:** llama binaries and the model (needed only for the documented manual run).

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest `>=9.1.1`, pytest-asyncio, monkeypatch/tmp_path; mypy strict on `src`; ruff check and format |
| Config file | `pyproject.toml` (`testpaths = ["tests"]`) |
| Quick run command | `uv run pytest tests/llm_gateway -q -x` |
| Full suite command | `uv run pytest -q -rs` (CI) plus `uv run ruff check . && uv run ruff format --check . && uv run mypy src` |

### Phase Requirements to Test Map
| Req ID / criterion | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| GW-01 | Every script obtains its model output only through `LlmGateway.complete`; no script imports `run_bounded_process` or contains a `codex` argv | unit (fake gateway per script) + source scan | `uv run pytest tests/test_generate_codex_*.py -q` | rewrite (Wave 0) |
| GW-01 / Q7 | `site/` and `ops/` never import `llm/`; inner modules import no adapter | AST scan | `uv run pytest tests/test_llm_layering.py -q` | Wave 0 |
| GW-02 | Codex argv byte for byte, scratch cwd, HOME present, credentials absent, bounded limits, typed errors | adapter contract | `uv run pytest tests/llm_gateway/test_codex_adapter.py -q` | Wave 0 |
| GW-03 / D-09 | Llama argv has `--offline --no-escape --no-display-prompt -n`, no download flag, no `-p`; env has only `LLAMA_ARG_OFFLINE`; model path validation | adapter | `uv run pytest tests/llm_gateway/test_llama_cpp_adapter.py -q` | Wave 0 |
| D-08 | Oversized prompt raises `PromptTooLargeError` naming stage and sizes before `llama-completion` is spawned (call log shows only `llama-tokenize`) | adapter | same file | Wave 0 |
| Criterion 1 | With `LECTURE_STUDY_LLM_ADAPTER=llama-cpp` (or YAML), stages 5, 6, 7 on a fixture lecture complete using stubs while a tripwire `codex` on PATH is never called | integration (real child process through `run_phase`, or script `main`) | `uv run pytest tests/llm_gateway/test_stages_without_codex.py -q` | Wave 0 |
| GW-04 / criterion 2 | Unknown adapter, missing executable, missing model path: `cli_main.main([...])` exits 1 before phase 2 runs and before any spawn; each script returns non-zero on its own | unit/integration | `uv run pytest tests/llm_gateway/test_preflight.py -q` | Wave 0 |
| GW-04 | Default Codex without a config file; YAML per-stage override; env override wins; env forwarded to children, ambient credentials not | unit | `uv run pytest tests/llm_gateway/test_configuration.py tests/test_cli.py -q` | Wave 0 + edit |
| D-14/D-15 | Codex refused for an unflagged lecture (registry, portal, unreadable registry) before any call; flag round trip in `register_local_lecture` | unit | `uv run pytest tests/llm_gateway/test_egress_policy.py tests/test_local_lectures.py -q` | Wave 0 + edit |
| D-13 | Sidecar written atomically outside artifact directories; dashboard counts unchanged | unit | `uv run pytest tests/llm_gateway/test_run_record.py -q` | Wave 0 |
| GW-06 / criterion 3 | Shared parametrized suite over both adapters, plus critic repair loop through each | contract | `uv run pytest tests/llm_gateway/test_gateway_contract.py -q` | Wave 0 |
| GW-05 / criterion 4 | ADR 0046 exists with Status "Accepted"; 0038 and 0045 contain a pointer to it; GEN-07 amended | doc check | `rg -n "^Accepted" docs/decisions/0046-*.md && rg -l "ADR 0046" docs/decisions/0038-*.md docs/decisions/0045-*.md` (or a small pytest) | Wave 0 |
| Pitfall 3 | Codex adapter rejects a prompt above the argv bound before spawn | adapter | `test_codex_adapter.py` | Wave 0 |
| Wiring | Orchestrator and script root defaults still equal | existing | `uv run pytest tests/test_cli.py -k StageRoots -q` | exists |

### Sampling Rate
- **Per task commit:** the relevant quick command above.
- **Per wave merge:** `uv run pytest -q` and `uv run ruff check . && uv run ruff format --check . && uv run mypy src`.
- **Phase gate:** full suite green on the phase-final diff, then the R0.4 mechanism recorded in STATE.md (`/gsd-code-review` and `/gsd-secure-phase`, re-run after material fixes); then `/gsd-verify-work`.

### Wave 0 Gaps
- [ ] `tests/llm_gateway/conftest.py`: stub `codex`, `llama-completion`, `llama-tokenize` builders, scripted-response and call-log helpers, tripwire `codex`.
- [ ] Test files listed in the table above (unique basenames).
- [ ] Rewrite of the six `tests/test_generate_codex_*.py` private-name tests to the gateway shape.
- [ ] `tests/test_cli.py` env and preflight assertions updated.
- [ ] Manual-run record template for D-07 (stages 5, 6, 7 on one pack with the example model: binary build hash, model SHA-256, command lines, outcome; no quality numbers).

## Security Domain

### Applicable ASVS Categories
| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no (Codex login is the CLI's own) | - |
| V4 Access Control | yes (per-lecture remote-egress permission) | fail-closed policy checked at preflight and in the factory |
| V5 Input Validation | yes | strict pydantic config; model path must be an existing regular file; no URL/repo strings; lecture id already `SafeIdentifier` |
| V6 Cryptography | no new use; SHA-256 check of the example model in provisioning | `shasum -a 256` helpers already in the script |
| V12 Files/Resources | yes | scratch directories, atomic sidecar write, no path built from model output |
| V14 Configuration | yes | git-ignored local config, explicit env allowlist |

### Known Threat Patterns
| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Patient-identifying lecture content sent to the remote model | Information disclosure | `remote_llm_allowed` fail-closed gate; local adapter route |
| Silent download or outbound request from the local adapter | Information disclosure | `--offline`, `LLAMA_ARG_OFFLINE`, explicit env, argv and env tests, no download flags |
| Ambient credentials reaching a child | Information disclosure | explicit env allowlist plus sanitizer blocklist |
| Prompt echoed into diagnostics or traceback | Information disclosure | typed errors never render `cmd`; digests only (`llm_output_diagnostic`) |
| Prompt injection in lecture text steering a tool-using agent | Elevation of privilege | Codex keeps `--sandbox read-only` and a scratch cwd (the repository root holds `.env`); `llama-completion` has no tools |
| Option injection through a configured path | Tampering | resolve to an absolute path, never begin with `-`, list-form argv, no shell |
| Runaway or hung child | Denial of service | `run_bounded_process` timeout and caps, mandatory `-n`, process-group kill |
| Planted executable | Tampering | `resolve_trusted_executable` on every executable |
| Config tampering selects Codex for a flagged-off lecture | Elevation | policy evaluated independent of configuration; unreadable registry denies |

## Project Constraints (from CLAUDE.md)

No `./CLAUDE.md` or `.claude/CLAUDE.md` exists in the repository. The operator's global instructions apply and are binding on the plan:
- R0.1: no secrets, tokens, or key-like strings in any committed file (example config uses relative placeholder paths; the SHA-256 values in the provisioning script are public checksums with precedent).
- R0.2: no duplicate files or pages; replace in place (scripts are edited, not copied).
- R0.3: `clean-code` (and `clean-architecture`, since module boundaries and dependency direction change) must stay active through planning, implementation and review.
- R0.4: Code Review and Code Secure on the phase-final diff, re-run after any material fix; recorded for this repository as `/gsd-code-review` plus `/gsd-secure-phase`.
- Never assume, always ask: the Open Questions above need operator answers before they become locked plan decisions.
- Language: code, comments, ADRs, error messages in formal en-US without emojis; existing script console output is pt-BR and may stay as is.
- Repository conventions: `from __future__ import annotations`, module docstrings, frozen slotted dataclasses or strict pydantic, no `type: ignore`, no `print()` in `src/`, `_logger` per module, absolute imports.
- A leak-gate pre-commit hook scans staged content against a private denylist; no absolute home paths in committed files.

## Sources

### Primary (HIGH confidence)
- Repository files read this session: the six `scripts/generate_codex_*.py`, `src/lecture_study/ops/{process_lifecycle,process_environment,llm_response_contract,llm_instructions}.py`, `quality/prompt_budget.py`, `cli/{main,orchestrator}.py`, `study_materials/local_lectures.py`, `scripts/register_local_lecture.py`, `scripts/01_provision_engines.sh`, `captioning/vlm_worker.py`, `tests/test_cli.py` (relevant parts), `tests/test_generate_codex_semantic_ledgers.py`, `.github/workflows/ci.yml`, `Dockerfile.site`, `pyproject.toml`, `.gitignore`.
- ADRs 0020, 0022, 0034, 0036, 0038, 0044 (section 3), 0045; `.planning/` CONTEXT, REQUIREMENTS, ROADMAP, STATE, PROJECT, `codebase/{ARCHITECTURE,TESTING,CONVENTIONS}.md`.
- llama.cpp raw source at commit `91d2fc387529940230555abd297a8b5e99737d3f` (GitHub): `common/arg.cpp`, `common/common.h`, `common/log.cpp`, `tools/CMakeLists.txt`, `tools/cli/{cli.cpp,cli-context.cpp,cli-context.h,cli-server.h,CMakeLists.txt,README.md}`, `tools/completion/{completion.cpp,CMakeLists.txt,README.md}`, `tools/tokenize/tokenize.cpp`.
- Local probes: `codex --version`, `codex exec --help`, `getconf ARG_MAX`, `sysctl kern.argmax`, a Python `subprocess` E2BIG probe.

### Secondary (MEDIUM confidence)
- GitHub API commit and directory listings for the pinned commit (existence, date, tool directories).

### Tertiary (LOW confidence)
- None used as a basis for a recommendation; every unverified runtime behavior is in the Assumptions Log.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - no new dependencies; all helpers read in full.
- Architecture: HIGH - layering follows existing packages and was checked against the image and ARCHITECTURE.md.
- Pitfalls: HIGH for items 3-7 (reproduced or read in code); MEDIUM for 1, 2, 8 (source-level, not run).
- llama.cpp runtime behavior: MEDIUM - source-level only; the manual run (D-07) is the first real execution.

**Research date:** 2026-10-14
**Valid until:** 30 days, or until the llama.cpp pin in `scripts/01_provision_engines.sh` changes (re-read `tools/completion` and `common/arg.cpp` at the new commit).
