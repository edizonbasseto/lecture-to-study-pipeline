# 0010. Local PII At-Rest Encryption: Risk Acceptance via macOS FileVault

## Status

Accepted, scoped to local storage. The "no cloud egress" premise below no
longer holds for the pipeline as a whole: ADRs 0030, 0035, and 0038 route
LLM-backed phases (semantic extraction, drafting, critic review, mock exams)
through the Codex CLI, which sends lecture content to a remotely hosted model,
and ADR 0029 publishes study material to a study site served over the network. This record therefore covers only the at-rest
protection of `workspace/data/` on the operator's machine; the confidentiality
of content sent to those services or deployed to the site is governed by those
ADRs, not by this risk acceptance. ADR 0045 section 2 records that decision.

## Context

A `security-reviewer` pass flagged that `workspace/data/` -- every phase's
handoff artifacts (Phase 3 transcripts, Phase 5 semantic facts, Phase 6-7
drafts, Phase 8 canonical documents, Phase 9 global index, and now Phase
10-11's published study guides) -- persists lecture content, which may
include patient-identifying or otherwise sensitive clinical narrative
(PII), unencrypted at rest. No phase from 1 through 11 applies
application-level encryption before writing to `workspace/data/*.json`
(`docs/decisions/0009-...md`'s Rationale section already notes this
plainly: "Phases 3-8 already persist unencrypted to
`workspace/data/*.json` on every run").

`DIRETRIZES_ARQUITETURA.txt` Section 2 mandates the pipeline run strictly
locally and natively on Apple Silicon (M5 Pro): no Docker, no cloud
egress for any AI processing step. The entire pipeline, by design, never
transmits `workspace/data/` content off the operator's machine.

## Decision

1. Accept the missing-at-rest-encryption risk formally, rather than
   adding application-level encryption to `workspace/data/`.
2. Delegate at-rest protection entirely to macOS FileVault (full-disk
   encryption), external to this codebase and outside `lecture_study`'s
   responsibility.
3. No `lecture_study` code encrypts, decrypts, or otherwise transforms
   `workspace/data/*.json`/`*.md`/`*.html` for confidentiality purposes.
   Every phase's existing plain `Path.write_text`/`.read_text` plus
   write-to-`.tmp`-then-`.replace()` convention is unchanged by this ADR.

## Rationale

- **Threat model matches FileVault's coverage.** The pipeline never
  leaves the operator's machine (`DIRETRIZES_ARQUITETURA.txt` Section 2):
  there is no network transmission of `workspace/data/` content, no
  multi-tenant host, and no shared-server exposure to defend against.
  FileVault's full-volume XTS-AES-128 encryption already covers every
  byte written to disk, including `workspace/data/`, at zero
  implementation cost.
- **Application-level encryption adds key-management surface with no
  corresponding threat-model benefit here.** Encrypting each artifact
  (e.g. one Fernet key per file, or one pipeline-wide key) requires
  deciding where that key is generated, stored, and unlocked from -- on a
  single local machine already covered end-to-end by FileVault, that
  machinery defends against nothing FileVault does not already cover,
  while adding real engineering and operational cost (key rotation, key
  loss recovery, a new failure mode if the key file itself is lost).

## Consequences

- `workspace/data/` remains git-ignored plain JSON/Markdown/HTML on disk;
  its confidentiality is contingent on the operator keeping FileVault
  enabled and the machine's login credentials private -- an operational
  control external to this codebase, not a code-level guarantee.
- If a future deployment target changes this assumption (a shared
  workstation, cloud sync of `workspace/data/` via iCloud
  Drive/Dropbox/etc., or a second operator account on the same machine),
  this ADR's risk acceptance must be revisited: FileVault protects data
  at rest against a stolen or powered-off disk, not against another
  logged-in user on the same unlocked machine, nor against a directory
  that leaves the disk FileVault protects.
- This ADR is documentation-only: no source file changes accompany it. It
  formally closes the `security-reviewer` finding by recording the
  accepted risk and its boundary, rather than by changing code.

## Alternatives Considered

- **Per-file application-level encryption** (e.g. `cryptography`'s
  Fernet on each `.json`/`.md`/`.html` write). Rejected: introduces key
  generation, storage, and rotation for a single-operator, single-machine,
  no-network-egress pipeline already covered by FileVault -- the added
  engineering cost has no corresponding marginal threat-model benefit
  given `DIRETRIZES_ARQUITETURA.txt`'s local-only mandate.
- **A separate encrypted disk image (`.dmg`) mounted at `workspace/`.**
  Rejected: functionally redundant with FileVault (both are
  full-volume/container-level, not selectively scoped to PII fields
  within each artifact), and adds an operational mount/unmount step the
  pipeline's async workers do not currently coordinate around.
