# Operating boundaries

This document states the operating boundaries of the lecture study pipeline and of its study site, as the architecture decision records (ADRs) in [decisions](decisions) define them. It adds no rule of its own: each statement is either a verbatim passage of an ADR, shown as a blockquote, or a summary written against the ADR text and followed by a link to its source.

Terminology: the ADRs call the pipeline stages "Phase N"; this document says "stage N" and keeps "Phase N" only inside quoted passages.

Reading guide: each boundary belongs to one of four kinds.

- Enforced by code: a check in the repository refuses the operation.
- Procedural: the operator carries the duty and no code verifies it.
- Accepted risk: an ADR records a risk that was accepted and states when it must be revisited.
- Deployment constraint: a property of how the site is deployed that the operator must preserve.

## Summary

| Boundary | Kind | Source |
| --- | --- | --- |
| Remote-model egress only for flagged lectures | Enforced by code (fails closed) | [ADR 0045](decisions/0045-critic-gate-for-derivative-material-egress-and-audience.md), [ADR 0046](decisions/0046-pluggable-llm-gateway-and-local-adapter.md) |
| Provider terms and detection of patient-identifying data | Procedural | [ADR 0045](decisions/0045-critic-gate-for-derivative-material-egress-and-audience.md), [ADR 0046](decisions/0046-pluggable-llm-gateway-and-local-adapter.md), [ADR 0047](decisions/0047-demo-lecture-measurement-method.md) |
| Uncertified notice on every page that renders study material | Enforced by code | [ADR 0024](decisions/0024-uncertified-personal-study-execution-mode.md), [ADR 0045](decisions/0045-critic-gate-for-derivative-material-egress-and-audience.md) |
| No standing toward certification (stage 10 refuses) | Enforced by code | [ADR 0024](decisions/0024-uncertified-personal-study-execution-mode.md) |
| Blocked-lecture recovery | Enforced by the gate; recovery is a manual procedure | [ADR 0045](decisions/0045-critic-gate-for-derivative-material-egress-and-audience.md), [ADR 0047](decisions/0047-demo-lecture-measurement-method.md) |
| Page regeneration after an inline script or style change | Procedural (operational requirement) | [ADR 0044](decisions/0044-public-extraction-and-site-access-hardening.md), [ADR 0045](decisions/0045-critic-gate-for-derivative-material-egress-and-audience.md) |
| Single-worker site | Deployment constraint | [ADR 0039](decisions/0039-phone-sessions-and-edge-cache-isolation.md) |
| At-rest protection of `workspace/data/` left to FileVault | Accepted risk | [ADR 0010](decisions/0010-local-pii-encryption-risk-acceptance.md) |

## Remote-model egress and the provider terms

Source: [ADR 0045, section 2](decisions/0045-critic-gate-for-derivative-material-egress-and-audience.md) and [ADR 0046, decision 6](decisions/0046-pluggable-llm-gateway-and-local-adapter.md).

Several stages send lecture content to a remotely hosted model through the Codex command-line client. ADR 0045 accepts that risk under one rule:

> The Codex route is used only for lecture content the operator has verified
> to carry no patient-identifying data.

Whether the content carries such data, and whether the provider's terms are acceptable, is not decided by any code:

> Applying this rule is the operator's responsibility; no technical control
> detects patient-identifying data before a call. The retention and training
> terms of the remote provider are not recorded here and must be checked against
> the account in use.

This document makes no statement about what any provider's terms say.

### What the code enforces

ADR 0046 turns the rule into a per-lecture flag. The remote adapter runs for a lecture only when the lecture is recorded as eligible in one of two places:

- A lecture of the local registry carries `remote_llm_allowed` in `config/local-lectures.local.yaml`. The field defaults to false, and `scripts/register_local_lecture.py` sets it with `--remote-llm-allowed`.
- A lecture of the portal catalog is listed in `remote_llm_allowed_lecture_ids` of the gateway configuration, whose versioned example is `config/llm-gateway.example.yaml`.

Any other lecture is refused:

> A lecture flagged in neither place is refused (fail closed, D-15).

The refusal happens before any call is made:

> The check happens in the factory before the Codex executable is resolved, so a
> refused run resolves nothing and spawns nothing.

