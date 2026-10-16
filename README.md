# lecture-to-study-pipeline

A macOS Apple Silicon command-line pipeline, plus a small authenticated study site, that turns a registered lecture video into frames, OCR text, a corrected transcript, evidence packs, critic-reviewed grounded drafts and student-facing study material: a study guide, flashcards, a mock exam and a narrated podcast.

Status: a study project. Every generated page is labelled as uncertified material. The repository holds a synthetic demo lecture authored for it and no real lecture data.

Terminology: this README says "stage N" for the numbered pipeline stages. The architecture decision records (ADRs) and the code call the same units "Phase N".

## Contents

- [The problem](#the-problem)
- [Architecture](#architecture)
- [Stage map](#stage-map)
- [Design decisions](#design-decisions)
- [Measured results](#measured-results)
- [Quick start](#quick-start)
- [End-to-end demo](#end-to-end-demo)
- [Operating boundaries](#operating-boundaries)
- [Status and limits](#status-and-limits)
- [Repository layout](#repository-layout)
- [License](#license)

## Quick start

Requirements, as the repository files state them:

- Apple Silicon macOS. OCR uses Apple Vision and narration uses MLX ([ADR 0001](docs/decisions/0001-apple-silicon-quartz.md), and the macOS job of the CI workflow).
- Python 3.12, managed by [uv](https://docs.astral.sh/uv/) (`.python-version`).
- Homebrew. The provisioning script installs `ffmpeg`, `typst` and `huggingface-cli`; the CI job additionally installs `poppler` and `gitleaks`.

```bash
uv sync --group dev
bash scripts/01_provision_engines.sh --with-example-llm-model
cp config/llm-gateway.example.yaml config/llm-gateway.local.yaml
make lint
make test
```

The provisioning script builds whisper.cpp and llama.cpp with Metal, downloads the speech model and, with the opt-in flag, the example local text model. The example model is about 9 GB and pinned by revision and shard digests ([ADR 0046](docs/decisions/0046-pluggable-llm-gateway-and-local-adapter.md), decision 5). The first narration run downloads the Kokoro voice model once ([ADR 0032](docs/decisions/0032-kokoro-mlx-podcast-tts-engine.md)). The local gateway configuration selects the adapter; the versioned example documents every key.

`make docs-check` verifies that every command, make target, make variable, script, option, repository path, link and ADR number written in this README and in `docs/operations.md` exists as written.

`make hooks` installs the leak-gate git hooks. They read a private denylist that is kept outside the repository ([ADR 0044](docs/decisions/0044-public-extraction-and-site-access-hardening.md), section 3), and no term of it is named here.

```bash
make hooks
make docs-check
```

## End-to-end demo

The demo runs a six-slide Brazilian Portuguese synthetic lecture through the pipeline to a served page. The video is rebuilt from `demo/lecture/script.yaml`, which was authored for this repository and holds no third-party content and no patient data. The same video is registered twice: one registration is local-only, the other is cleared for the remote adapter ([ADR 0047](docs/decisions/0047-demo-lecture-measurement-method.md), decision 4). The steps run in this order from the repository root.

Step 1 builds the lecture video from the script (slides, narration and composition).

```bash
make demo-video
```

Step 2 registers the two lecture ids, 925100 for the local adapter and 925101 for the remote adapter.

```bash
make demo-register
```

Step 3 checks the engines, the model files and the egress policy before anything runs. The output is `preflight=ok` or `preflight=refused`.

```bash
make demo-preflight
```

Step 4 runs the driver with the local adapter. The driver runs four steps in order: `run-all` (stages 2 to 9 complete, and stage 10 refuses by design because no certificate is issued), `run-personal-study` (stages 8, 9, 13, 14 and 15), the stage 18 personal-study flashcards script and the study-guide script.

```bash
make demo-run
```

Step 5 prepares the throwaway sign-in material of the demo site, generates the pages, serves them over local HTTPS and checks the signed-in view. The sign-in values stay in a gitignored location under `workspace/demo/` and are never printed. `make demo-serve` occupies its terminal, so the check runs in a second one.

```bash
make demo-site-access
make demo-site
make demo-serve
```

```bash
make demo-check
```

The check prints one line with the lecture id, the status of an anonymous request, the status of the sign-in, the status of the lecture page and whether the uncertified notice is present.

Step 6 harvests the measured artifacts of the run and only then induces and recovers the blocked states ([ADR 0047](docs/decisions/0047-demo-lecture-measurement-method.md), decision 5; the recovery reruns the critic and replaces its record). Step 7 regenerates the results report.

```bash
make demo-harvest
make demo-recovery
make demo-report
```

### Variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `WHISPER_MODEL_PATH` | `engines/whisper.cpp/models/ggml-large-v3-turbo.bin` | Speech model passed to the transcription stage. |
| `DEMO_LOCAL_ID` | `925100` | Lecture id registered without the remote flag. |
| `DEMO_CODEX_ID` | `925101` | Lecture id registered with the remote flag. |
| `ADAPTER` | `llama-cpp` | LLM adapter of the driver: `llama-cpp` (local) or `codex` (remote). |
| `LECTURE` | `DEMO_LOCAL_ID` | Lecture id used by preflight, run, harvest and check. |
| `CONFIRM_REMOTE_EGRESS` | empty | The value `yes` passes the egress confirmation flag. |
| `FROM_STEP` | `run_all` | First driver step to run: `run_all`, `personal_study`, `flashcards` or `study_guide`. |
| `FLASHCARDS_MINIMUM_TOTAL_CARDS` | empty | Deck floor override for the flashcards stage; the stage default is 12. |

The measured local run used a deck floor of 5, because the local model did not reach the default floor of 12 for the one-unit lecture, as the results report records:

```bash
make demo-run FLASHCARDS_MINIMUM_TOTAL_CARDS=5
```

### Codex variant

The remote adapter sends lecture content to a remote provider. It is refused without the confirmation variable, and `docs/operations.md` is required reading before it is used. The first command below is refused by design (exit code 2) because the confirmation is absent. The second is documentation only: it passes the confirmation and sends the synthetic lecture to the provider.

```bash
make demo-preflight ADAPTER=codex LECTURE=925101
make demo-run ADAPTER=codex LECTURE=925101 CONFIRM_REMOTE_EGRESS=yes
```
