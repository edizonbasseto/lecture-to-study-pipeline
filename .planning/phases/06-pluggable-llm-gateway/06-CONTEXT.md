# Phase 6: Pluggable LLM gateway - Context

**Gathered:** 2026-10-14
**Status:** Ready for planning

<domain>
## Phase Boundary

Every LLM-backed stage (5 semantic ledgers, 6 drafts, 7 critic, 13 exam, 14 podcast, 18 personal-study flashcards) calls one `LlmGateway` port instead of spawning the Codex CLI directly. A `CodexCliAdapter` preserves current behavior and is the default adapter. A `LlamaCppAdapter` runs a local GGUF model with no network egress. The adapter is chosen by configuration. A new ADR amends ADR 0038 and closes ADR 0045 section 2. Requirements: GW-01..GW-06.

Out of this phase: chunking of oversized prompts, any change to artifact schemas, real-model quality numbers (Phase 7), and README text (Phase 8).

</domain>

<decisions>
## Implementation Decisions

### Port shape and layering
- **D-01:** The gateway has two layers. The port is a raw-text call (prompt in, response bytes out). A thin shared helper on top of any port performs the parse with `parse_llm_json_response` and the single bounded retry with `build_bounded_retry_prompt_section`. The six scripts keep prompt building and their domain-specific validation (for example traceability checks) and drop their own call, parse and retry boilerplate. The critic repair loop (ADR 0036) stays in its script.
- **D-02:** The port and adapters live in a new package `src/lecture_study/llm/`. The port is the inner element; the Codex and llama.cpp adapters are the outer ones. `scripts/` and `ops/` depend on `llm/`, never the reverse.
- **D-03:** Bounded-process limits (timeout 900 s, 16 MiB output cap), the read-only sandbox and the scratch working directory are owned by each adapter as defaults. Callers pass the prompt only. A per-call timeout override is allowed for the large podcast prompt.

### Local adapter
- **D-04:** `LlamaCppAdapter` invokes `llama-cli` once per call as a bounded process through `run_bounded_process`. No `llama-server`, no listener. The contract is rebuilt from ADR 0020 (mandatory `-n` completion cap, sized against the context budget), ADR 0022 (schema embedded as literal prompt text, `--single-turn`, no `--json-schema-file`) and ADR 0034 (repeat penalty on the feedback-carrying retry only).
- **D-05:** The llama.cpp text route does not exist in this repository's git history (history starts at `9322e91` without it). GW-03's "restored from the history" cannot be done from git. The adapter is written new from the three ADRs; `scripts/01_provision_engines.sh` already pins llama.cpp.
- **D-06:** The model is path-configured with no pinned default and no weights in the repository. The model path and family come from configuration. The new ADR documents one tested example model, and provisioning offers it with a SHA-256 check as done for the Whisper model.
- **D-07:** Done bar for the adapter in this phase: GW-06 contract tests with a stub binary in CI, plus one documented manual real-model run of stages 5, 6 and 7 on a pack. No quality numbers are produced here.
- **D-08:** When a prompt does not fit the local context window, the adapter raises a typed prompt-too-large error naming the stage and the sizes, before any model call. No truncation and no chunking.
- **D-09:** "No network egress" is shown, not claimed: the adapter accepts only a local model path, rejects URL and Hugging Face repo style arguments, sets offline environment variables, and a test asserts the argv and the environment. No OS sandbox.

### Selection and fail-closed configuration
- **D-10:** Configuration lives in a YAML file with an environment override. `config/llm-gateway.example.yaml` is versioned; the local copy is git-ignored, the same pattern as `config/targets.example.yaml`. An environment variable (name to be fixed at planning, expected `LECTURE_STUDY_LLM_ADAPTER`) overrides for a single run. The orchestrator forwards the resolved choice to child processes.
- **D-11:** A per-stage override is supported now: a default adapter plus optional overrides keyed by stage name (semantic, drafting, critic, exams, podcast, flashcards).
- **D-12:** Bad configuration is detected twice. The CLI preflight resolves and validates the configuration for all selected stages before the first phase runs. Each script re-validates before its first call. Checks: adapter name is known, executable and model path exist. Nothing is contacted. An unknown or misconfigured adapter exits non-zero before any model call (GW-04, success criterion 2).
- **D-13:** Provenance is a sidecar run record beside the stage output (adapter name and model identifier). Artifact schemas stay unchanged. — **Reversibility:** reversible — a sidecar file can be added or dropped without touching shared contracts.

### Egress enforcement (ADR 0045 section 2)
- **D-14:** Section 2 is closed by code, not by procedure. The lecture registry gains a per-lecture field `remote_llm_allowed`. The gateway refuses the Codex adapter for a lecture that does not carry `remote_llm_allowed: true`, before any call. Detecting patient-identifying data stays the operator's responsibility; the flag is the operator's recorded verification.
- **D-15:** Operator override: a lecture with no flag is remote NOT allowed (fail closed). The demo catalog sets `remote_llm_allowed: true` explicitly. Consequence: the configured default adapter stays Codex (GW-04), but a Codex run on an unflagged lecture is refused, so every existing registry entry needs the flag added to keep using Codex. — **Reversibility:** costly — flipping the default later changes behavior for every registered lecture and every test that assumes Codex is reachable.
- **D-16:** The new ADR amends ADR 0038 (local text-LLM route returns as a gateway adapter), closes ADR 0045 section 2 with the flag rule, amends baseline requirement GEN-07 (GW-05), and keeps the provider's retention and training terms as a procedural check.

