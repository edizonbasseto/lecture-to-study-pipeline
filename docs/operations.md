# Operating boundaries

This document states the operating boundaries of the lecture study pipeline and of its study site, as the architecture decision records (ADRs) in [decisions](decisions) define them. It adds no rule of its own: each statement is either a verbatim passage of an ADR, shown as a blockquote, or a summary written against the ADR text and followed by a link to its source.

Terminology: the ADRs call the pipeline stages "Phase N"; this document says "stage N" and keeps "Phase N" only inside quoted passages.

Reading guide: each boundary belongs to one of four kinds.

- Enforced by code: a check in the repository refuses the operation.
- Procedural: the operator carries the duty and no code verifies it.
- Accepted risk: an ADR records a risk that was accepted and states when it must be revisited.
- Deployment constraint: a property of how the site is deployed that the operator must preserve.

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