The local adapter is not subject to the flag. The adapter is chosen by the gateway configuration or by the `LECTURE_STUDY_LLM_ADAPTER` environment variable, which replaces the configured default and every per-stage entry for that run (ADR 0046, decision 2), so a run that selects the local adapter is entirely local.

### What stays procedural

The flag is the operator's recorded statement that the lecture carries no patient-identifying data; no code inspects content. The provider terms stay unchecked by code as well:

> The retention and training terms of the remote provider stay a procedural check
> against the account in use (D-16).

### Practice in the demonstration

The synthetic demo lecture follows the same boundary. `make demo-register` registers it twice, once without the flag (the identifier used for the local adapter) and once with `--remote-llm-allowed` (the identifier reserved for the Codex adapter). The demo driver `scripts/demo_run.py` additionally refuses the Codex adapter without `--confirm-remote-egress`, and [ADR 0047, decision 4](decisions/0047-demo-lecture-measurement-method.md) records that the provider terms remained a check of the operator:

> The provider's terms of use remain a procedural check of the operator.

## Uncertified status and notice

Source: [ADR 0024, decision](decisions/0024-uncertified-personal-study-execution-mode.md) and [ADR 0045, section 3](decisions/0045-critic-gate-for-derivative-material-egress-and-audience.md).

ADR 0024 introduces a separate, explicit execution mode that produces personal study material without a publication certificate. Certification itself is not weakened: the certified path stays unreachable until a reference corpus and an evaluation authority exist, and the material produced in the personal-study mode is always uncertified.

ADR 0045 widens the audience of that material from the single requesting student to the audience of the study site:

> ADR 0024's audience bound is widened from the single requesting student to the
> allowlisted site audience defined by ADRs 0029 and 0039. The material stays
> uncertified, and every site page that renders study material says so.

The notice is shipped to readers in Portuguese, as one text defined once in `src/lecture_study/site/renderer.py`:

> Material de estudo gerado automaticamente a partir da aula, sem certificação
> nem revisão clínica humana. Confira sempre com a fonte original.

The notice appears at the top of the generated home, lecture, reading and watch pages and of both mock-exam pages (the latter through `src/lecture_study/site/quiz.py`). The login page carries none. Files that the site serves as they are cannot carry it:

> Files the site serves unchanged (PDF, Markdown sources, audio, video) cannot
> carry an HTML notice. They are reached only from the pages above.

## No standing toward certification

Source: [ADR 0024, enforced consequences](decisions/0024-uncertified-personal-study-execution-mode.md).

The personal-study mode does not touch the certification chain. Stage 10 refuses unconditionally:

> Phase 10 keeps raising `QualityCertificationError` unconditionally.

No artifact of the mode may claim a status it does not have:

> No artifact produced under this mode may be presented, labeled, or exported
> as certified, production-ready, or clinically approved.

The mode carries no authority and no claim on a later certification:

> It grants no clinical, academic, or publication authority of any kind.

> It must not be exposed as a batch, multi-student, or distribution-facing
> capability, and it confers no standing toward eventual production
> certification.

The measured demonstration shows the refusal by design: stage 10 issues no certificate in either run (see the per-stage wall times in [the results report](results/demo-results.md)). Any future certified route first needs a new record on how a generator and its evaluator are shown to be independent, which matters when both are remote models or when the local adapter serves one of them ([ADR 0045, section 4](decisions/0045-critic-gate-for-derivative-material-egress-and-audience.md)).

## Blocked-lecture recovery

Source: [ADR 0045, section 1 and Consequences](decisions/0045-critic-gate-for-derivative-material-egress-and-audience.md) and [ADR 0047, decision 9](decisions/0047-demo-lecture-measurement-method.md).

The scripts that turn drafting output into student-facing material (mock exam, podcast, personal-study flashcards and study guide) load only critic-approved units. The whole lecture is blocked, and no material is written for it, when any of these conditions holds:

- no unit is found for the lecture;
- the unit sequence has a gap or a duplicate;
- any unit has a review that did not pass;
- the drafts and the units are not the same set of sequence numbers;
- a draft was modified after its unit was written;
- the sequence number echoed inside a draft disagrees with its file name;
- the lecture or the sequence number inside a unit disagrees with the lecture and the file name it is stored under.

The ADR rejects partial publication:

> A blocked lecture is never published partially.

Material generated before the gate existed was not checked by it:

