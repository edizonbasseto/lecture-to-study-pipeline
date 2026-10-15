# Phase 7: Synthetic demo lecture and measured results - Research

**Researched:** 2026-10-15
**Domain:** Authored demo media (Typst slides, Kokoro narration, ffmpeg), the existing 15-stage lecture pipeline driven end to end on one synthetic lecture, run-record based quality measurement, local serving of the study site, ADR 0045 blocked-lecture recovery
**Confidence:** HIGH for repository mechanics and for the media chain (probed this session); MEDIUM for measured-result expectations (the numbers themselves do not exist until the operator-run stages execute)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

### Narration
- **D-01:** Narration uses Kokoro-82M through `mlx-audio` (`mlx_audio.tts.generate`), the engine ADR 0032 adopted for the podcast, replacing macOS `say`. The operator asked that the engine used previously in the source project stay as default; the repository record confirms Kokoro as the accepted engine (ADR 0032, status Accepted). Whether it was the most recent engine in the private source project is not verifiable from this repository.
- **D-02:** `mlx-audio>=0.5.4` is already a core dependency in `pyproject.toml` and imports in the project environment; no new dependency or extra is needed.
- **D-03:** ADR 0032 and `tts/generator.py` map only Brazilian-Portuguese voices (`pf_dora`, `pm_alex`, lang code `p`). The demo narration is therefore Brazilian Portuguese unless planning finds a reason to add an English voice mapping (that would need an ADR amendment). Assumption to confirm in planning.

### Measurement scope
- **D-04:** Measurements are taken for two adapters: local `LlamaCppAdapter` (Qwen2.5-14B Q4_K_M already provisioned under `engines/models/`) and remote `CodexCliAdapter`. The demo lecture is flagged `remote_llm_allowed` because its content is synthetic and carries no patient data (ADR 0046 policy; the operator remains responsible for that claim).

### Lecture shape
- **D-05:** Short lecture, about 4 minutes and 6 slides, veterinary-flavored generic topic. Versioned sources live in the repository (directory name chosen at planning), contain no third-party content and no patient data (DEMO-01).

### Ground truth for ASR
- **D-06:** The narrated script is the word-for-word reference for ASR word error rate. About 15 domain terms are planted on purpose so the terminology dictionary (`transcription/terminology.py`) has measurable corrections; WER is reported before and after the dictionary (DEMO-03).

### Claude's Discretion
Exact topic, slide design, directory layout of demo sources, report format and location, make target names, and how the second adapter run is orchestrated.

