# Phase 7: Synthetic demo lecture and measured results - Context

**Gathered:** 2026-10-15
**Status:** Ready for planning

<domain>
## Phase Boundary

A lecture authored for this repository (own script, own slides, Kokoro narration, ffmpeg composition) is rebuilt by a make target, runs through `run-all` and `run-personal-study` (stages 8 and 9 included) to a signed-in page view on the locally served site, and the pipeline's quality is reported with measured numbers (DEMO-01..DEMO-04). The blocked-lecture recovery of ADR 0045 is demonstrated on the same lecture.

</domain>

<decisions>
## Implementation Decisions

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

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements and roadmap
- `.planning/ROADMAP.md` Phase 7 - goal and four success criteria
- `.planning/REQUIREMENTS.md` DEMO-01..DEMO-04 - requirement text

### Decisions
- `docs/decisions/0032-kokoro-mlx-podcast-tts-engine.md` - TTS engine, voices, subprocess discipline
- `docs/decisions/0015-local-tts-audio-generation.md` - TTS error taxonomy and subprocess rules still in force
- `docs/decisions/0024-uncertified-personal-study-execution-mode.md` - personal-study mode, stages 8 and 9 first run
- `docs/decisions/0045-critic-gate-for-derivative-material-egress-and-audience.md` - blocked-lecture recovery (DEMO-04)
- `docs/decisions/0046-pluggable-llm-gateway-and-local-adapter.md` - adapter selection, remote policy, run records

### Code
- `src/lecture_study/tts/generator.py` - Kokoro invocation
- `src/lecture_study/transcription/terminology.py` - terminology dictionary
- `src/lecture_study/llm/run_record.py` - per-stage run records usable for wall time and call counts
- `Makefile` - existing targets (`run`, `site`) to extend

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `mlx_audio.tts.generate` subprocess helper in `tts/generator.py` (bounded process, sanitized environment)
- Gateway run records (adapter, calls, status) for DEMO-03 stage metrics
- Provisioned llama.cpp engines and Qwen2.5-14B shards; ffmpeg at `/opt/homebrew/bin/ffmpeg`; `codex` at `/opt/homebrew/bin/codex`

### Established Patterns
- Subprocesses use bounded-process helpers with sanitized environment; no `shell=True`
- OCR is Apple Vision through pyobjc (macOS only); `tesseract` is not installed and not needed

### Integration Points
- `make` targets next to `run` and `site`
- Local lecture registration with `--remote-llm-allowed` flag for the demo lecture

</code_context>

<specifics>
## Specific Ideas

- Operator emphasized that Kokoro output quality was very good in the source project; keep it as the default voice engine.

</specifics>

<deferred>
## Deferred Ideas

None.

</deferred>
