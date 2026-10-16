# 0047. Synthetic demo lecture and measurement method

## Status

Accepted. 2026-10-15. Builds on ADR 0024 (stages 8 and 9 run on lecture data for
the first time), ADR 0032 (Kokoro narration engine), ADR 0045 (blocked-lecture
recovery) and ADR 0046 (adapters, egress rule, run records).

The status covers the demo lecture, the measurement method and the local-adapter
measurement and the Codex-adapter measurement (decision 4).

## Context

The repository needed a lecture that carries no third-party content and no
patient data, so that the pipeline can be shown end to end and measured in
public (DEMO-01 to DEMO-04). Stages 8 (assembly) and 9 (metadata) had never run
on lecture data, and the ADR 0045 recovery of a blocked lecture had never been
demonstrated on a real lecture.

A measurement is only as credible as its method. The results report
(`docs/results/demo-results.md`) therefore states every rate together with its
denominator, and this record states how each number is obtained and what it
cannot support.

## Decision

### 1. One versioned source, rebuilt by `make demo-video` (D-01, D-02, D-03, D-05)

`demo/lecture/script.yaml` is the single source: six slides, each with a
heading, bullets, colors and the narration lines. Typst renders the slides,
Kokoro narrates each slide through the shared `synthesize_kokoro_wav` helper
(the same code path as the podcast, ADR 0032) with the voice `pm_alex` in
Brazilian Portuguese, and ffmpeg composes the video. `make demo-video` rebuilds
it; the video itself is a gitignored build product under `workspace/demo/`. The
build is accepted only when the pipeline's own frame extraction and OCR recover
every slide heading and the duration falls inside the 200 to 290 second band.

The script is authored for this repository. It contains no third-party text and
no patient data, and the narration passes a lint of nine named rules before any
audio is produced. The lecture runs about four minutes, so that a full run
stays practical on a workstation.

### 2. Reference text and word error rate (D-06)

The reference is the narration lines joined in script order. Words are the NFC
normalized, casefolded word tokens of both texts. The word error rate is
(S + D + I) / N, where S, D and I are the substitutions, deletions and
insertions of a minimum word edit distance and N is the number of reference
words. The distance is a standard-library Levenshtein implementation; an
external package was not needed for a few hundred words.

### 3. Before and after the terminology dictionary (D-06)

The stored transcript is the output of the dictionary, so the "before" text is
reconstructed by undoing the recorded corrections of the corrections file. Both
texts are measured against the same reference. The corrections file is the only
source: nothing is re-transcribed to obtain "before".

Because the pipeline transcribes with the vocabulary prompt on, "before" is the
prompted recognition result. The report therefore also carries an ablation: the
lecture audio is transcribed once more without the vocabulary prompt, so that
the effect of the dictionary rule is visible apart from the effect of the
prompt. The ablation is a harvest-time step; when it cannot run, the harvest
records why and the report shows the pipeline condition only.

About fifteen domain terms are planted in the script on purpose, with one
negative control (a term that the dictionary must leave alone). The report lists
each planted term with its count in the reference, before and after the
dictionary.

### 4. Two lecture ids and the egress design (D-04)

Two registrations of the same video exist. `925100` is registered without
`remote_llm_allowed` and is used for the local adapter; the fail-closed guard of
ADR 0046 therefore refuses any remote run on it, and the codex preflight is
refused for it as well. `925101` is registered with `remote_llm_allowed` and is
reserved for the Codex adapter. Two ids keep each adapter's records,
run records and outputs apart without deleting artifacts between runs.

Sending the synthetic lecture to the remote provider needs an explicit operator
decision: the driver refuses the codex adapter without a confirmation flag, and
the confirmation was requested at a blocking checkpoint. The operator confirmed
the egress of lecture `925101` on 2026-10-15, stating that the lecture is
synthetic, carries no patient data and no third-party content, and goes to an
external provider knowingly. The provider's terms of use remain a procedural
check of the operator.