### Amendments from research (operator-confirmed 2026-10-14)
- **D-17 (amends D-04):** At the pinned llama.cpp commit `llama-cli` embeds a loopback server and echoes the prompt, so it cannot satisfy D-04 (no listener) or D-09 and would break the JSON parser. `LlamaCppAdapter` uses `llama-completion` instead, one bounded process per call, with `--single-turn --no-display-prompt --no-escape --simple-io --offline -f <prompt file> -n <cap> -c <ctx>` and reasoning off. `--no-escape` is mandatory (the embedded schema text must not be rewritten). Fall back to `-no-cnv` plus an adapter-rendered family envelope only if the manual real run (D-07) shows chat-template trouble. The ADR 0020/0022/0034 contract is otherwise unchanged; `model_family` stays validated metadata.
- **D-18 (amends D-13):** The sidecar run record lives at `workspace/data/llm_runs/<lecture_id>/<script_stage>.json`, not beside the stage output: stage output directories are counted by the progress dashboard and fingerprinted by `quality/delivery.py`.
- **D-19 (amends D-14/D-15):** For lectures outside the local registry (portal catalog), the flag lives in the gateway YAML as `remote_llm_allowed_lecture_ids` (research option O2). Local-registry lectures carry `remote_llm_allowed` on `LocalLecture`. Unflagged in both places = remote refused.
- **D-20:** `LECTURE_STUDY_LLM_ADAPTER` overrides per-stage entries too: a run that says local is entirely local.
- **D-21:** Redrafts inside stage 7 use the `drafting` stage; both `critic` and `drafting` are preflighted.
- **D-22:** The podcast script's retry-on-process-failure is replaced by uniform typed call errors without retry; recorded in the new ADR.
- **D-23:** D-08 token counting uses exact `llama-tokenize` (with `PromptBudget`, as in the ADR 0020 erratum); the CI stub covers both binaries. The Codex adapter gets an argv-size guard that raises the typed prompt-too-large error before spawning (macOS `ARG_MAX` is 1048576; the podcast prompt can exceed it).
- **D-24:** Example model for the ADR and provisioning: Qwen2.5-14B-Instruct Q4_K_M; provisioning is an opt-in script step (it needs network by design, unlike runtime) with a SHA-256 check; its license and source are verified at execution time.

### Operator answers to plan checkpoints (2026-10-14, direct from operator)
- **D-25 (resolves OPEN-1):** No per-model-family mechanism. A generic `--reasoning off` on every local call suffices; its adequacy for thinking-style models is validated in the D-07 manual run, which is the operator's pending item when no local model exists. No model weights are downloaded by the executor.
- **D-26 (resolves OPEN-2):** A lecture id present in both the local registry and `remote_llm_allowed_lecture_ids` is a configuration error at preflight (option A). Egress stays fail-closed.
- **D-27 (execution authorization):** The operator authorized running the whole phase unattended, then `/gsd-code-review` and `/gsd-secure-phase` on the phase-final diff with fixes and re-runs, validation (pytest, ruff, mypy src, denylist scan), and a push to origin main of the private repository only when everything is green. Questions are reserved for: making the repository public, force-push or history rewrite, deleting data or branches, denylist terms, credentials.

### Claude's Discretion
- Exact module names inside `llm/`, the env variable name, the YAML schema keys beyond adapter, per-stage overrides and model path.
- Contract-test layout, and how the stub llama-cli binary is built for CI.
- Migration order of the six scripts.
- Where the sidecar run record is written and its file name.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements and roadmap
- `.planning/ROADMAP.md` Phase 6 - goal, dependency, four success criteria
- `.planning/REQUIREMENTS.md` GW-01..GW-06 - the six open requirements
- `.planning/PROJECT.md` D-READ-01, D-ADR0038-01, D-ADR0045-02 - revised egress reading, orchestrator child-process contract, Codex egress rule

### Decisions to amend
- `docs/decisions/0038-orchestrator-codex-integration-and-local-llm-route-removal.md` - removed the local route; amended by the new ADR
- `docs/decisions/0045-critic-gate-for-derivative-material-egress-and-audience.md` section 2 - egress rule closed by the new ADR

### Codex route (behavior to preserve)
- `docs/decisions/0035-codex-route-for-semantic-extraction-and-critic.md` - Codex route for stages 5 and 7
- `docs/decisions/0036-codex-critic-repair-loop-and-instruction-calibration.md` - repair loop stays in the script
- `docs/decisions/0037-codex-podcast-script-route.md` - whole-lecture podcast prompt
- `docs/decisions/0030-critical-review-merge-and-codex-exam-route.md` - exam route
- `docs/decisions/0027-student-facing-text-guard.md` - guard applied to exam, flashcard and podcast text

