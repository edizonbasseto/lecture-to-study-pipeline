# 0046. Pluggable LLM gateway, local llama.cpp adapter, and code-enforced remote-model egress

## Status

Accepted. 2026-10-14. Amends ADR 0038 (the local text-LLM route returns as the
`LlamaCppAdapter` of the gateway; decisions 7-9 no longer list every local
model runtime; `--llm-model-path` and `--llm-model-family` stay removed).
Closes ADR 0045 section 2 (the egress rule is enforced by code). Amends
baseline requirement GEN-07.

The status covers decisions 1-9 as built in Phase 6. Decision 10 defines the
manual real-model run that the operator performs and lists the follow-ups that
run can still require; none of them is implemented by this record.

## Context

Six pipeline stages call a large language model: 5 (semantic ledgers), 6
(drafts), 7 (critic), 13 (mock exam), 14 (podcast script) and 18
(personal-study flashcards). Before this record each of the six scripts
spawned the Codex CLI itself and repeated the same boilerplate: scratch
directory, bounded process, response parsing, one bounded retry. Three
problems followed.

- The only available route sends lecture content to a remotely hosted model.
  ADR 0045 section 2 accepted that risk under a procedural rule and promised a
  local route "for content that may carry patient-identifying data". No such
  route existed, and no code enforced the rule.
- ADR 0038 removed the local llama.cpp text route. The operator decided on
  2026-10-14 that local text generation returns, but only behind a narrow
  port and only for the six stages above.
- The call, parse and retry boilerplate existed six times, so a change to the
  process contract needed six edits.

The local route did not survive in the repository history: the history starts
without it. The adapter is therefore written new from the contracts recorded in
ADRs 0020, 0022 and 0034 (decision 4), not restored.

## Decision

### 1. Port and layering (D-01, D-02, D-03)

The package `src/lecture_study/llm/` holds the gateway. The port is a raw-text
call: `LlmGateway.complete(LlmRequest) -> LlmResponse`, with the request
carrying the prompt, the `Stage`, a retry flag and an optional per-call
timeout. The shared helper `complete_json` in `llm/json_completion.py` sits on
top of any port: it parses the response with `parse_llm_json_response`,
validates it with a caller-supplied callable, and on a contract or parsing
failure performs exactly one retry with the original prompt plus a bounded
feedback section built by `build_bounded_retry_prompt_section`. The feedback
never replays model output. A typed gateway error is never retried.

The six scripts keep their prompt building and their domain validation
(traceability, the student-facing text guard, the listener guard, count
checks) and pass the validation as the `validate` argument, so it shares the
one bounded retry. The critic repair loop of ADR 0036 stays in
`scripts/generate_codex_critic_review.py`; its redraft request is an ordinary
first-attempt request, so the retry-only repeat penalty of decision 4 never
applies to it.

The gateway has its own error hierarchy, with messages built from stored
attributes only and raised without the originating exception, so a prompt or an
argument vector never reaches a traceback: call failure, prompt too large,
configuration, and refused remote egress.

Layering follows the dependency rule:

- The inner modules `gateway.py`, `json_completion.py`, `config.py`,
  `egress.py` and `run_record.py` import no adapter.
- `llm/` imports only `lecture_study.domain`, `lecture_study.quality.prompt_budget`
  and three `lecture_study.ops` modules (`process_lifecycle`,
  `process_environment`, `llm_response_contract`).
- `ops/` and `site/` never import `llm/`.
- The adapters (`codex_adapter.py`, `llama_cpp_adapter.py`, `bounded_call.py`)
  and `factory.py` are the outer modules.
- Composition happens in `src/lecture_study/cli/llm_gateway_wiring.py`
  (`open_stage_gateway`, `preflight_stage_gateways`), the only place that joins
  `llm/` with the registry-backed policy
  `src/lecture_study/study_materials/remote_llm_policy.py`.
- Each of the six scripts imports the wiring module and no process-spawning or
  scratch-directory module.

`tests/test_llm_layering.py` enforces these rules mechanically, including a
check that the list of six scripts matches the files on disk.

### 2. Selection and fail-closed configuration (D-10, D-11, D-12, D-20, D-21)