> Material generated before this decision was not gated. The mock exams, podcast
> scripts and audio, personal-study flashcard decks, and study guides produced
> earlier must be regenerated or deleted.

### Manual recovery procedure

Recovery is a manual procedure, and the orchestrator does not perform it:

> Recovery after a rejection or a re-draft: delete the lecture's units that no
> longer have a draft, then run `scripts/generate_codex_critic_review.py` for
> the lecture directly. The orchestrator decides whether to skip Phase 7 by file
> sequence numbers alone, so it does not rerun the critic after a re-draft, and
> the personal-study command never runs Phase 7.

### Outcome of the demonstration

`make demo-recovery` performs the demonstration on the local adapter, so nothing is sent to a remote service. It induces two blocked states one at a time, a stale draft and a rejected review, then follows the recovery procedure. The outcome, with the figures of [the results report](results/demo-results.md), was five full runs:

- Blocking was demonstrated in every run and in both scenarios: the four scripts exited 1 and every output root stayed unchanged.
- The critic rerun re-approved the lecture in every run, with a real model verdict.
- Podcast and study guide recovered in 10 of 10 scenario runs.
- The flashcards deck recovered in 6 of 6 scenario runs at the deck floor of 5 and in 0 of 4 at the default floor of 12. The floor of 5 was set by operator decision.
- The mock exam recovered in 1 of 10 scenario runs.

The demonstration as specified, all four scripts recovered in each scenario, was therefore only partly reached. The ADR attributes the exam failures to the local model:

> The exam failure is the same class as the first-attempt exam failure of the
> measured run, and is attributed to the reliability of the local model, not to
> the recovery procedure of ADR 0045.

## Page regeneration after an inline script or style change

Source: [ADR 0044, section 4](decisions/0044-public-extraction-and-site-access-hardening.md) and [ADR 0045, Consequences](decisions/0045-critic-gate-for-derivative-material-egress-and-audience.md).

The site admits inline scripts and styles only by their hash in the Content-Security-Policy. A change to inline code therefore invalidates the pages generated before it:

> Changing an inline script or style changes the policy with it, so pages
> generated before the change must be regenerated, or the browser blocks their
> now-unlisted inline code.

ADR 0045 records the same operational consequence for the notice on pages that are already deployed:

> pages already deployed keep the old footer notice until the site is regenerated.

Two paths regenerate the pages. `make demo-site` regenerates the demo site. The `generate` mode of `infra/docker-entrypoint.sh` regenerates the site of the container image from the mounted study-guides volume.

## Single-worker site constraint

Source: [ADR 0039, Context and decision 5](decisions/0039-phone-sessions-and-edge-cache-isolation.md).

The sign-in form is reachable without credentials and its cost is paid by one process:

> `POST /entrar` is reachable without credentials and costs one PBKDF2 pass per
> allowlisted person, run on the event loop of a single-worker container.

ADR 0039 answers it with two limits on sign-in attempts, a wait counted per address attempted and a cap on concurrent password checks. Both are held by the process itself:

> Both limits are in-process, matching the single-replica deployment; they are a
> first barrier, not a distributed quota.

The container entrypoint (`infra/docker-entrypoint.sh`) and `make demo-serve` each start one server process; neither the entrypoint, `Dockerfile.site` nor the Makefile passes a workers option. As a direct consequence of "in-process", a deployment with more than one process does not share these limits.

## FileVault revisit triggers

Source: [ADR 0010, Status, Decision and Consequences](decisions/0010-local-pii-encryption-risk-acceptance.md).

ADR 0010 covers only the at-rest protection of `workspace/data/` on the operator's machine. The confidentiality of content sent to services is governed by other ADRs (see the egress section above). The ADR accepts the absence of application-level encryption and delegates at-rest protection to macOS FileVault. No code of the pipeline encrypts, decrypts or otherwise transforms those files for confidentiality.

The acceptance has stated limits and stated triggers for a new review:

> If a future deployment target changes this assumption (a shared
> workstation, cloud sync of `workspace/data/` via iCloud
> Drive/Dropbox/etc., or a second operator account on the same machine),
> this ADR's risk acceptance must be revisited: FileVault protects data
> at rest against a stolen or powered-off disk, not against another
> logged-in user on the same unlocked machine, nor against a directory
> that leaves the disk FileVault protects.

`make docs-check` verifies the commands, paths, links and quotations in this document.
