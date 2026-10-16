# Phase 8: Portfolio README and documentation - Context

**Gathered:** 2026-10-16
**Status:** Ready for planning

<domain>
## Phase Boundary

The README replaces the "under active extraction" placeholder and, with the documents it links, lets a technical reader or recruiter understand the problem, the architecture, the design decisions and the measured results. The demo walkthrough is executed once and every command in it exists as written (DOC-01..DOC-03). Documentation only; no pipeline behaviour changes.

</domain>

<decisions>
## Implementation Decisions

Chosen by the agent on operator instruction (2026-10-16: the agent does all work itself) and under the operator's earlier guidance that this is a study project and the process stays proportionate.

- **D-01:** The README is in English, single file `README.md`, with: problem, Mermaid architecture diagram, stage map, design decisions linking to ADRs, a results summary taken from `docs/results/demo-results.md` (linked, not copied), and a quick start.
- **D-02:** Operating boundaries (DOC-03) live in one new document `docs/operations.md`, linked from the README: egress rule and unchecked provider terms (ADR 0045 section 2), uncertified status and notice (ADR 0024, ADR 0045 section 3), no standing toward certification, blocked-lecture recovery (ADR 0045), page regeneration after inline script or style change (ADR 0044), single-worker site constraint (ADR 0039), FileVault revisit triggers (ADR 0010). Statements are quoted or summarised from the ADRs, never invented.
- **D-03:** The end-to-end demo walkthrough lives in the README ("End-to-end demo") using the Phase 7 make targets; every command is checked to exist (Makefile targets, scripts) and the sequence is executed once for the walkthrough evidence (dry-run or `demo-check` level where a full multi-hour run is unnecessary; results already produced in Phase 7 are cited).
- **D-04:** The Codex figures are described as one evidence pack of one run with the model named in the results report; the partial DEMO-04 outcome is stated honestly (mock exam recovered in 1 of 10 runs on the local model).
- **D-05:** No secrets, hostnames, personal data or machine-specific paths in the documents (R0.1); the throwaway TLS site credentials are never shown.

### Claude's Discretion
Section ordering, diagram layout, and wording.

</decisions>

<canonical_refs>
- `.planning/ROADMAP.md` Phase 8, `.planning/REQUIREMENTS.md` DOC-01..03
- `docs/decisions/` ADRs 0010, 0024, 0039, 0044, 0045, 0046, 0047
- `docs/results/demo-results.md`, `Makefile`
</canonical_refs>