The adapter is chosen by configuration. `config/llm-gateway.example.yaml` is
versioned with synthetic values; the local copy `config/llm-gateway.local.yaml`
is git-ignored through the existing `config/*.local.yaml` rule. Keys:
`default_adapter` (`codex` or `llama-cpp`), `stages` (optional per-stage
adapter keyed by `semantic`, `drafting`, `critic`, `exams`, `podcast`,
`flashcards`), `remote_llm_allowed_lecture_ids`, `codex.executable` and the
`llama_cpp` section (`completion_executable`, `tokenize_executable`,
`model_path`, `model_family`).

Two environment variables apply:

- `LECTURE_STUDY_LLM_CONFIG` names an alternate file; a path that does not
  exist is an error, while an absent default file yields the built-in codex
  defaults.
- `LECTURE_STUDY_LLM_ADAPTER` replaces `default_adapter` and clears every
  `stages` entry for that run, so a run that says local is entirely local
  (D-20).

Precedence, highest first: `LECTURE_STUDY_LLM_ADAPTER`, the per-stage entry,
`default_adapter`, the built-in default (codex). Validation is strict: an
unknown key, stage name or adapter name, a `llama-cpp` selection without a
`llama_cpp` section, or an unreadable file stops the run before any model
call. Error messages carry field locations and error types, never input
values. An override is applied by re-validating the already validated file
model, so an invalid file still fails closed when the override would hide the
invalid part.

Bad configuration is detected twice (GW-04):

- The orchestrator preflight. `PhaseSpec.llm_stages` declares the gateway
  stages of each registry row explicitly: row 5 semantic, row 6 drafting,
  row 7 critic and drafting, row 13 exams, row 14 podcast; the
  deterministic Anki phase carries none. `run_all`, `run_phase` and
  `run_personal_study` call the preflight with the ordered union of the
  selected rows' stages before the first phase runs, independent of resume
  predicates. A `LlmGatewayError` becomes a `PipelineExecutionError` naming the
  lecture. Redrafts inside stage 7 use the `drafting` stage (D-21), so both
  `critic` and `drafting` are preflighted.
- The script. Each script re-validates through `open_stage_gateway` before its
  first call. Stage 18 is not run by the orchestrator and validates itself
  this way.

The preflight checks that the adapter name is known, that the executables
resolve, that the llama model path is a local regular file and that the egress
policy allows the lecture (decision 6). Nothing is contacted and nothing is
spawned.

The orchestrator forwards exactly two new names to the child scripts,
`LECTURE_STUDY_LLM_ADAPTER` and `LECTURE_STUDY_LLM_CONFIG`, in addition to the
eight names it already allowed. The child environment is otherwise unchanged
and credentials are still not forwarded.

### 3. Codex adapter (GW-02)

`CodexCliAdapter` preserves the previous behavior:

- The argument vector is `exec --skip-git-repo-check --sandbox read-only -C
  SCRATCH -o SCRATCH/response.txt PROMPT`, asserted exactly against a real stub
  process.
- The child starts in a fresh temporary scratch directory outside the
  repository, because the repository root holds the `.env` file.
- The environment is the sanitized allowlist plus `HOME`, which the Codex
  client needs for its credentials.
- Defaults: a 900 s timeout and a 16 MiB output cap, both owned by the adapter;
  the podcast script may override the timeout per call (decision 8).
- An argument-vector budget of 900 KiB (the macOS limit is 1 MiB) raises the
  typed prompt-too-large error with unit `bytes` before any scratch directory
  or process exists (D-23). The whole-lecture podcast prompt can exceed the
  limit.
- Timeout, output-cap overrun, start failure and a missing response file map
  to a typed call error whose reason carries neither the argument vector nor
  prompt text.

Failure capture under `tmp/codex_phase_failures/`, the 12-hour bound and the
child command shape remain owned by the orchestrator and are unchanged.

### 4. Local adapter contract (D-04 as amended by D-17, D-05, D-08, D-09, D-23)

`LlamaCppAdapter` runs one bounded `llama-completion` process per call through
`run_bounded_process`. It does not use `llama-cli`: at the pinned llama.cpp
commit `llama-cli` embeds a loopback server and echoes the prompt, which
contradicts D-04 (no listener) and D-09 and would break the JSON parser. There
is no `llama-server` and no listener. This was established from the pinned
source during research and is exercised in CI only through stub binaries
(decision 10 lists what only a real run can show).