The first Codex attempt did not produce a measurement: both attempts of stage 5
failed in under seven seconds, before any model output, because the default
model named in the operator's own codex configuration (outside the repository)
is not supported by the ChatGPT account in use. The adapter passes no model
argument and was left unchanged. On 2026-10-16 the run was repeated with the
supported model `gpt-6-sol`, set through a temporary edit of that user-level
configuration that was restored afterwards. It covers the same synthetic
lecture 925101 under the egress confirmation of 2026-10-15. The report carries
the Codex section, with a note naming the model used.

### 5. Harvest before recovery

The measurements live in per-stage run records, units and logs under
`workspace/data/` and `workspace/demo/logs/`. The ADR 0045 recovery reruns the
critic, and a successful critic rerun replaces the measured critic record. The
run is therefore harvested first, with `make demo-harvest`, into
`workspace/demo/runs/<adapter>/` with a digest per file; the recovery driver
refuses to start while the harvested critic record is absent. The harvest
refuses an existing destination, so a measured run is never overwritten.

### 6. Metric formulas

- First-attempt schema pass rate of a stage: requests minus retries, over
  requests, where requests are the calls minus the retry calls of that stage's
  own gateway entry in its run record. It was verified per script that each
  request is one `complete_json` call with at most one retry and no retry of
  gateway errors (assumption A3 of the planning research). For the exams, podcast
  and flashcards stages the first attempt also includes the student-facing text
  guard.
- Critic rejection rate: (redrafts + units whose final review still rejected)
  over reviews. Reviews are the critic stage's review calls.
- A record with status `failed` is reported as failed, never as a percentage. A
  superseded record (an earlier attempt kept by the gateway) is listed as an
  extra marked row. Reruns after a failed attempt are disclosed in the notes of
  the report, together with any operator decision that changed a parameter.

### 7. Wall-time sources

Orchestrated stages are timed from the timestamps of the orchestrator log, which
have millisecond precision; the latest attempt of a phase wins and a skipped
phase is shown as skipped. Stage 18 (personal-study flashcards) is not
orchestrated by `run-all`, so its wall time comes from its run record. Stage 10
(quality certification) refuses by design: certification stays fail-closed
because authoritative evidence is unavailable (ADR 0024), so the report shows the
refusal and no time. A run log is accepted only when phases 2 to 9 ran or were
skipped and phase 10 started without completing.

### 8. Signed-in view over local HTTPS

The site is served from `127.0.0.1` over HTTPS with a throwaway self-signed
certificate valid for two days. The session cookie is `Secure` with the
`__Host-` prefix, which browsers accept only over HTTPS, so plain HTTP could not
demonstrate the real sign-in. The demo credentials and the session key are
generated at run time and kept in the gitignored
`workspace/demo/site-access/.env`; nothing of them is versioned or printed in the
report.

### 9. Recovery demonstration (DEMO-04)

`make demo-recovery` demonstrates the ADR 0045 recovery on lecture `925100` with
the local adapter, so nothing is sent to a remote service. Two blocked states
are induced one at a time: a stale draft (a draft file newer than its reviewed
unit, which raises `UnresolvedReviewError`) and a rejected review (a unit whose
review is rewritten as not passed). For each state the four derivative scripts
(mock exam, podcast, personal-study flashcards, study guide) must exit 1 and a
filesystem snapshot of every output root must be identical before and after;
the snapshot is the proof that nothing was written. The recovery steps of ADR
0045 then run: units without a draft are deleted, the critic script reruns and
re-approves the lecture with a real model verdict, and the four scripts must
exit 0 and refresh their outputs. The gate and the scripts are not modified.
The driver can pass the flashcards deck floor to the flashcards script
(`--flashcards-minimum-total-cards`, `FLASHCARDS_MINIMUM_TOTAL_CARDS` in the make
target), because the local model did not reach the default floor of 12 for this
one-unit lecture.

