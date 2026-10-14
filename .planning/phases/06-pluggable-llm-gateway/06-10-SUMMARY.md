---
phase: 06-pluggable-llm-gateway
plan: 10
subsystem: provisioning
tags: [provisioning, llama-cpp, shell, supply-chain, sha256, qwen2.5]

requires:
  - phase: 06-pluggable-llm-gateway
    provides: "Decisions D-06, D-17, D-23, D-24 (adapter binaries, opt-in example model, execution-time source and license verification)"
provides:
  - "scripts/01_provision_engines.sh builds and verifies llama-completion, llama-tokenize and llama-mtmd-cli at the pinned llama.cpp commit"
  - "--with-example-llm-model: opt-in download of Qwen2.5-14B-Instruct Q4_K_M from a pinned Hugging Face revision with per-shard SHA-256 verification"
  - "Argument parsing before any side effect (--help exit 0, unknown argument exit 2)"
  - "Network-free tests of argument handling and pinned-value structure"
affects: [06-03, 06-11, 07]

actuals:
  tokens: 2515
  tasks: 2
  commits: 2
plan_head_before: 2790d2b809a5510a34eac324281fed2bf5841493
plan_head_after: 4b5659877c7aae815aae6204f05f7df9f09fd0fd

tech-stack:
  added: []
  patterns:
    - "Parse every argument before any side effect; unknown argument exits 2"
    - "Download to FILE.part, verify_sha256 (deletes and exits 1 on mismatch), then mv to the final name"
    - "Pinned repository revision (40-hex commit) in the download URL, never a branch name"

key-files:
  created:
    - tests/test_provision_engines.py
  modified:
    - scripts/01_provision_engines.sh

key-decisions:
  - "Model download pinned to Hugging Face revision b466e1f8c07172155743e8e1307507d8a4f91fbd, HTTPS only with TLS 1.2 minimum"
  - "huggingface-cli left in the brew dependency list as instructed; no step uses it any more (cleanup candidate)"

requirements-completed: [GW-03]

coverage:
  - id: D1
    description: "Provisioning builds and verifies llama-completion, llama-tokenize and llama-mtmd-cli at the pinned commit; argument handling exits before side effects"
    requirement: GW-03
    verification:
      - kind: unit
        ref: "tests/test_provision_engines.py"
        status: pass
    human_judgment: true
    rationale: "The real build path (clone, cmake, binary presence) is not executed by the network-free tests; it is exercised by the operator's provisioning run and the D-07 manual run (06-11)."
  - id: D2
    description: "Opt-in example model download from a pinned revision with per-shard SHA-256 verification; nothing downloads by default"
    requirement: GW-03
    verification:
      - kind: unit
        ref: "tests/test_provision_engines.py"
        status: pass
    human_judgment: true
    rationale: "The download itself was deliberately not run (no model weights fetched). Pinned values and URL structure are asserted; the first real download belongs to the operator."

duration: 2 min
completed: 2026-10-14
status: complete
---

# Phase 6 Plan 10: Provisioning for the gateway local adapter Summary

**Provisioning now builds and checks the three llama.cpp binaries the gateway needs and offers an explicit `--with-example-llm-model` step that fetches the documented Qwen2.5-14B-Instruct Q4_K_M shards from a pinned Hugging Face revision, each verified against its pinned SHA-256 before it takes its final name.**

## Performance

- **Duration:** about 2 min of execution
- **Started:** 2026-10-14T21:56:04Z
- **Tasks:** 2
- **Files:** 1 created, 1 modified

## Accomplishments

- `parse_arguments` runs first in `main`: `-h`/`--help` prints the usage and exits 0, `--with-example-llm-model` sets `WITH_EXAMPLE_LLM_MODEL=1`, any other argument prints the usage to stderr and exits 2 before any install, clone or download.
- `build_llama_cpp` keys staleness on `llama-completion` and calls `require_llama_binaries`, which exits 1 with `==> FATAL: missing NAME after the llama.cpp build` unless `llama-completion`, `llama-tokenize` and `llama-mtmd-cli` are executable.
- `download_example_llm_model` iterates `EXAMPLE_LLM_MODEL_SHARDS` (renamed from `LLM_MODEL_SHARDS`, values unchanged); present-and-matching shards are skipped, others are downloaded with `curl --fail --location --proto '=https' --tlsv1.2` to `FILE.part`, checked with `verify_sha256`, then moved into place.
- Without the flag the script prints `==> Skipping the example LLM model (pass --with-example-llm-model to download it)`; Whisper remains the only default download.
- Header comment rewritten for ADR 0038 / ADR 0046.