The contract is rebuilt from ADR 0020 (a mandatory `-n` completion cap, sized
against the context budget), ADR 0022 (the schema embedded as literal prompt
text, `--single-turn`, no `--json-schema-file`) and ADR 0034 (a repeat penalty
on the retry only), not restored from history (D-05). The flags of every call
are:

```
-m MODEL -f PROMPT_FILE -c CONTEXT -n CAP --temp TEMPERATURE
--single-turn --no-display-prompt --no-escape --simple-io --offline
--reasoning off
```

- The prompt is delivered by file, never through the argument vector.
- `--no-escape` is mandatory: the embedded schema text must not be rewritten.
- `--reasoning off` is passed on every stage and every attempt (D-17), and
  tests assert it for each. No per-model-family mechanism exists. The operator
  answered the open question (OPEN-1, answered 2026-10-14, D-25) as follows:
  the generic flag suffices, and its adequacy for thinking-style models, whose
  reasoning text could contain braces that the single-candidate JSON parser
  rejects, is validated in the manual run of decision 10. That validation is
  pending the operator's run: the example model is not a thinking model, and no
  other model was downloaded for this record.
- `--repeat-penalty 1.3 --repeat-last-n 256` are appended only when the
  request is a retry (ADR 0034). A critic-feedback redraft is not a retry.
- `model_family` is validated metadata (`[a-z0-9][a-z0-9._-]*`) and selects no
  code path.
- The trailing ` [end of text]` marker that llama.cpp prints is removed only
  when it is the final text; output cut by the `-n` cap is returned unchanged.
  The adapter never parses the response.

Before generation the adapter runs `llama-tokenize -m MODEL -f PROMPT_FILE
--no-escape --ids --show-count` and reads the `Total number of tokens:` line.
`PromptBudget` compares the exact count plus the completion reserve with the
stage context window. A prompt that does not fit raises the typed
prompt-too-large error naming the stage and the sizes before any model call
(D-08). There is no truncation and no chunking; chunking is a separate
capability.

Per-stage budgets are code-level defaults of the adapter (context tokens,
completion cap, temperature):

| Stage | Context | Completion cap | Temperature |
| ----- | ------- | -------------- | ----------- |
| semantic | 12288 | 2048 | 0.0 |
| drafting | 12288 | 2048 | 0.2 |
| critic | 12288 | 1024 | 0.0 |
| exams | 8192 | 4096 | 0.2 |
| podcast | 8192 | 4096 | 0.2 |
| flashcards | 8192 | 2048 | 0.2 |

The values are planning assumptions: the pairs for the first three stages come
from the ADR 0020 erratum, the others are estimates. Their adequacy is not
shown by stub tests. A truncated response (cap too small) or memory pressure
(context too large) in a real run is the signal to revise them.

The child environment is the sanitized allowlist with every `LLAMA_ARG_*` and
`HF_*` variable removed, plus `LLAMA_ARG_OFFLINE=1`. It carries no other
`LLAMA_ARG_*` or `HF_*` variable and no `HOME`. The child starts in a dedicated
empty directory, separate from the directory holding the prompt file.

What "no network egress" means: the adapter passes no download or listener
flag and no URL; it passes `--offline` and sets the offline variable; the model
path must resolve to an existing regular file and must not contain a URL
separator; a repository-style identifier is not a file and is rejected; and
tests assert the argument vector and the environment of both binaries, with
stub binaries that exit non-zero on any forbidden flag, URL argument or
download variable. What it does not mean: there is no operating-system
sandbox, which is deferred, and the pinned llama.cpp binary is trusted not to
open connections on its own. The property is shown by construction and tests,
not by confinement.

### 5. Example model (D-06, D-24)

The documented example is Qwen2.5-14B-Instruct, Q4_K_M, from the Hugging Face
repository `Qwen/Qwen2.5-14B-Instruct-GGUF`, pinned to revision
`b466e1f8c07172155743e8e1307507d8a4f91fbd` and distributed under the
Apache-2.0 license. The license, the revision and the per-shard digests were
verified on 2026-10-14 against the model card (front matter `license:
apache-2.0`) and the repository file listing; the pinned revision resolves for
the first shard. The model is split into three shards:

| Shard | SHA-256 |
| ----- | ------- |
| `qwen2.5-14b-instruct-q4_k_m-00001-of-00003.gguf` | `a09ea5e7b1eafb1b30b241726c3cc3c905c96f14ad41e246ffa5f44e53904f68` |
| `qwen2.5-14b-instruct-q4_k_m-00002-of-00003.gguf` | `21b9457d079680d284e90ef69607c4b2d8ef64a09d4729cb7b5e1357bdba41ae` |
| `qwen2.5-14b-instruct-q4_k_m-00003-of-00003.gguf` | `c8d37006760a387a35216e070e6664d7da927f10be8eb870fef2e3d4833d9976` |

The total download is about 9 GB. `scripts/01_provision_engines.sh` builds and
verifies `llama-completion`, `llama-tokenize` and `llama-mtmd-cli` at the
pinned llama.cpp commit, and downloads the shards only with the opt-in flag
`--with-example-llm-model`: HTTPS only, TLS 1.2 minimum, each shard written to
a temporary name, verified against its pinned digest and moved into place only
on a match (a mismatch deletes the file and fails). Without the flag, Whisper
stays the only default download. Provisioning needs the network by design;
runtime does not. The files land in `engines/models/qwen2.5-14b-instruct-q4_k_m/`
and `config/llm-gateway.example.yaml` points `llama_cpp.model_path` at the
first shard.

No weights are stored in the repository and no code carries a default model
path.

### 6. Remote-model egress as code (D-14, D-15, D-19)

ADR 0045 section 2 is closed by code. The remote `CodexCliAdapter` runs for a
lecture only when the operator recorded the lecture as eligible:

- A lecture of the local registry carries `LocalLecture.remote_llm_allowed` in
  `config/local-lectures.local.yaml`. The field defaults to false, and a row
  without the key loads as false. `scripts/register_local_lecture.py` sets it
  with `--remote-llm-allowed`; a rerun may toggle the flag either way.
- A lecture of the portal catalog, which is not in the local registry, is
  listed in `remote_llm_allowed_lecture_ids` of the gateway configuration
  (D-19).
- A lecture flagged in neither place is refused (fail closed, D-15). A missing
  registry file refuses, an unregistered reserved local identifier refuses, and
  an unreadable registry raises a typed configuration error instead of passing.

The check happens in the factory before the Codex executable is resolved, so a
refused run resolves nothing and spawns nothing. The local adapter is not
subject to the flag.

Each lecture identifier has exactly one governing source: reserved local
identifiers are decided only by the registry row, any other identifier only by
the portal list. An identifier that appears both in the local registry and in
`remote_llm_allowed_lecture_ids` is a configuration error raised at preflight
(OPEN-2, option A, operator decision 2026-10-14): not a warning, and the
registry does not win. Egress stays fail closed. The accepted consequence is
that a run selecting codex stops until the duplicate list entry is removed,
even for an unrelated lecture.

Detecting patient-identifying data stays the operator's responsibility. The
flag is the operator's recorded verification that the lecture carries none;
no technical control inspects content. The retention and training terms of the
remote provider stay a procedural check against the account in use (D-16).

### 7. Provenance run record (D-13 as amended by D-18)

Each script writes a metadata-only record of its run to
`workspace/data/llm_runs/LECTURE_ID/SCRIPT_STAGE.json` (`semantic.json`,
`drafting.json`, `critic.json`, `exams.json`, `podcast.json`,
`flashcards.json`). The `--llm-runs-root` option moves the root. The record
holds `record_version`, `lecture_id`, `script_stage`, `schema_name`, `status`
(`succeeded` or `failed`), `started_at`, `finished_at`, `wall_seconds` and one
entry per gateway used with `stage`, `adapter_name`, `model_identifier`,
`model_sha256`, `engine_version`, `calls`, `retry_calls` and `call_seconds`.
Stage 7 lists two gateways, critic and drafting. The record contains no prompt
and no response text, is written to a temporary name and then replaced
atomically, validates the lecture identifier before building the path, and is
written with status `failed` when the body raises. `engine_version` is the
llama.cpp commit read from `engines/llama.cpp/build/.built-commit` when
present.