### Local adapter contract (history, not in git)
- `docs/decisions/0020-llama-cli-invocation-contract-correction.md` - `-n` cap and flag contract
- `docs/decisions/0022-llama-cli-structured-output-workaround.md` - prompt-embedded schema, `--single-turn`, response contract amendment
- `docs/decisions/0034-retry-repeat-penalty-for-llama-cli-workers.md` - retry-only repeat penalty
- `docs/decisions/0005-semantic-ledger-extraction-contracts.md`, `docs/decisions/0006-learning-unit-draft-synthesis-contracts.md`, `docs/decisions/0007-workspace-migration-and-critic-repair-loop.md` - original per-stage contracts

### Repository and CI constraints
- `docs/decisions/0044-public-extraction-and-site-access-hardening.md` section 3 - CI and leak gate that the new files must satisfy
- `docs/decisions/0010-local-pii-encryption-risk-acceptance.md` - confidentiality delegation to the Codex ADRs
- `docs/decisions/0024-uncertified-personal-study-execution-mode.md` - uncertified mode bounds

### Codebase maps
- `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/INTEGRATIONS.md`, `.planning/codebase/STACK.md` - layers, Codex integration, pinned llama.cpp

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/lecture_study/ops/llm_response_contract.py`: `parse_llm_json_response`, `build_bounded_retry_prompt_section`, `build_llm_retry_feedback`, `LlmResponseContractError`; the shared parse and retry contract both adapters must use.
- `src/lecture_study/ops/process_lifecycle.py`: `run_bounded_process` (timeout, stdout and stderr caps, `check`); both adapters spawn through it.
- `src/lecture_study/ops/process_environment.py`: sanitized child environment and `resolve_trusted_executable`.
- `src/lecture_study/ops/llm_instructions.py` and `quality/prompt_budget.py`: stable-prefix instruction text and prompt budgeting.
- `src/lecture_study/study_materials/local_lectures.py`: `LocalLecture` model (fields include `publish`), `LOCAL_LECTURES_PATH = config/local-lectures.local.yaml`, `load_local_lectures`; the natural home of `remote_llm_allowed`.

### Established Patterns
- All six `scripts/generate_codex_*.py` repeat the same call: `codex exec --skip-git-repo-check --sandbox read-only -C <scratch> -o <response.txt> <prompt>` through `run_bounded_process`, with a scratch cwd (the repository root holds `.env`), then `parse_llm_json_response`, then one retry with bounded feedback. Each also owns `_SUBPROCESS_TIMEOUT_SECONDS = 900.0` and a 16 MiB cap, and a `--codex-executable` argparse flag.
- The orchestrator runs these scripts as child processes with an environment allowlist (`_CODEX_PHASE_ENV_KEYS`: HOME, LANG, LC_ALL, LC_CTYPE, PATH, TEMP, TMP, TMPDIR), a 12-hour bound, and failure capture under `tmp/codex_phase_failures/`. A wiring test pins the script defaults to the orchestrator constants.
- Local config files follow the example-plus-git-ignored-local pattern (`config/targets.example.yaml`, `config/local-lectures.local.yaml`).
- `site/` must not import processing stages; the new `llm/` package must not be imported by `site/`.

### Integration Points
- `src/lecture_study/cli/orchestrator.py`: `_run_phase_5/6/7/13/14` and `_codex_phase_environment()` need the configuration path or resolved choice to reach children; the preflight belongs in the CLI before the first phase.
- Stage 18 (personal-study flashcards) is run by `scripts/generate_codex_personal_study_flashcards.py`; ADR 0038 does not list it among the orchestrator-integrated phases, so its wiring is checked at planning.
- `scripts/01_provision_engines.sh`: pinned llama.cpp commit; add the optional model provisioning step with a SHA-256 check.
- `.github/workflows/ci.yml` (`test` job on macos-15): runs the contract tests with the stub binary.

</code_context>

<specifics>
## Specific Ideas

- Operator override on D-15: unflagged lecture = remote not allowed; demo catalog carries `remote_llm_allowed: true` explicitly.
- Open for research: the flag lives in `LocalLecture`, but lectures that come from the academic catalog rather than the local registry do not appear in `config/local-lectures.local.yaml`. Where their egress policy is recorded needs a decision at planning. Not decided here.
- Open for research: `config/local-lectures.local.yaml` does not exist in the repository (git-ignored), so the demo catalog of Phase 7 is the first registry the repository itself ships; its location and format are a Phase 7 concern that D-15 constrains.

</specifics>

<deferred>
## Deferred Ideas

- Chunking or hierarchical splitting of prompts that exceed the local context window - new capability, own phase.
- `llama-server` on loopback for faster repeated calls - revisit if per-call model reload makes Phase 7 runs impractical.
- Adapter name recorded inside artifact payloads - rejected for now to avoid touching shared schemas.
- OS-level network sandbox for the local adapter.

</deferred>

---

*Phase: 6-Pluggable LLM gateway*
*Context gathered: 2026-10-14*