## Task Commits

1. **Task 1 (tracer): argument handling, binary verification, tests** - `4d85fa2` (feat)
2. **Task 2: opt-in example model download, pinned-value tests** - `4b56598` (feat)

The tracer gate was satisfied by re-running Task 1's `<verify>` end to end (bash -n, shellcheck baseline of exactly the two SC2034 warnings, pytest) before expansion.

## D-24 Execution-time Verification Evidence (for ADR 0046)

Verified on 2026-10-14 over HTTPS; metadata only, no model weights were downloaded.

| Item | Result |
| ---- | ------ |
| Model API URL | `https://huggingface.co/api/models/Qwen/Qwen2.5-14B-Instruct-GGUF?blobs=true` (HTTP success) |
| Model card URL | `https://huggingface.co/Qwen/Qwen2.5-14B-Instruct-GGUF/raw/b466e1f8c07172155743e8e1307507d8a4f91fbd/README.md` (HTTP success) |
| License identifier | `apache-2.0` (model card front matter `license: apache-2.0`; API tag `license:apache-2.0`) |
| Repository revision pinned | `b466e1f8c07172155743e8e1307507d8a4f91fbd` (repository `sha`; last modified 2024-09-20) |
| Revision resolves | HEAD on `.../resolve/b466e1f8c07172155743e8e1307507d8a4f91fbd/qwen2.5-14b-instruct-q4_k_m-00001-of-00003.gguf` returned a 302 redirect to the CDN |

LFS SHA-256 published for each shard versus the pinned value (all three match):

| Shard | Size (bytes) | SHA-256 (published = pinned) |
| ----- | ------------ | ---------------------------- |
| `qwen2.5-14b-instruct-q4_k_m-00001-of-00003.gguf` | 3991999872 | `a09ea5e7b1eafb1b30b241726c3cc3c905c96f14ad41e246ffa5f44e53904f68` |
| `qwen2.5-14b-instruct-q4_k_m-00002-of-00003.gguf` | 3989373504 | `21b9457d079680d284e90ef69607c4b2d8ef64a09d4729cb7b5e1357bdba41ae` |
| `qwen2.5-14b-instruct-q4_k_m-00003-of-00003.gguf` | 1006737120 | `c8d37006760a387a35216e070e6664d7da927f10be8eb870fef2e3d4833d9976` |

Repository id `Qwen/Qwen2.5-14B-Instruct-GGUF` (an assumption in the plan) is confirmed. Total download is about 9 GB.

## Deviations from Plan

None - plan executed exactly as written.

The plan placed the `WITH_EXAMPLE_LLM_MODEL=0` default "before parsing"; it is assigned at the start of `parse_arguments`, which `main` calls first, so the default is still set before any argument is read.

## Authentication Gates

None.

## Verification Results

- `bash -n scripts/01_provision_engines.sh`: pass
- `shellcheck scripts/01_provision_engines.sh`: zero findings (Task 1 baseline of two SC2034 warnings resolved by Task 2)
- `uv run pytest tests/test_provision_engines.py -q`: 9 passed
- `uv run pytest -q -rs`: 2306 passed, 1 warning
- `uv run ruff check .` and `uv run ruff format --check .`: clean (214 files)
- `uv run mypy src`: no issues in 116 source files
- No absolute home paths in the script or the test.

## Follow-ups and Notes

- `huggingface-cli` is no longer used by any provisioning step; the brew dependency was left in place as instructed and is a cleanup candidate.
- `config/llm-gateway.example.yaml` (plan 06-03) must point `llama_cpp.model_path` at the first shard inside `engines/models/qwen2.5-14b-instruct-q4_k_m/`; loading split GGUF files from the first shard is RESEARCH assumption A9, to be confirmed or refuted by the D-07 manual run (06-11, operator's pending item).
- The real download and the real build path were not executed here by design.

## Known Stubs

None.

## Threat Flags

None. The network surface introduced (HTTPS download behind an explicit flag) is the one covered by T-06-38, T-06-39, T-06-40 and T-06-SC: pinned per-shard SHA-256 verified before the final name, pinned revision, HTTPS with TLS 1.2 minimum, opt-in only, license and source evidence recorded above.

## Self-Check: PASSED

- `scripts/01_provision_engines.sh` and `tests/test_provision_engines.py` exist.
- Commits `4d85fa2` and `4b56598` are ancestors of HEAD.