`model_sha256` is the digest of the configured `model_path` file only. For the
split example model that file is the first shard, so the digest identifies the
first shard, while every shard is verified at provisioning by its pinned
SHA-256 (decision 5). The digest of a file shared by several gateways of one
run is computed once.

The record is not stored beside the stage outputs because those directories are
counted by the progress dashboard (`scripts/generate_progress_dashboard.py`
reads a fixed list of stage directories) and fingerprinted by
`src/lecture_study/quality/delivery.py` (explicit roots only). The directory
`workspace/data/llm_runs` is under none of those roots, which a test asserts.
Artifact schemas stay unchanged.

### 8. Deliberate behavior changes

- Typed call errors replace raw process errors in all six scripts. The podcast
  script no longer retries a failed process: a process failure is a typed call
  error, never retried, and the script exits 1 without the prompt on stderr
  (D-22). A validation failure of the response is still retried once.
- `--codex-executable` now defaults to the configuration (`codex.executable`);
  the flag, when given, overrides it.
- `--llm-runs-root` is new on every generation script.
- `--call-timeout-seconds` is new on the podcast script (default 900, the
  adapter default); zero, negative and non-finite values are rejected at the
  parser.
- An oversized whole-lecture podcast prompt is refused with the typed
  prompt-too-large error naming the stage and the sizes, with no output
  written.
- Every existing registry entry needs `remote_llm_allowed: true` to keep using
  Codex (D-15); the configured default adapter stays codex.
- The orchestrator preflights the gateway stages before the first phase and
  forwards the two new environment names (decision 2).
- Stage 18 validates its own configuration, because the orchestrator does not
  run it.

### 9. Open question carried

When one adapter serves both generator and critic, the generator-evaluator
independence question of ADR 0045 section 4 remains open. Any future certified
route must decide it in a new record.

### 10. Manual real-model run (D-07)

The Accepted status covers decisions 1-9 as built. The adapter is proven in CI
against stub binaries only; no llama.cpp binary or GGUF model exists in CI or
on the planning machine. D-07 therefore requires one documented real-model run
of stages 5, 6 and 7 on one evidence pack, performed by the operator. The
record carries no quality numbers: measured results belong to Phase 7.

Procedure:

1. Run `scripts/01_provision_engines.sh --with-example-llm-model`.
2. Copy `config/llm-gateway.example.yaml` to `config/llm-gateway.local.yaml`.
3. With `LECTURE_STUDY_LLM_ADAPTER=llama-cpp`, on a lecture whose evidence
   packs exist locally, run in order:
   - `uv run python scripts/generate_codex_semantic_ledgers.py LECTURE_ID --pack-sequence 1`
   - `uv run python scripts/generate_codex_drafts.py LECTURE_ID`
   - `uv run python scripts/generate_codex_critic_review.py LECTURE_ID`

Only pack 1 has a ledger, so drafts and critic process one pack.

Record (status: pending operator run; each result cell is filled when the run
is executed):

| Item | Result |
| ---- | ------ |
| Date | pending operator run |
| Machine class | pending operator run |
| llama.cpp commit (`engines/llama.cpp/build/.built-commit`) | pending operator run |
| Model file SHA-256 values (three shards) | pending operator run |
| Command line, stage 5 | pending operator run |
| Command line, stage 6 | pending operator run |
| Command line, stage 7 | pending operator run |
| Stage 5 outcome (exit code, error class if any, run record status) | pending operator run |
| Stage 6 outcome (exit code, error class if any, run record status) | pending operator run |
| Stage 7 outcome (exit code, error class if any, run record status) | pending operator run |
| Run records report adapter `llama-cpp` and no Codex process started | pending operator run |
| GGUF chat template worked with `--single-turn`, or the `-no-cnv` fallback is needed | pending operator run |
| Child needed `HOME` | pending operator run |
| First shard loaded the split model | pending operator run |
| Token-count line format as parsed by the adapter | pending operator run |
| Every flag of decision 4 accepted at the pinned commit | pending operator run |
| Thinking-style model with `--reasoning off` (optional): any reasoning text on stdout | pending operator run |