### Deferred Ideas (OUT OF SCOPE)
None.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| DEMO-01 | Synthetic veterinary lecture authored for the repository (own script, own slides, Kokoro narration, ffmpeg composition), no third-party content, no patient data, reproducible from versioned sources by a make target | Verified media chain (Typst 0.15.1 -> Kokoro via the generator's argv -> ffmpeg 9.0.2 -> repo extraction -> Apple Vision OCR) in "Code Examples 1-4"; script design rules in Pitfalls 8-10; single-source `script.yaml` pattern |
| DEMO-02 | Demo lecture runs through `run-all` and `run-personal-study`, stages 8 and 9 included, to a signed-in page view on the locally served site | Registration and run commands, the stage-10 stop of `run-all`, the study-guide and site-generation bridge, and the HTTPS local serving finding (Pitfalls 1, 6, 7, 11; Code Examples 5, 9) |
| DEMO-03 | Measured quality report: ASR WER before/after dictionary, schema pass rate per LLM stage, critic rejection rate, per-stage wall time, per adapter | Dictionary mechanics and raw-ASR reconstruction, run-record fields and derivation formulas, harvest-before-recovery ordering, two-lecture-id adapter strategy (Standard Stack, Pitfalls 2-5, Code Examples 6-7) |
| DEMO-04 | Blocked-lecture recovery of ADR 0045 demonstrated: a rejected or stale unit blocks the four derivative scripts (nothing written); after recovery they produce exam, podcast, flashcards, study guide | Gate code read in full, per-script blocked behavior, the two deterministic ways to induce the blocked state, recovery commands, tree-snapshot verification (Architecture Pattern 4, Pitfall 12) |
</phase_requirements>

## Project Constraints (from CONTEXT.md and operator instructions)

No `./CLAUDE.md` exists in the repository root [VERIFIED: `ls -a` of the repository root this session]. The operator's global instructions (`~/.claude/CLAUDE.md`, `~/AGENTS.md`) apply and are treated as locked:

- **R0.1 Sensitive data:** no keys, tokens, credentials, cookies, PII, dumps in any file except `.env`. The demo site credentials and session secret MUST be generated at run time into gitignored paths and never committed; no fake strings that resemble real keys in tests or docs.
- **R0.2 No duplication:** no duplicate files or pages; extend `tts/generator.py` in place rather than copying the Kokoro subprocess code into a second module.
- **R0.3:** `clean-code` skill before any code is typed or edited; `clean-architecture` skill as well, because this phase adds a package and decides dependency direction (pure measurement logic inward, subprocess adapters outward).
- **R0.4:** phase-level `/gsd-code-review` and `/gsd-secure-phase` on the phase-final diff, re-run after material fixes (also in ROADMAP "Phase completion gates").
- **Language:** commits, code, ADRs, results report in formal en-US, no emojis; site and demo lecture content in pt-BR; impersonal voice in all produced text.
- **Python gates:** `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src` (strict), `uv run pytest` [VERIFIED: Makefile `lint` target, `.github/workflows` job `test`].
- **Binary outputs are never versioned:** `.gitignore` excludes `*.mp4`, `*.wav`, `*.m4a`, `*.png`, `*.pem`, `*.key`, `workspace/`, `config/*.local.yaml`, `config/secrets/`, `config/session-key/`, `config/site-credentials.txt` [VERIFIED: `.gitignore`]. The demo video, slide images and TLS material therefore live under `workspace/` and are rebuilt, never committed.
- **Egress rule (ADR 0045 section 2 / ADR 0046):** the demo lecture is flagged `remote_llm_allowed` by operator decision D-04; the operator remains responsible for the no-patient-data claim and for the provider retention/training terms, which the repository does not record.

## Summary

The pipeline needs almost no change to process the demo lecture: the lecture is registered with `scripts/register_local_lecture.py` (synthetic id in 925100-925299, symlink `workspace/data/01_raw/<id>.mp4`, row in `workspace/data/queue.json`, flags in `config/local-lectures.local.yaml`), and `uv run python -m lecture_study.cli.main run-all|run-personal-study <id> --whisper-model-path engines/whisper.cpp/models/ggml-large-v3-turbo.bin` does the rest. The adapter is selected per run with the environment variable `LECTURE_STUDY_LLM_ADAPTER` (`llama-cpp` or `codex`), which the orchestrator forwards to every child. Everything required is present on this machine: ffmpeg 9.0.2, typst 0.15.1, whisper model, llama.cpp binaries, the Qwen2.5-14B shards, the Kokoro cache, codex-cli 0.155.1 (logged in), Apple Vision OCR [VERIFIED: probes this session, see Environment Availability].

The decisive findings that shape the plan are about measurement, not plumbing. (1) At about 4 minutes the lecture yields exactly **one evidence pack**: the chunker needs 300-600 s per unit and a shorter lecture becomes one unit with a `lecture_shorter_than_minimum_unit` exception, and only the slide frame nearest the unit midpoint reaches the LLM prompts, so the critic rejection rate has a sample of one review (plus repair attempts) per adapter unless the operator chooses a longer lecture. (2) The "terminology dictionary" is code, not data: two regex rule families (`estopatológico` to `histopatológico`; `facial` to `fascial` only after a depth-context anchor word within three segments). The ASR initial prompt already fixes `histopatológico`; the probe showed `fascial` is heard as `facial` and survives the prompt. The measurable before/after effect therefore comes from planted `fascial` phrases that sit next to an anchor word, and an optional no-prompt ablation exposes the `histopatológico` rule. (3) The raw ASR text is **not persisted**; it can be reconstructed from the corrections file or re-run. (4) Run records are overwritten per stage and the critic rerun of the ADR 0045 recovery deletes the earlier record, so measurements must be harvested before the recovery, and the two adapters should run on two registered ids (artifact directories and resume predicates are keyed by lecture id). (5) The site session cookie is `Secure` with the `__Host-` prefix, which is unreliable over plain HTTP on localhost; serving the site over local HTTPS with a throwaway self-signed certificate was verified end to end this session.

**Primary recommendation:** Build the lecture from one versioned `script.yaml` (narration lines plus slide text, rendered by Typst `yaml()`), reuse the Kokoro invocation of `tts/generator.py` through a shared public helper, register the same video under two ids (one per adapter), put pure measurement logic (WER, metrics from run records, report rendering) in a new typed `src/lecture_study/demo/` package driven by thin scripts and make targets, harvest records into `workspace/demo/runs/<adapter>/` before running the recovery, and serve the site over local HTTPS on `127.0.0.1`.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Demo media build (slides, narration, composition) | Offline build tooling (`scripts/` + make) | `src/lecture_study/tts` shared Kokoro helper | Produces gitignored artifacts under `workspace/demo/`; subprocesses with argument lists only (SPINE-03) |
| Registration of the lecture and flags | `scripts/register_local_lecture.py` (existing) | `config/local-lectures.local.yaml` registry | The only supported entry (INGEST-01, PUB-01); egress flag lives here |
| Stages 2-4, 8, 9, 15 (deterministic) | Pipeline CLI / orchestrator (existing) | Workspace filesystem artifacts | Resume predicates are artifact based (SPINE-01) |
| Stages 5-7, 13, 14, 18 (LLM) | Gateway adapters via scripts (existing) | Run records under `workspace/data/llm_runs` | Adapter chosen by `LECTURE_STUDY_LLM_ADAPTER`; records hold counters only |
| ASR ground truth and WER | New pure module `src/lecture_study/demo/` | Reads `05_transcribed` artifacts | Pure function over text; no I/O inside the metric |
| Run-record metrics and report | New pure module + thin script | `docs/results/` committed text | Deterministic and testable from fixture JSON |
| Study guide bundle and site generation | Existing scripts and `site.generator` | `workspace/study-guides`, `workspace/site`, `workspace/site-videos` | Site reads only the public study-guide root |
| Local serving and signed-in view | Site server (`uvicorn`, existing app) | Local TLS material, demo credentials | Cookie is `Secure` + `__Host-`; needs HTTPS locally |
| ADR 0045 recovery demonstration | Existing derivative scripts + critic script | New tree-snapshot verifier | The gate itself is untouched; only the evidence is new |

## Standard Stack

### Core

No new dependency is required. Every tool below is already part of the repository stack or the operator machine.

| Library / Tool | Version | Purpose | Why Standard |
|----------------|---------|---------|--------------|
| mlx-audio (Kokoro-82M `mlx-community/Kokoro-82M-bf16`) | `>=0.5.4` in `pyproject.toml` [VERIFIED: pyproject.toml dependencies] | Narration | ADR 0032 engine; voices `pf_dora`, `pm_alex`, lang code `p`; cached locally [VERIFIED: `ls ~/.cache/huggingface/hub`] |
| Typst | 0.15.1 [VERIFIED: `typst --version`] | Slide PNGs from versioned source | Already a repository tool (`brew install typst` in provisioning and CI); `typst compile s.typ 'slide-{p}.png' --ppi 72` produced 1920x1080 PNGs [VERIFIED: probe] |
| ffmpeg / ffprobe | 9.0.2 [VERIFIED: `ffmpeg -version`] | Compose video, measure narration duration | Already used by extraction and the podcast stage |
| whisper.cpp `whisper-cli` + `ggml-large-v3-turbo.bin` | pinned commit `080bbbe8...` [VERIFIED: `scripts/01_provision_engines.sh:19`] | ASR (stage 3) | Existing stage |
| llama.cpp + Qwen2.5-14B Q4_K_M (3 shards) | pinned commit `91d2fc38...` [VERIFIED: `scripts/01_provision_engines.sh:20`] | Local adapter | Provisioned under `engines/` [VERIFIED: `ls -la engines/models`] |
| codex-cli | 0.155.1, "Logged in using ChatGPT" [VERIFIED: `codex --version`, `codex login status`] | Remote adapter | `CodexCliAdapter` (GW-02) |
| PyYAML + `ops/strict_yaml.load_strict_yaml` | existing | Load `script.yaml` | The repository's strict loader refuses aliases and explicit tags (Phase 6 hardening) |
| pydantic (strict models) | `>=2.13.4` | Typed demo-script and metric models | Project convention |
| httpx | `>=0.28.1` (dev group) | Scripted signed-in check | Used by `tests/test_site_app.py` |
| openssl | 3.6.5 [VERIFIED: `openssl version`] | Throwaway self-signed localhost certificate | uvicorn `--ssl-keyfile/--ssl-certfile` |

### Supporting

| Library / Tool | Version | Purpose | When to Use |
|----------------|---------|---------|-------------|
| `lecture_study.ops.process_lifecycle.run_bounded_process` | existing | All demo subprocesses | Every typst/ffmpeg/ffprobe/openssl call (finite timeout, output caps, env allowlist, no `shell=True`) |
| `lecture_study.llm.run_record.LlmRunRecord` | existing | Parse run records | Metric extraction (strict pydantic model) |
| `lecture_study.transcription.terminology.correct_specialist_terminology` | existing | Re-derive the "after" transcript or the no-prompt ablation | Only if the corrections file is not used |
| `lecture_study.site.access.hash_password` | existing | Demo allowlist entry | Called by a demo credentials helper (no other non-test caller exists, so a helper is needed) [VERIFIED: `rg "hash_password\("` outside tests: only the definition at `access.py:69`] |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Typst slides | Pillow, ffmpeg `drawtext`, a headless browser | Pillow is not installed [VERIFIED: `pip list` shows only numpy of the candidates]; `drawtext` needs a freetype build and gives no layout engine; a browser adds a runtime. Typst is already provisioned and OCR-verified on its output |
| Own ~25-line stdlib WER | `jiwer` | `jiwer` is not installed and adds a dependency to a public portfolio repository for a 25-line algorithm; keep stdlib, cross-check with hand-computed cases in tests. `jiwer` existence on PyPI is [ASSUMED] and was not legitimacy-checked because it is not recommended |
| Two lecture ids (one per adapter) | One id with artifact cleanup between adapters | One id requires deleting `07`, `08`, `09`, `14`, `15`, `16`, `18` and `llm_runs` between runs (resume predicates skip stages 5-7 otherwise) and risks losing the first adapter's records; two ids isolate everything |
| Local HTTPS with self-signed certificate | Plain HTTP on `localhost` | Cookie is `Secure` + `__Host-`; plain HTTP works only in some browsers (see Pitfall 6) |
| Parse orchestrator log lines for non-LLM wall time | Add `in %.3fs` to the orchestrator's "done" log line | Parsing needs no baseline change (log lines carry millisecond `asctime`); the log-format change gives monotonic-clock precision but touches Phase 1 code. Planner decision; either is acceptable |

**Installation:** none. `uv sync --group dev` already provides everything.

**Version verification:** versions above were read from the machine this session (`ffmpeg -version`, `typst --version`, `codex --version`, `openssl version`, `uv --version` = 0.13.0, `.venv/bin/python --version` = 3.12.13, `.venv/bin/pytest --version` = 9.1.1). No registry install is part of this phase.

## Package Legitimacy Audit

This phase installs no external package. The Standard Stack table lists only tools already present in `pyproject.toml`/`uv.lock` or Homebrew tools already required by provisioning and CI.

| Package | Registry | Age | Downloads | Source Repo | Verdict | Disposition |
|---------|----------|-----|-----------|-------------|---------|-------------|
| (none installed) | - | - | - | - | - | - |

**Packages removed due to [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none
`jiwer` was considered and rejected (see Alternatives); it is `[ASSUMED]` and must not be added without a legitimacy check and an operator decision.

## Architecture Patterns

### System Architecture Diagram

```
 demo/lecture/script.yaml  (single source: narration lines, slide heading + bullets)
        |                                   |
        |  yaml() in Typst                  |  load_strict_yaml -> DemoScript (typed)
        v                                   v
 slides.typ --typst--> slide-N.png     narration line(s) per slide
        |                                   |  Kokoro (mlx_audio.tts.generate, argv list, pm_alex|pf_dora)
        |                                   v
        |                               narration-N.wav  --ffprobe--> duration_N
        +---------------+-------------------+
                        v
              ffmpeg: still PNG + wav -> segment-N.mp4 -> concat (copy) -> workspace/demo/aula-demo.mp4
                        |
        register_local_lecture.py (x2 ids: 925100 local run, 925101 codex run; flags --publish --remote-llm-allowed)
                        |   -> workspace/data/01_raw/<id>.mp4 (symlink), queue.json row, config/local-lectures.local.yaml
                        v
   cli.main run-all <id>  (stages 2 extract+OCR, 3 whisper+terminology, 4 packs, 5 ledgers, 6 drafts,
        |                  7 critic+repair, 8 assembly, 9 metadata, then STAGE 10 refuses by design -> exit 1)
        v
   cli.main run-personal-study <id>  (8, 9 skipped by resume; 13 exam, 14 podcast script, 15 Kokoro audio)
        v
   generate_codex_personal_study_flashcards.py <id>      (stage 18, standalone)
        v
   generate_personal_study_guides.py <id> --targets-path ... --schedule-path ...  -> bundle in workspace/study-guides (video hard-linked)
        v
   generate_site()  -> workspace/site + workspace/site-videos
        v
   uvicorn lecture_study.site.app:app  --ssl-*  (127.0.0.1:8443)  <-- signed-in session (httpx jar or browser)

 Measurement (read-only over artifacts):
   run-all/personal-study stderr log --(timestamps)--> non-LLM stage wall time
   workspace/data/llm_runs/<id>/{semantic,drafting,critic,exams,podcast,flashcards}.json --> calls, retries, wall
   workspace/data/09_validated_units/<id>/unit_NNN.json (review.passed) + critic.json --> rejection rate
   05_transcribed/<id>_transcription.json + _terminology_corrections.json + script.yaml --> WER before/after
        v  harvest copy into workspace/demo/runs/<adapter>/  (BEFORE the recovery)
   demo.report  -> docs/results/demo-results.md + .json

 Recovery (DEMO-04, on the local-adapter id): touch draft_001.json OR flip unit review.passed
   -> 4 scripts exit 1, tree snapshot unchanged -> critic script rerun -> 4 scripts succeed
```

### Recommended Project Structure

```
demo/
├── lecture/
│   ├── script.yaml            # title, slides[{id, heading, bullets[], narration[]}] - single source of truth
│   └── slides.typ             # reads script.yaml through Typst yaml(); one distinct background per slide
└── site/
    └── academic-calendar.yaml # "entries: []" (versioned empty schedule for the guide/registration flags)
src/lecture_study/demo/
├── script.py                  # DemoScript pydantic model, reference_text(), narration lint (no digits/abbreviations)
├── wer.py                     # tokenization + word-level Levenshtein (S, D, I counts)
├── asr_measure.py             # raw-ASR reconstruction from corrections, before/after WER
├── run_metrics.py             # LlmRunRecord + unit files -> StageMetrics (pass rate, rejection rate, wall)
├── phase_timing.py            # parse orchestrator log lines (or consume the new timing suffix)
├── report.py                  # render Markdown + JSON, per adapter, honest "not run"
└── tree_snapshot.py           # snapshot/diff of a directory set for the "writes nothing" proof
scripts/
├── build_demo_lecture.py      # typst -> kokoro -> ffprobe -> ffmpeg (argv lists, bounded)
├── demo_site_access.py        # generate TLS cert, demo credentials, session secret into workspace/demo/
├── demo_signed_in_check.py    # httpx: GET /entrar, POST /entrar, GET lecture page with jar
├── demo_harvest_and_report.py # copy records/logs, call demo.report
└── demo_blocked_recovery.py   # induce stale/rejected, run the 4 scripts, diff, recover (operator-run)
docs/results/demo-results.md   # committed report (en-US) + demo-results.json
docs/decisions/0047-...md      # recommended: records method (reference text, WER definition, ids, harvest order)
tests/test_demo_*.py           # pure-logic tests; one slow/manual build probe
```

Pure logic (`wer`, `run_metrics`, `report`, `tree_snapshot`, `script`) depends only on the standard library and pydantic; subprocess work stays in `scripts/` and in the shared `tts` helper. This mirrors the repository rule that heavy logic lives in `src/` (mypy strict) and scripts stay thin.

### Pattern 1: Single-source script rendered three ways
**What:** `script.yaml` holds each slide's heading, bullets and narration lines. Typst renders slides from it with `yaml()`; the builder reads the same file for Kokoro; the reference text for WER is the concatenation of narration lines in order.
**When to use:** always; prevents slide text, narration and WER reference from drifting.
**Example:**
```typst
// Source: Typst docs data loading (yaml); probe this session used the equivalent hard-coded slides
#let script = yaml("script.yaml")
#set page(width: 1920pt, height: 1080pt, margin: 96pt)
#set text(size: 56pt, lang: "pt")
#for (index, slide) in script.slides.enumerate() {
  // one distinct page fill per slide index, see Pitfall 9
  page(fill: rgb(script.palette.at(calc.rem(index, script.palette.len())))[
    = #slide.heading
    #for bullet in slide.bullets [ - #bullet ]
  ]
}
```
The `yaml()` loader and the per-page `page()` call are [ASSUMED] syntax (the probe compiled a hard-coded three-slide `.typ` with `#pagebreak()` and `#set page(fill: ...)`); the planner should compile-test the final `slides.typ` as the first task.

### Pattern 2: One Kokoro call per slide, one line per sentence
Pass each slide's narration as one `--text` argument whose sentences are separated by `\n`, with `--join_audio`, exactly like `PodcastAudioGenerator`. Kokoro logs "Chunking logic not yet implemented, so long texts may be truncated unless you split them with '\n'" for `pt-br` [VERIFIED: probe stderr this session]. Keep each line short (one sentence) and rely on the newline split.

### Pattern 3: Harvest then recover
After the measured run of an adapter, copy `workspace/data/llm_runs/<id>/`, the stage log, the transcription artifact, the corrections file, and `09_validated_units/<id>/` into `workspace/demo/runs/<adapter>/` with `shutil.copy2`. Only then run the DEMO-04 recovery, because a successful whole-stage rerun replaces `critic.json` and deletes `critic.superseded.json` [VERIFIED: `run_record.py:255-264` and `_discard_superseded_record`].

### Pattern 4: "Writes nothing" is proven by a tree diff, not by absent directories
The first successful run leaves exam/podcast/flashcards/guide outputs on disk. A blocked rerun must leave every file under the lecture's output directories byte- and mtime-identical. Snapshot (relative path, size, sha256, `st_mtime_ns`) of `workspace/data/{14_mock_exams,15_podcast_scripts,16_podcast_audio,18_personal_study_flashcards,llm_runs}/<id>` and of the lecture bundle under `workspace/study-guides`, run the four scripts, snapshot again, assert equal. The blocked scripts raise before `record_llm_run` is entered, so no new run record appears either [VERIFIED: `generate_codex_mock_exam.py` `_run` calls `load_critic_approved_drafts` first; podcast and flashcards likewise; the guide script loads drafts at `generate_personal_study_guides.py:694` before staging].

### Anti-Patterns to Avoid
- **Faking the figure catalog:** do not hand-write `17_figure_captions/<id>/figures.json` to unlock `--personal-study-inference`; it misrepresents a stage that did not run (see Open Question 2).
- **`run-phase` for stages above 10:** `run_phase` requires a publication certificate for `spec.number > 10` and always fails here; stages 13-15 run through `run-personal-study` only [VERIFIED: `orchestrator.py:676-701`].
- **Copying artifacts with plain `cp`:** the stale-unit check compares `st_mtime_ns`; use `cp -p`/`shutil.copy2` for any restore, or the copy itself can create or hide a blocked state [VERIFIED: `assembler.py:168`].
- **A second adapter on the same id:** stages 5-7 are skipped by resume predicates and the run records are overwritten.
- **Pointing the pipeline at a non-default local registry:** the gateway egress policy always reads `LOCAL_LECTURES_PATH` (`config/local-lectures.local.yaml`); the orchestrator and scripts have no flag for another path [VERIFIED: `llm_gateway_wiring.py` `open_stage_gateway(..., local_lectures_path=LOCAL_LECTURES_PATH)`; `main.py` has no such flag].

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Slide images | Raster drawing code, ffmpeg `drawtext` chains | Typst `compile ... 'slide-{p}.png' --ppi 72` | Layout engine, diacritics, repository-provisioned; OCR on its PNGs verified |
| Speech synthesis | `say`, a new TTS subprocess wrapper | Public helper extracted from `PodcastAudioGenerator._synthesize_chunks` (same argv, `run_bounded_process`, `AudioGenerationError`) | ADR 0032/0015 discipline; avoids a duplicate subprocess implementation (R0.2) |
| Subprocess control | `subprocess.run(..., shell=True)` | `run_bounded_process(args, env=..., timeout_seconds=..., maximum_stdout_bytes=..., maximum_stderr_bytes=..., check=True)` | SPINE-03 |
| Config parsing | `yaml.safe_load` in new code | `load_strict_yaml` | Phase 6 refuses aliases and explicit tags (commit `3eb1d60`) |
| Run-record parsing | Ad hoc `json.load` | `LlmRunRecord.model_validate_json(text, strict=False)` | Extra-forbid strict model; identical to `_read_record` |
| Password hashes for the demo allowlist | Custom hashing | `hash_password` from `site/access.py` | PBKDF2-SHA256, 240000 iterations, existing format `email:hash` |
| Sign-in for the check | Basic Auth shortcut or a forged cookie | Real `GET /entrar` then `POST /entrar` with the CSRF field | The requirement says "signed-in session"; proves the cookie path |
| Certificate generation | Python crypto code | `openssl req -x509 ... -addext subjectAltName=IP:127.0.0.1` | One command, verified |
| Stage 8/9, guide, site generation | New assembly code | Existing `DocumentAssembler`, `MetadataAggregator`, `generate_personal_study_guides.py`, `generate_site()` | Already shipped; the phase exercises them |
| WER | (exception) | ~25 lines stdlib Levenshtein with S/D/I backtrace | Documented above; tested against hand-computed cases |

**Key insight:** the phase adds evidence and glue, not pipeline behavior. Any change to a baseline stage needs a reason recorded in the ADR; the two small ones recommended here (public Kokoro helper; optional orchestrator timing suffix) are additive.

## Runtime State Inventory

Not applicable: this is not a rename, refactor or migration phase. Section omitted.

## Common Pitfalls

### Pitfall 1: `run-all` never reaches stage 11 and exits 1 by design
**What goes wrong:** the operator or a verification script treats a non-zero exit of `run-all` as a failure.
**Why it happens:** stage 10 `issue_publication_certificate` always refuses; `QualityCertificateIssuer.issue` raises `QualityCertificationError` (a `RuntimeError`), the CLI prints one line and exits 1 [VERIFIED: `orchestrator.py:441-442`, `main.py:96-110`, `certification.py:53` "class QualityCertificationError(RuntimeError)"]; BND-01/BND-02 state this is intended.
**How to avoid:** define DEMO-02's `run-all` success as "stages 2-9 logged `done` or `skipping`, stage 10 refuses, artifacts `canonical_document.json` and `global_index.json` exist". `run-personal-study` is expected to exit 0 and runs stages 8, 9 (skipped by resume once `run-all` ran them), 13, 14, 15; stage 18 and the study guide are separate scripts.
**Warning signs:** a plan task that asserts exit code 0 for `run-all`.

### Pitfall 2: One evidence pack, one slide in the prompts, tiny critic sample
**What goes wrong:** the "critic rejection rate" is reported over one review, and 5 of the 6 slides never reach an LLM.
**Why it happens:** `MINIMUM_LEARNING_UNIT_DURATION_SECONDS = 300.0` and `MAXIMUM_LEARNING_UNIT_DURATION_SECONDS = 600.0` [VERIFIED: `chunker.py:24,26`]; a remainder of at most 600 s becomes the final (and only) unit, with `DurationExceptionReason.LECTURE_SHORTER_THAN_MINIMUM_UNIT = "lecture_shorter_than_minimum_unit"` when shorter than 300 s [VERIFIED: `chunker.py:85-98`, `models.py:162`]. `_representative_frame` keeps one frame, the one nearest the unit midpoint [VERIFIED: `chunker.py:270-293`], and the critic and draft prompts use `pack.anchor_frame.ocr_text` only [VERIFIED: `generate_codex_critic_review.py:251`].
**How to avoid:** keep D-05 (locked) but (a) put the key terminology on the slide that spans the midpoint, (b) report the denominator (reviews, units) next to every rate, (c) optionally take K extra critic-only repetitions per adapter (the critic script is rerunnable; local cost about 8 s per review [CITED: ADR 0046 Record table]). A lecture longer than 600 s would give at least two units; that conflicts with D-05 and is Open Question 1.
**Warning signs:** a report that prints a percentage without N.

### Pitfall 3: The dictionary is two regex families and the ASR prompt already fixes one
**What goes wrong:** the planted terms yield "0 corrections" and a WER delta of zero, or a delta that looks like a dictionary success but is really the ASR prompt.
**Why it happens:** the rules are `_HISTOPATHOLOGY_PATTERN = re.compile(r"\bestopatol(ógic|ogia)", re.IGNORECASE)` (unconditional) and a fascia rewrite built from `_FASCIA_NOUN = r"(camada|camadas|plano|planos|margem|margens)"` + `_FASCIA_ADJECTIVE = r"(facial|faciais)"`, applied only when the lecture pairs the phrase with `_DEPTH_CONTEXT_PATTERN` (`profundidade|abaixo|f[áa]scia|m[úu]sculo|musculatura|tumor`) inside a window of `_CONTEXT_WINDOW_SEGMENTS = 3` segments [VERIFIED: `terminology.py:60,61,73-76,82,86`]. Probe results this session with the pipeline's own whisper flags on Kokoro `pm_alex` audio of "A margem profunda deve ser avaliada na camada fascial. O exame histopatológico confirma a biópsia excisional do mastocitoma.":
```
WITH_PROMPT  -> A margem profunda deve ser avaliada na camada facial. O exame histopatológico confirma a biópsia excisional do mastocitoma.
NO_PROMPT    -> A margem profunda deve ser avaliada na camada facial. O exame estopatológico confirma a biópsia excisional do mastocitoma.
```
and `correct_specialist_terminology` on the WITH_PROMPT-style segment left `camada facial` untouched because "profunda" is not an anchor word, while `camada facial de profundidade` was corrected to `camada fascial` [VERIFIED: probes this session].
**How to avoid:** write `fascial` phrases (`camada/plano/margem fascial`) in the same sentence as an anchor word (`profundidade`, `abaixo`, `fáscia`, `músculo`, `tumor`); plant about 8-10 such phrases; plant `histopatológico` several times; report a 2x2 ablation (prompt on/off x dictionary on/off) so the `histopatológico` rule is also measured; add one negative control (`nervo facial`, no depth noun) to show the dictionary does not touch legitimate `facial`. Describe honestly that "before the dictionary" in the pipeline means "with the ASR prompt".
**Warning signs:** the corrections file is `[]`.

### Pitfall 4: Raw ASR text is not persisted
**What goes wrong:** the "before dictionary" transcript is unavailable after stage 3.
**Why it happens:** `_transcribe_lecture` writes only the corrected segments and a corrections list of `{start_time, original_text, corrected_text, term}`, one entry per rewritten term [VERIFIED: `pipeline.py:128-144, 191-199`].
**How to avoid:** reconstruct the raw segment texts by replacing, for each correction `start_time`, the corrected segment text by `original_text` (the field holds the full original segment text), or rerun `run_transcription(audio, model, initial_prompt=...)` for the ablation. State which method produced each number.

### Pitfall 5: Run records are per stage, latest-attempt, and deleted by a successful rerun
**What goes wrong:** the recovery overwrites the measured `critic.json`; a second adapter run on the same id overwrites everything.
**Why it happens:** path is `workspace/data/llm_runs/<lecture_id>/<script_stage>.json` [VERIFIED: `run_record.py:47,166-167`]; a whole-stage success removes the `.superseded.json` sibling [VERIFIED: `run_record.py:255-264`]; the orchestrator skips stages 5-7 when their sequences match (`_phase_5/6/7_artifact_exists`) [VERIFIED: `orchestrator.py:501-525`].
**How to avoid:** two ids, harvest before recovery (Pattern 3).

### Pitfall 6: Session cookie is `Secure` + `__Host-`; plain HTTP on localhost is unreliable
**What goes wrong:** the sign-in redirect succeeds but the next request is anonymous in some browsers, and `httpx`/`curl` cookie jars never send a `Secure` cookie over `http://`.
**Why it happens:** `COOKIE_NAME = "__Host-pos_session"`, `LOGIN_CSRF_COOKIE = "__Host-pos_login_csrf"`, `set_cookie(..., secure=True, samesite="lax")` [VERIFIED: `sessions.py:38`, `login_csrf.py:20`, `app.py:796-797`]. Browser behavior on plain `http://localhost`: one source says Chrome 89+ and Firefox 75+ ignore the HTTPS requirement for `Secure` on localhost, another reports Chrome rejecting `__Host-`/`__Secure-` cookies there, and a WebKit report says Safari does not store the cookie [CITED: web.dev/en/when-to-use-local-https; github.com/httpwg/http-extensions/issues/2605; www2.webkit.org/show_bug.cgi?id=232088 - sources conflict, treat as unreliable].
**How to avoid (verified):** generate a throwaway certificate and serve over HTTPS:
`openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 2 -subj "/CN=127.0.0.1" -addext "subjectAltName=IP:127.0.0.1,DNS:localhost"`, then `uvicorn lecture_study.site.app:app --host 127.0.0.1 --port 8443 --ssl-keyfile key.pem --ssl-certfile cert.pem`. A full `GET /entrar` -> `POST /entrar` (303) -> `GET /index.html` (200) with an `httpx.Client(verify="cert.pem")` cookie jar, and an anonymous request returning 401, were observed this session [VERIFIED: probe]. The site sends `Strict-Transport-Security: max-age=31536000` [VERIFIED: `security_headers.py:24,69`]; browse `https://127.0.0.1:8443` (an IP literal is not subject to HSTS per RFC 6797 section 8.1 [ASSUMED]) rather than `localhost`, to avoid pinning HSTS for every localhost port in the operator's browser. A browser will show a certificate warning once.

### Pitfall 7: Study guide lands in the private root unless the lecture is published; the script needs catalog and schedule files
**What goes wrong:** the site shows no page for the demo lecture.
**Why it happens:** `select_output_root` sends an unpublished local lecture to `workspace/study-guides-local`, which the deploy and the site generator never read; the site reads `STUDY_GUIDES_ROOT = Path("workspace/study-guides")` only [VERIFIED: `local_lectures.py:58,271-286`, `catalog.py:17`]. The guide script and the registration script default `--targets-path` to `config/targets.local.yaml` and `--schedule-path` to `config/academic-calendar.local.yaml`, both absent here [VERIFIED: `ls config`], and `load_academic_schedule` raises on a missing file or a payload without an `entries` list [VERIFIED: `academic_catalog.py:161-173`].
**How to avoid:** register with `--publish`; pass `--targets-path config/targets.example.yaml` (versioned, ids 10001-10003 do not collide with 925xxx) and `--schedule-path demo/site/academic-calendar.yaml` (`entries: []`; local lectures are merged into the schedule in memory by `merge_local_lectures`) to both scripts; the guide script's `lecture_ids` filter means the example catalog's own lessons without a schedule entry are not validated [VERIFIED: `academic_catalog.py:187-210`].

### Pitfall 8: Kokoro truncation, numbers and abbreviations
**What goes wrong:** long narration is cut; digits, units and abbreviations are pronounced or transcribed unpredictably and inflate WER with non-terminology errors.
**Why it happens:** Kokoro `pt-br` has no chunking (warning text above); whisper may write numerals for number words.
**How to avoid:** a `DemoScript` lint in `script.py`: no digits, no abbreviations or units, no hyphenated compounds, one sentence per narration line, each line under about 200 characters, no English words. Normalize for WER with NFC + `casefold()` + word tokens, and state the rule in the report.

### Pitfall 9: Scene detection must see six distinct slides
**What goes wrong:** two similar slides produce one frame; the OCR evidence loses a slide.
**Why it happens:** the frame filter is `select='isnan(prev_selected_t)+gt(scene,0.3)+gte(t-prev_selected_t,37.5)',showinfo` [VERIFIED: `media_workers.py:28-31`]. With three slides of clearly different backgrounds the probe produced three frames at 0.043, 5.368 and 11.968 s with OCR text recovered exactly (`'Biópsia excisional\n• Remoção completa da lesão\n• Margem lateral e margem profunda'`) [VERIFIED: probe through the repository's own `extract_frames` and `extract_text_supervised`]. A slide longer than 37.5 s also yields a duplicate "safety" frame, which is harmless.
**How to avoid:** one distinct background colour per slide; add an assertion in the build script that `extract_frames` finds at least the slide count; keep hard cuts (no fades).

### Pitfall 10: Stages 8 and 9, the guide and the temporal-coverage check have never run on lecture data in this repository
**What goes wrong:** first-run defects surface only after the operator spent LLM time.
**Why it happens:** STATE.md records "Stages 8 and 9 have never run on lecture data" [CITED: STATE.md Blockers/Concerns]. The guide script also runs `build_temporal_coverage_report`, whose tolerances are `_EDGE_GAP_TOLERANCE_SECONDS = 60.0` and `_INTERNAL_GAP_TOLERANCE_SECONDS = 5.0` and which special-cases a single short pack [VERIFIED: `temporal_coverage.py:25-27,470-475`]; it also flags dosage words (`"dose"`, `"mg/"`) [VERIFIED: `temporal_coverage.py:34-35`] so the script should avoid dosage language.
**How to avoid:** schedule an early dry-run task: hand-author schema-valid `07`/`08`/`09` fixtures for one scratch id in a throwaway working copy (or a unit test with `tmp_path` roots, since the scripts accept `--*-root` flags) and run stage 8, stage 9, the guide and `generate_site` on them before any LLM run.

### Pitfall 11: `--personal-study-inference` needs a figure catalog that this machine cannot produce
**What goes wrong:** the guide command fails with "catálogo de legendas de figuras ausente".
**Why it happens:** with the flag set, `_load_figure_catalog` requires `17_figure_captions/<id>/figures.json`, produced by `scripts/caption_lecture_figures.py` with an attested Qwen3-VL-8B model and projector in `config/actors.local.json` [VERIFIED: `generate_personal_study_guides.py:351-363`, `caption_lecture_figures.py:35-50`]. `engines/models/` holds only the Qwen2.5-14B text shards [VERIFIED: `ls -la engines/models`]; a shallow `find ~ -maxdepth 4 -name '*mmproj*'` found nothing [ASSUMED beyond that depth]. Without the flag the bundle uses deterministic templates and macOS `say` for its podcast file, while the LLM exam is still served by the quiz page [VERIFIED: `generate_personal_study_guides.py:1342` (`_write_bundle` branches on `inference_podcast_script`) and `site/generator.py:198` (`has_interactive_quiz` reads `exam.json`)].
**How to avoid:** see Open Question 2.

### Pitfall 12: The stale check is mtime based and the study-guide script prints a traceback
**What goes wrong:** the demonstration is flaky or the output is mistaken for a crash.
**Why it happens:** a draft whose `st_mtime_ns` exceeds its unit's blocks the lecture with "changed after its critic review"; a unit with `review.passed` false blocks with "has an unresolved Critic review" [VERIFIED: `assembler.py:156-171, 209-218`]. The exam and flashcard scripts catch `_CRITIC_GATE_ERRORS = (LectureNotFoundError, MissingSequenceError, UnresolvedReviewError)` and print a Portuguese message with exit 1; the podcast script catches the same three; `generate_personal_study_guides.py` does not catch them in `main()` so it exits 1 with a traceback [VERIFIED: `generate_codex_mock_exam.py:79-83,150-160`; `generate_codex_podcast.py:158-165`; guide `main()` has no `except`].
**How to avoid:** the verifier asserts exit code 1 and a tree diff of zero changes for all four; it does not parse stderr beyond the exception class name. Induce staleness with `os.utime` or `touch` on a draft (cheap and deterministic), and rejection by rewriting `review.passed` to `false` in `unit_001.json` (an honest simulation: the real critic verdict cannot be forced).

### Pitfall 13: Remote adapter is an operator action with egress implications
**What goes wrong:** an automated executor starts sending lecture text to a third-party service.
**Why it happens:** `CodexCliAdapter` runs `codex exec` on the operator's ChatGPT account; the lecture must carry `remote_llm_allowed: true` in the registry [VERIFIED: `codex_adapter.py` argv; `remote_llm_policy.py`]. The content is synthetic by construction, but the provider terms are unrecorded [CITED: ADR 0045 section 2].
**How to avoid:** make the codex run a human-action checkpoint in the plan; the make target prints which adapter and which lecture id before starting.

## Code Examples

Verified patterns; probes were run in the session scratchpad, not in the repository tree.

### 1. Kokoro narration (argv identical to the shipped generator)
```python
# Source: src/lecture_study/tts/generator.py:59-62 and 159-190 (read this session)
# _VOICE_BY_SPEAKER: dict[str, str] = {"Host": "pf_dora", "Expert": "pm_alex"}
# _KOKORO_MODEL_ID = "mlx-community/Kokoro-82M-bf16"
# _KOKORO_LANGUAGE_CODE = "p"
command = [
    sys.executable, "-m", "mlx_audio.tts.generate",
    "--model", "mlx-community/Kokoro-82M-bf16",
    "--lang_code", "p",
    "--voice", "pm_alex",            # or "pf_dora"
    "--text", narration_text_with_newlines,
    "--output_path", str(output_dir),
    "--file_prefix", "narration-01",  # writes narration-01.wav
    "--join_audio",
]
run_bounded_process(command, env=sanitized_child_process_environment(),
                    timeout_seconds=300.0, maximum_stdout_bytes=1024 * 1024,
                    maximum_stderr_bytes=8 * 1024 * 1024, check=True)
```
Observed: output `pcm_s16le`, 24000 Hz, mono, 7.825 s for 21 words (about 160 words/min, so roughly 640 words for 4 minutes) [VERIFIED: probe]; the same command succeeds from an `env -i` environment with only `PATH`, `LANG`, `TMPDIR` (no `HOME`), which is what the sanitized environment provides [VERIFIED: probe; `process_environment.py:11-21`]. Recommended: extract the loop body into a public `synthesize_kokoro_wav(text, voice, output_dir, file_prefix) -> Path` in the same module and have `_synthesize_chunks` call it; `tests/test_tts.py` already covers the module.

### 2. Slides from Typst
```bash
# Source: probe this session (typst 0.15.1)
typst compile demo/lecture/slides.typ 'workspace/demo/build/slide-{p}.png' --ppi 72
# 1920pt x 1080pt pages -> 1920 x 1080 PNG (file reported: "PNG image data, 1920 x 1080")
```

### 3. Compose per-slide segments and concatenate
```bash
# Source: probe this session (ffmpeg 9.0.2); duration of each segment = its narration
ffmpeg -y -loglevel error -loop 1 -framerate 25 -i slide-1.png -i narration-1.wav \
  -c:v libx264 -tune stillimage -pix_fmt yuv420p -r 25 -c:a aac -ar 24000 -shortest segment-1.mp4
printf "file 'segment-1.mp4'\nfile 'segment-2.mp4'\n" > list.txt
ffmpeg -y -loglevel error -f concat -safe 0 -i list.txt -c copy aula-demo.mp4
# probe result: h264 1920x1080 + aac, duration 15.47 s for 3 segments (5.325 + 6.600 + 3.500 s of speech)
```
Add `-movflags +faststart` for the watch page and a short pad (for example `-af apad=pad_dur=0.5` with an explicit `-t`) so slide cuts do not clip speech; the exact pad is a planner detail not yet probed.

### 4. Verify the build against the repository's own extraction
```python
# Source: probe this session; functions exist at extraction/media_workers.py and extraction/ocr_supervisor.py
frames = await extract_frames(Path("workspace/demo/aula-demo.mp4"), scratch_dir)   # list[(timestamp, path)]
ocr = extract_text_supervised(frames[0][1])  # 'Biópsia excisional\n• Remoção completa da lesão\n...'
```

### 5. Registration and runs (one id per adapter)
```bash
# Source: register_local_lecture.py argparse (read this session); main.py subcommands
uv run python scripts/register_local_lecture.py 925100 workspace/demo/aula-demo.mp4 \
  --title "Biopsia excisional: margens e planos fasciais (demonstracao sintetica)" \
  --discipline "Cirurgia veterinaria (demonstracao)" --semester 2026.2 \
  --targets-path config/targets.example.yaml --schedule-path demo/site/academic-calendar.yaml \
  --publish --remote-llm-allowed
# same call with 925101 for the codex run (same source video; each id gets its own symlink)

LECTURE_STUDY_LLM_ADAPTER=llama-cpp uv run python -m lecture_study.cli.main run-all 925100 \
  --whisper-model-path engines/whisper.cpp/models/ggml-large-v3-turbo.bin 2> workspace/demo/runs/llama-cpp/run-all.log
# expected: stages 2-9 done, stage 10 refuses, exit 1
LECTURE_STUDY_LLM_ADAPTER=llama-cpp uv run python -m lecture_study.cli.main run-personal-study 925100 \
  --whisper-model-path engines/whisper.cpp/models/ggml-large-v3-turbo.bin 2> workspace/demo/runs/llama-cpp/personal-study.log
LECTURE_STUDY_LLM_ADAPTER=llama-cpp uv run python scripts/generate_codex_personal_study_flashcards.py 925100
```
Titles and disciplines are ASCII here only to keep the command portable; pt-BR text with accents is valid (`LocalLecture` accepts printable text). `LECTURE_STUDY_LLM_ADAPTER` replaces the default adapter and every per-stage entry for that run [VERIFIED: `llm/config.py:39,93-100,165`]; `config/llm-gateway.local.yaml` already exists with `default_adapter: codex` and the Qwen2.5-14B shard path [VERIFIED: file read this session; git-ignored by `config/*.local.yaml`], so only the environment variable changes between adapters.

### 6. Metric derivations from run records
```python
# Source: derived from json_completion.py and run_record.py (read this session); formulas are [ASSUMED] design,
# valid because complete_json issues exactly one retry per failed first attempt and never retries gateway errors.
first_attempts = usage.calls - usage.retry_calls
first_pass_schema_rate = 1 - usage.retry_calls / first_attempts if first_attempts else None
# stage 7 only (critic.json carries two GatewayUsage entries: stage "critic" and stage "drafting"):
reviews = critic.calls - critic.retry_calls
redrafts = drafting.calls - drafting.retry_calls          # one per rejected review that was repaired
final_rejected = sum(1 for unit in units if not unit.review.passed)
rejected_reviews = redrafts + final_rejected
critic_rejection_rate = rejected_reviews / reviews
```
`GatewayUsage` fields: `stage`, `adapter_name`, `model_identifier`, `model_sha256`, `engine_version`, `calls`, `retry_calls`, `call_seconds`; `LlmRunRecord` fields: `lecture_id`, `script_stage`, `schema_name`, `status` (`"succeeded"` | `"failed"`), `scope`, `started_at`, `finished_at`, `wall_seconds`, `gateways` [VERIFIED: `run_record.py:58-103`]. Stage values and record file names: `semantic`, `drafting`, `critic`, `exams`, `podcast`, `flashcards` [VERIFIED: `gateway.py:22-30`]. For stages 13/14/18 the "schema" includes the student-facing text guard inside the same retry, so the report column should be named "first-attempt contract pass rate" and say so [VERIFIED: `generate_codex_podcast.py` `_validate_script` -> `_reject_text_unfit_for_a_listener`]. `CriticReview` also carries `hallucination_score` (0.0-1.0), a free extra metric [VERIFIED: `models.py:243`]. Report `status: failed` records as failures, never as a rate.

### 7. WER (stdlib)
```python
# Source: standard word-level Levenshtein; [ASSUMED] implementation, to be unit-tested against hand-computed cases
def tokens(text: str) -> list[str]:
    return re.findall(r"\w+(?:[-']\w+)*", unicodedata.normalize("NFC", text).casefold())
# WER = (substitutions + deletions + insertions) / len(reference_tokens); DP table + backtrace for S, D, I.
```
Reference = `script.yaml` narration lines joined in order; hypothesis = concatenated `segments[].text` of the transcription artifact (after) or of the reconstructed raw segments (before).

### 8. Local HTTPS serving and the signed-in check
```python
# Source: probe this session; flow mirrors tests/test_site_app.py (_with_csrf, Cookie header)
with httpx.Client(base_url="https://127.0.0.1:8443", verify="workspace/demo/tls/cert.pem") as client:
    form = client.get("/entrar")                      # 200, sets __Host-pos_login_csrf
    token = re.search(r'value="([0-9a-f]{64})"', form.text).group(1)   # csrf_token hidden field
    login = client.post("/entrar", data={"email": demo_email, "password": demo_password,
                                         "csrf_token": token, "next": lecture_page_path})   # 303 + __Host-pos_session
    page = client.get(lecture_page_path)              # 200 with the jar; anonymous request -> 401
```
The CSRF regex above matched in the probe; the planner should parse the hidden `csrf_token` input robustly. Lecture page path: `workspace/site/<Lecture.relative_path>/index.html` (`_write_lecture` writes `lecture_output_dir / "index.html"`; `assistir.html` beside it) [VERIFIED: `generator.py:178-196`]; assert the page text contains `UNCERTIFIED_NOTICE` from `site/renderer.py` (ADR 0045 section 3).
Credentials and secret for the demo are generated by a helper into `workspace/demo/`, with `LECTURE_STUDY_SITE_CREDENTIALS`, `LECTURE_STUDY_SITE_SESSION_KEY` and `LECTURE_STUDY_SITE_MOCK_EXAMS` pointing there/at `workspace/data/14_mock_exams` [VERIFIED env names: `access.py:24`, `signing.py:27`, `quiz.py:37`]. The credentials format is one `email:hash` per line; the session secret must be at least 32 bytes [VERIFIED: `access.py` docstring, `signing.py:MINIMUM_SECRET_BYTES = 32`]. Never overwrite an existing `config/site-credentials.txt` and never write the demo password to disk or to a committed file; print it once.

### 9. Guide, site, serve
```bash
uv run python scripts/generate_personal_study_guides.py 925100 \
  --targets-path config/targets.example.yaml --schedule-path demo/site/academic-calendar.yaml
uv run python -c "from lecture_study.site.generator import generate_site; generate_site()"
# generate_site has no CLI; its only other caller is infra/docker-entrypoint.sh [VERIFIED: rg this session]
LECTURE_STUDY_SITE_CREDENTIALS=workspace/demo/site-credentials.txt LECTURE_STUDY_SITE_SESSION_KEY=workspace/demo/site-session-key \
  uv run uvicorn lecture_study.site.app:app --host 127.0.0.1 --port 8443 \
  --ssl-keyfile workspace/demo/tls/key.pem --ssl-certfile workspace/demo/tls/cert.pem
```
`generate_site` hard-links bundle files into `workspace/site` and falls back to copy; the bundle's video is hard-linked from the raw video, so the source video and `workspace/` must be on one filesystem (`workspace/demo/aula-demo.mp4` satisfies this) [VERIFIED: `generator.py:1-12, 255-260`; `_link_video_or_explain`].

### 10. DEMO-04 sequence (local adapter id)
```bash
# 0. harvest measurements first (Pattern 3)
# 1. stale unit
touch workspace/data/08_drafts/925100/draft_001.json
for script in generate_codex_mock_exam generate_codex_podcast generate_codex_personal_study_flashcards; do
  LECTURE_STUDY_LLM_ADAPTER=llama-cpp uv run python scripts/$script.py 925100; echo "exit=$?"; done   # expect exit=1 each
uv run python scripts/generate_personal_study_guides.py 925100 --targets-path ... --schedule-path ...   # expect exit=1, traceback
# tree snapshot before/after must be identical
# 2. recovery (ADR 0045 Consequences): delete units that no longer have a draft (none in this scenario), then
LECTURE_STUDY_LLM_ADAPTER=llama-cpp uv run python scripts/generate_codex_critic_review.py 925100
# 3. rerun the four scripts: exam.json, script.json, deck.json, bundle appear/refresh
```
For the rejected variant rewrite `review.passed` to false in `unit_001.json` (its mtime then exceeds the draft's, so only the unresolved-review branch fires), repeat steps 1-3. The critic script re-reviews every draft and rewrites every unit [VERIFIED: `generate_codex_critic_review.py:148-168`]; a repair-loop redraft is written only into the unit, never into `08_drafts`.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| macOS `say` for podcast audio | Kokoro-82M via mlx-audio | ADR 0032, 2026-09-15 operator feedback "robotico" [CITED: `tts/generator.py` comments] | Demo narration uses Kokoro; the guide script's deterministic podcast path still uses `say` |
| Direct Codex spawn in each script | `LlmGateway` port with Codex and llama.cpp adapters, run records | Phase 6, ADR 0046 | Per-stage counters and wall time exist without new instrumentation |
| Local text LLM removed | Local text LLM returns only as `LlamaCppAdapter` | ADR 0046 amends ADR 0038 | Makes the two-adapter measurement possible |
| `ffmpeg -vsync` | `-fps_mode vfr` | ffmpeg 9 removed `-vsync` [CITED: `media_workers.py:124-128` comment] | Any new ffmpeg command must use current option names |

**Deprecated/outdated:** none specific to this phase beyond the above.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Typst `yaml()` plus a per-page `page(fill: ...)[...]` loop compiles as sketched | Pattern 1 | The slide source needs a different loop form; low impact, caught by the first build task |
| A2 | IP literals are exempt from HSTS (RFC 6797 section 8.1), so `https://127.0.0.1:8443` avoids pinning HSTS for `localhost` | Pitfall 6 | The operator's browser may pin HSTS for the host; mitigated by clearing the HSTS entry; cosmetic |
| A3 | First-attempt metric formulas (retries equal first-attempt failures) hold for every stage | Code Example 6 | A stage that wraps `complete_json` in another retry would miscount; verify per script when writing `run_metrics.py` (all six scripts were seen to call `complete_json` once per request) |
| A4 | No Qwen3-VL/mmproj file exists elsewhere on the machine beyond `find ~ -maxdepth 4` | Pitfall 11 | The vision stage might be runnable after all; operator can answer Open Question 2 |
| A5 | `jiwer` exists on PyPI and is the conventional WER package | Alternatives | Irrelevant unless adopted; not recommended |
| A6 | Whisper large-v3-turbo output on the 4-minute Kokoro audio will contain the `facial` mishearing at a useful rate (one sentence probed) | Pitfall 3 | The WER delta may be small or zero; the report states the measured value whatever it is |
| A7 | Qwen2.5-14B passes the exam/podcast/flashcard contracts (student-facing guard, pt-BR) within one retry | Summary | A failed stage run is a legitimate measured result, but it blocks DEMO-02 until the operator decides; plan a contingency task |
| A8 | The dry run of stages 8, 9, the guide and the site on hand-authored fixtures will expose first-run defects cheaply | Pitfall 10 | If defects only appear on real LLM output, they surface during the operator run |
| A9 | Adding a public Kokoro helper to `tts/generator.py` keeps `tests/test_tts.py` green | Code Example 1 | If tests patch private names, adapt the tests in the same commit |

## Open Questions

1. **Lecture length versus a meaningful critic rejection rate (D-05 is locked at about 4 minutes)**
   - What we know: at most 600 s gives exactly one evidence pack, one critic review plus repair attempts, one slide's OCR in the prompts (Pitfall 2); the local adapter costs about 40 s, 16 s and 8 s for stages 5, 6, 7 on one pack [CITED: ADR 0046 Record table].
   - What's unclear: whether the operator accepts N=1 per adapter, or wants a lecture above 600 s (two or more units) or repeated critic runs.
   - Recommendation: keep D-05, report N beside every rate, and add an optional `--critic-repetitions K` harvest step; ask the operator only if K repetitions are unacceptable.

2. **Figure catalog for `--personal-study-inference`**
   - What we know: the flag needs `17_figure_captions/<id>/figures.json` from the Qwen3-VL-8B pair, which is not under `engines/models/` (Pitfall 11).
   - What's unclear: whether the operator will provision and attest that pair for the demo.
   - Recommendation: default the demo guide to no inference flag (templates in the bundle, LLM exam still served by the quiz page) and document the limitation; do not forge a figure catalog. If the operator provisions the pair, add `caption_lecture_figures.py` and the flag as an optional extra.

3. **Which id is the DEMO-04 subject**
   - Recommendation: the local-adapter id (cheap, no remote egress), after harvesting.

4. **ADR for the method**
   - Recommendation: a short ADR 0047 recording the reference-text rule, WER definition, two-id strategy, harvest-before-recovery order and the N=1 limitation; the repository records such decisions in ADRs and R2.1 requires documentation updates with architecture changes.

5. **Voice**
   - `pm_alex` was probed; `pf_dora` is equally valid under ADR 0032. Planner picks one narrator (a monologue lecture does not need both).

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| ffmpeg, ffprobe | build, stage 2, stage 15, guide | yes (`/opt/homebrew/bin`) | 9.0.2 | none needed |
| typst | slides, guide PDFs | yes (`/opt/homebrew/bin`) | 0.15.1 | none needed |
| macOS `say` | guide script deterministic podcast path | yes (pt_BR voices Luciana, Reed listed) | - | none needed |
| whisper-cli + `ggml-large-v3-turbo.bin` | stage 3 | yes (`engines/whisper.cpp`, 1.6 GB model) | pinned commit | none |
| llama-completion, llama-tokenize, Qwen2.5-14B Q4_K_M (3 shards) | local adapter | yes (`engines/`) | pinned commit | none |
| Kokoro-82M cache | narration, stage 15 | yes (`~/.cache/huggingface/hub`, `mlx-community--Kokoro-82M-bf16`) | - | first run needs network if the cache is cleared |
| codex-cli | remote adapter | yes, logged in | 0.155.1 | skip the codex adapter and report "not run" |
| Apple Vision OCR (pyobjc) | stage 2 | yes (probe returned exact slide text) | - | tesseract is absent and not needed |
| openssl | local TLS | yes | 3.6.5 | any `mkcert` install |
| uv | make targets | yes | 0.13.0 | `.venv/bin/python` (ADR 0046 Record notes `uv run` was unavailable on another machine) |
| Qwen3-VL-8B + mmproj | optional figure captions | no (only Qwen2.5-14B under `engines/models/`) | - | omit `--personal-study-inference` (Open Question 2) |
| Machine | all | Apple M5 Pro, 48 GiB (`hw.memsize` 51539607552), macOS 27.0.1 | - | - |

**Missing dependencies with no fallback:** none.
**Missing dependencies with fallback:** Qwen3-VL pair (omit the inference flag).

## Validation Architecture

`.planning/config.json` does not exist, so `workflow.nyquist_validation` is treated as enabled [VERIFIED: `.planning/` listing this session].

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.1.1 (+ pytest-asyncio, pytest-cov), `testpaths = ["tests"]` [VERIFIED: pyproject.toml, `.venv/bin/pytest --version`] |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` |
| Quick run command | `uv run pytest tests/test_demo_*.py -q` |
| Full suite command | `uv run pytest -q` (baseline 2262 passed in about 38 s [CITED: STATE.md]) plus `uv run ruff check . && uv run ruff format --check . && uv run mypy src` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| DEMO-01 | `script.yaml` validates strictly; lint rejects digits, abbreviations, long lines; reference text equals joined narration | unit | `uv run pytest tests/test_demo_script.py -x` | no, Wave 0 |
| DEMO-01 | Composition command builders emit argv lists (no `shell=True`), segment durations follow wav durations | unit | `uv run pytest tests/test_demo_build.py -x` | no, Wave 0 |
| DEMO-01 | Slides compile, `extract_frames` finds at least 6 frames, OCR returns each heading, video duration within the target band | integration (needs typst, ffmpeg, Apple Vision; CI macOS has typst and ffmpeg) | `uv run pytest tests/test_demo_build.py -k slides -x` | no, Wave 0 |
| DEMO-01 | Full `make demo-video` rebuild (Kokoro + ffmpeg) | manual-only: needs the cached Kokoro model and 1-2 minutes; verified once by the operator with `ffprobe` | `make demo-video && ffprobe -v error -show_entries format=duration -of csv=p=0 workspace/demo/aula-demo.mp4` | n/a |
| DEMO-02 | Phase log parser extracts per-phase seconds and the stage-10 refusal; artifact checklist for stages 2-9 | unit | `uv run pytest tests/test_demo_phase_timing.py -x` | no, Wave 0 |
| DEMO-02 | Signed-in check against the in-process app over HTTPS `testserver` (fixture site tree) returns 200 with the uncertified notice and 401 anonymously | unit/integration | `uv run pytest tests/test_demo_signed_in_check.py -x` | no, Wave 0 |
| DEMO-02 | Real `run-all` + `run-personal-study` + guide + site on the live machine | manual-only (operator): LLM and ASR time; evidence = logs and artifacts | `make demo-run-local` (name is planner discretion) | n/a |
| DEMO-03 | WER cases (identical, one substitution, deletion, insertion, empty hypothesis), normalization | unit | `uv run pytest tests/test_demo_wer.py -x` | no, Wave 0 |
| DEMO-03 | Raw-ASR reconstruction from a corrections fixture; before/after WER; ablation wiring | unit | `uv run pytest tests/test_demo_asr_measure.py -x` | no, Wave 0 |
| DEMO-03 | Run-record metrics from fixture JSON: pass rate, rejection rate, failed and superseded records, missing codex run reported as "not run" | unit | `uv run pytest tests/test_demo_run_metrics.py tests/test_demo_report.py -x` | no, Wave 0 |
| DEMO-04 | Tree snapshot/diff detects any create, modify or delete, and equality passes | unit | `uv run pytest tests/test_demo_tree_snapshot.py -x` | no, Wave 0 |
| DEMO-04 | Existing gate behavior (blocked lecture, four scripts) is already covered by `tests/test_assembly.py`, `tests/test_generate_codex_*`, `tests/test_personal_study_generator.py`; no duplicate test | unit (existing) | `uv run pytest tests/test_assembly.py tests/test_generate_codex_mock_exam.py tests/test_generate_codex_personal_study_flashcards.py tests/test_generate_codex_podcast.py -q` | yes |
| DEMO-04 | Live blocked-then-recovered sequence on the demo lecture | manual-only (operator): needs local adapter | `scripts/demo_blocked_recovery.py` | n/a |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/test_demo_*.py -q` and `uv run ruff check . && uv run ruff format --check . && uv run mypy src`
- **Per wave merge:** `uv run pytest -q`
- **Phase gate:** full suite green, then `/gsd-code-review` and `/gsd-secure-phase` on the phase-final diff (operator rule R0.4), then `/gsd-verify-work`; the manual evidence (logs, results report, signed-in check output) is committed or attached per the plan.

### Wave 0 Gaps
- [ ] `tests/test_demo_script.py`, `tests/test_demo_build.py`, `tests/test_demo_wer.py`, `tests/test_demo_asr_measure.py`, `tests/test_demo_run_metrics.py`, `tests/test_demo_report.py`, `tests/test_demo_phase_timing.py`, `tests/test_demo_signed_in_check.py`, `tests/test_demo_tree_snapshot.py` - none exist
- [ ] Fixture JSON for run records (succeeded, failed, retries, superseded) under `tests/` fixtures
- [ ] Framework install: none (pytest present)

## Security Domain

`security_enforcement` is not set to `false` anywhere (no `.planning/config.json`), so this section applies.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes (demo allowlist, sign-in) | Existing `hash_password` PBKDF2-SHA256 240000 iterations; random demo password printed once, only the hash stored in a gitignored file |
| V3 Session Management | yes | Existing signed `__Host-pos_session` cookie, `Secure`, `HttpOnly`, `SameSite=Lax`; local HTTPS so the cookie works |
| V4 Access Control | yes | Existing email allowlist; the check proves anonymous access gets 401 |
| V5 Input Validation | yes | `SafeIdentifier` on every id, strict models for `script.yaml`, `load_strict_yaml`, argv lists (narration text reaches Kokoro as one opaque argument, never a shell string) |
| V6 Cryptography | partly | Never hand-roll: `hash_password`, `openssl` for the throwaway certificate; session secret from `secrets` (at least 32 bytes) |
| V12 Files | yes | Demo artifacts under `workspace/demo/` (gitignored); no write outside the repository workspace; hard-link semantics noted |
| V14 Configuration | yes | Nothing secret committed; existing `config/*.local.yaml`, `config/secrets/`, `*.pem`, `*.key` ignore rules |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Demo credentials or session key committed (R0.1) | Information disclosure | Generate at run time into `workspace/demo/`; never overwrite `config/site-credentials.txt`; do not write the password to disk or logs; the pre-commit and CI scans stay in force |
| Command injection through narration text | Tampering | Argument lists only, bounded process helper, one opaque `--text` argument (SPINE-03, ADR 0015) |
| Path traversal through lecture id | Tampering | `SafeIdentifier` at every entry (SPINE-02); ids restricted to 925100-925299 for local lectures |
| Remote egress of lecture content | Information disclosure | Registry flag `remote_llm_allowed`, fail-closed egress check, operator responsibility for the synthetic claim; provider terms unchecked (ADR 0045 section 2); codex run is a human-action checkpoint |
| Overwriting the operator's real local registry or credentials | Tampering | The registry path is fixed by the gateway wiring, so the plan must check the registry is empty or demo-only before registering and never delete other rows; demo site files use `workspace/demo/` paths via environment variables |
| HSTS pinned on `localhost` | Denial of service (dev ergonomics) | Browse the IP literal `https://127.0.0.1:8443`; certificate limited to two days |
| Stale-check bypass by copying artifacts | Tampering with the gate | Use `copy2`/`cp -p`; the verifier asserts the gate, it does not weaken it |

## Sources

### Primary (HIGH confidence)
- Repository files read in full this session: `.planning/phases/07-.../07-CONTEXT.md`, `.planning/REQUIREMENTS.md`, `.planning/STATE.md`, `.planning/ROADMAP.md` (Phase 7 section), `src/lecture_study/cli/main.py`, `cli/orchestrator.py`, `cli/llm_gateway_wiring.py`, `llm/config.py`, `llm/gateway.py`, `llm/json_completion.py`, `llm/run_record.py`, `llm/codex_adapter.py`, `llm/llama_cpp_adapter.py` (first 120 lines), `transcription/terminology.py`, `transcription/pipeline.py`, `transcription/whisper_worker.py`, `extraction/pipeline.py`, `extraction/media_workers.py`, `evidence/chunker.py`, `tts/generator.py`, `assembly/assembler.py`, `study_materials/local_lectures.py`, `study_materials/remote_llm_policy.py`, `study_materials/academic_catalog.py` (relevant ranges), `site/app.py`, `site/access.py`, `site/sessions.py`, `site/signing.py`, `site/login_csrf.py`, `site/generator.py`, `site/catalog.py` (head), `scripts/register_local_lecture.py`, `scripts/generate_codex_critic_review.py`, `scripts/generate_codex_mock_exam.py`, `scripts/generate_codex_podcast.py`, `scripts/generate_codex_personal_study_flashcards.py`, `scripts/generate_personal_study_guides.py` (main and helpers), `scripts/caption_lecture_figures.py`, `scripts/01_provision_engines.sh` (head), `Makefile`, `pyproject.toml`, `.gitignore`, `.github/workflows/ci.yml`, `docs/decisions/0045-...md`, `docs/decisions/0046-...md` (Record table).
- Probes run this session in the scratchpad (not in the repository tree): Kokoro `pm_alex` synthesis and format; whisper-cli with and without the pipeline prompt; `correct_specialist_terminology` on two segments; Typst 0.15.1 slide compile; ffmpeg compose and concat; repository `extract_frames` + `extract_text_supervised`; `env -i` Kokoro call; local HTTPS site with throwaway certificate, demo credentials and an `httpx` jar sign-in; tool version commands.

### Secondary (MEDIUM confidence)
- WebSearch on `Secure`/`__Host-` cookies over `http://localhost` (conflicting reports): https://web.dev/en/when-to-use-local-https , https://github.com/httpwg/http-extensions/issues/2605 , https://www2.webkit.org/show_bug.cgi?id=232088

### Tertiary (LOW confidence)
- RFC 6797 IP-literal exemption (training knowledge, `[ASSUMED]`, A2); Typst `yaml()` loop syntax (A1).

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - every tool probed on the machine this session; no new dependency.
- Architecture: HIGH - orchestrator, gate, run records, site and registration code read directly.
- Pitfalls: HIGH for the code-derived ones (1, 2, 4, 5, 7, 11, 12) and probe-derived ones (3, 6, 9); MEDIUM for browser cookie behavior (6) and the expectation that stages 8/9/guide run cleanly on first contact (10).
- Measured numbers: not yet existing; the plan must treat the report as an output of operator-run stages, not as something research can predict.

**Research date:** 2026-10-15
**Valid until:** 2026-11-14 (stable repository; re-check if Phase 6 code or the pinned engines change)