Outcome as measured. Five full runs were made on the local adapter: two at the
default floor of 12, then three with a floor of 5 by operator decision (the same
deviation as the harvested run), which was the operator limit of three. In every
run and both scenarios the four derivative scripts exited 1 with every output
root unchanged, and the critic rerun exited 0. The blocking and the critic
re-approval are therefore demonstrated, and podcast and study guide recovered in
10 of 10 scenario runs. The flashcards deck recovered in 0 of 4 scenario runs at
floor 12 and in 6 of 6 at floor 5. The mock exam recovered in 1 of 10 scenario
runs (the rejected scenario of the fourth run, where all four scripts recovered
and refreshed their outputs); the other nine failed on the local 14B model's own
contract (schema validation, missing JSON array, student-facing text guard), not
on the gate or the critic. The demonstration as specified, all four scripts
recovered in each scenario, was therefore not reached; the report lists every
attempt and shows the scenarios of the last run, which did not pass. The exam
failure is the same class as the first-attempt exam failure of the measured run,
and is attributed to the reliability of the local model, not to the recovery
procedure of ADR 0045.

### 10. Limits

- One evidence pack per adapter (D-05): each critic rate rests on a very small
  number of reviews, and only the slide at the midpoint of the lecture reaches
  the prompts. The report prints every rate with its denominator for that reason.
- The study guide runs without the inference flag because no figure-caption model
  is provisioned, and no figure catalog is forged. The bundle therefore uses
  deterministic templates and the macOS `say` voice, while the quiz serves the
  model exam. The report does not describe the guide as model generated.
- Wall time of a remote adapter includes provider latency and is not comparable
  with local inference time without that caveat.
- The local flashcards stage failed twice at the default deck floor and succeeded
  at a lower floor chosen by the operator; the report discloses the attempts and
  the deviation, and the resulting deck is not representative of a default run.
- All numbers come from one run on one machine. They describe that run; they are
  not a benchmark.

## Alternatives considered

- `jiwer` for the word error rate: rejected; one small function replaces a
  dependency and keeps the definition auditable here.
- One lecture id with artifact cleanup between adapters: rejected; deleting
  records between runs risks destroying the measurement, and two ids keep the
  egress flag per adapter.
- Plain HTTP on localhost: rejected; the `__Host-` cookie is not accepted over
  HTTP, so the real sign-in would not be shown.
- A lecture longer than 600 seconds: rejected; the cost grows with the length
  while the method gains nothing.
- Forging a figure catalog so that the guide uses the inference route: rejected;
  it would fabricate evidence.
- A timing suffix in the orchestrator log: rejected; the existing millisecond
  timestamps are sufficient and the orchestrator stays unchanged.
- Pillow or ffmpeg `drawtext` slides: rejected; Typst gives real typography and
  stays rebuildable from the versioned source.
- macOS `say` narration: rejected; Kokoro is the accepted engine (ADR 0032) and
  the same code path is exercised by the podcast.

## Consequences

- `docs/results/demo-results.md` is the measured outcome and is linked here
  without restating its numbers; `docs/results/demo-results.json` holds the same
  data in machine-readable form. Both are produced by `make demo-report` from the
  gitignored harvest and are identical when regenerated from the same harvest.
- Stages 8 and 9, the study-guide script and the site generator ran on real
  lecture data for the first time. No code defect surfaced in them; the failures
  observed were model contract failures of the local adapter (exams on the first
  attempt, flashcards at the default floor), recorded in the report.
- The DEMO-04 evidence is partial for the mock exam: every other step of the
  ADR 0045 recovery was demonstrated, and the exam recovered once in ten
  scenario runs because of local model reliability (decision 9). A stronger
  local model, a larger evidence pack or a higher retry budget would change that;
  none of them was altered here, and neither the gate nor the scripts were
  touched to obtain a pass.
- Phase 8 cites the report; it must cite the Codex figures as one evidence pack of one run, with the model named in the report.
- The demo needs no new dependency and no change to the critic gate, the
  adapters or the pipeline scripts.