The last row is optional and applies only if the operator supplies a
thinking-style model; no such model has a pinned digest in provisioning.

Follow-ups from this run. The Accepted status holds while these are open. This
record implements none of them. After the run, the operator marks each cell
"needed" or "not needed":

| Item | Follow-up if the run requires it | Needed |
| ---- | -------------------------------- | ------ |
| (a) | The D-17 fallback: `-no-cnv` plus an adapter-rendered family envelope, if the GGUF chat template fails with `--single-turn` | pending operator run |
| (b) | `HOME` added to the llama child environment, if the child cannot start or Metal fails without it (research assumption A3) | pending operator run |
| (c) | A model-path or adapter change, if the first shard does not load the split model (research assumption A9) | pending operator run |
| (d) | An adapter change if the real `llama-tokenize` count line differs from the `Total number of tokens:` format the adapter parses | pending operator run |
| (e) | An argument change for any flag of decision 4 that the pinned commit does not accept | pending operator run |

An item marked "needed" is recorded as a failed item in the phase UAT produced
by `/gsd-verify-work`, which routes it to gap-closure planning
(`/gsd-plan-phase 6 --gaps`). The gap plan that implements it also adds an
amendment note to decision 4 of this record.

## Alternatives considered

- **`llama-cli` per call.** Rejected: at the pinned commit it embeds a
  loopback server and echoes the prompt (decision 4).
- **A resident `llama-server`.** Deferred: it needs a listener, and a faster
  repeated-call path is revisited only if per-call model loading makes real
  runs impractical.
- **A byte-length bound instead of a token count.** Rejected: an exact
  `llama-tokenize` count is available, and a byte bound either wastes context
  or admits overflow.
- **Delivering the Codex prompt through stdin.** Follow-up: `run_bounded_process`
  closes stdin, so the argument-vector budget guard is the interim measure.
- **Recording the egress flag on the portal catalog model** (research option
  O1). Rejected in favor of the configuration list (O2): O1 touches a second
  strict model and the catalog readers, while the list lives in the one
  operator-owned file the gateway already reads.
- **Flagging all lectures only in the gateway configuration** (O3), **refusing
  portal lectures implicitly** (O4) and **allowing them implicitly** (O5).
  Rejected: O3 contradicts the per-lecture registry field of D-14 and leaves
  the registration command unable to record the flag, O4 makes Codex unusable
  for the main existing flow, and O5 fails open.
- **Recording the adapter name inside artifact payloads.** Rejected: shared
  artifact schemas stay unchanged; the sidecar record carries it.
- **An operating-system network sandbox for the local adapter.** Deferred.
- **Detecting patient-identifying data in code.** Rejected: no reliable
  detector exists; the flag records the operator's verification instead.

## Consequences

- **Positive:** one call, parse and retry path replaces six copies; a change
  to the process contract is made in one adapter.
- **Positive:** a run that selects the local adapter starts no Codex process,
  shown end to end for stages 5, 6 and 7 against stub binaries.
- **Positive:** an unknown or misconfigured adapter, a missing executable or
  model, and an unflagged lecture stop the run before the first phase.
- **Positive:** the egress rule of ADR 0045 section 2 is enforced by code and
  fails closed.
- **Positive:** each LLM-backed run leaves a metadata-only provenance record.
- **Negative, accepted:** every existing registry entry needs the flag, and a
  duplicate between the registry and the portal list stops a codex run.
- **Negative, accepted:** the retention and training terms of the remote
  provider remain a procedural check, and the flag depends on the operator's
  verification.
- **Negative, accepted:** "no network egress" is shown by construction and
  tests, not by confinement; the pinned binary is trusted.
- **Negative, accepted:** a prompt that exceeds the local context window is
  refused, not chunked, so a long lecture can be processed by the remote
  adapter and not by the local one.
- **Negative, accepted:** loading the model on every call makes local runs
  slow.
- **Negative, accepted:** `model_sha256` identifies only the first shard of the
  split example model.
- **Operational:** the adapter has been exercised against stub binaries only.
  The manual real-model run of decision 10 is required to confirm the flag
  set at the pinned commit and the open items listed there.
