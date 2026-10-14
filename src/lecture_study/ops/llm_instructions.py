"""Backend-agnostic LLM instruction text shared by the local llama.cpp
workers and their Codex-CLI replacements (ADR 0035/0036).

Relocated out of `semantic/llm_worker.py`, `critic/llm_worker.py`, and
`drafting/llm_worker.py` so the Codex-route scripts under `scripts/` stop
reaching into those modules' private (`_`-prefixed) constants across a
package boundary with no shared contract -- the local llama.cpp worker
modules are being retired (qwen3-32b/llama.cpp removal), and every
constant a Codex script needs to survive that removal lives here instead.

Wording is verbatim, unchanged from the local workers' own instructions;
this is a pure relocation, not a recalibration.
"""

from __future__ import annotations

TARGET_SEMANTIC_FACTS = 48
MAXIMUM_SEMANTIC_FACTS = 64

EXTRACTION_INSTRUCTION = (
    "Extract medical facts, dosages, and red flags EXCLUSIVELY from the "
    "evidence below. Do not add outside information. Preserve the patient "
    "species and category exactly as stated. "
    "Write every textual output field in Brazilian Portuguese (pt-BR), "
    "including entity_name, category, and clinical_context. Translate any "
    "English source wording faithfully into Brazilian Portuguese without "
    "adding, removing, or changing its clinical meaning. "
    "Never substitute a human for "
    "an animal or one species or patient category for another. Do not "
    "silently change anatomy, procedures, dosages, or negations. If a fact "
    "is uncertain, omit it instead of reinterpreting it. Preserve both "
    "compared entities and the comparison direction in every comparative "
    "fact; include the subject and comparator together in clinical_context. "
    "Merge repeated statements and paraphrases into one fact when they "
    "express the same clinical claim. Never merge clinically distinct facts, "
    "including different dosages, red flags, contraindications, procedures, "
    "comparisons, anatomical sites, species, or patient categories. Keep each "
    "field concise and do not emit duplicate facts. Target no more than "
    f"{TARGET_SEMANTIC_FACTS} facts. Never return more than "
    f"{MAXIMUM_SEMANTIC_FACTS} facts; when the source contains more statements, "
    "consolidate only repetitions "
    "and preserve every clinically distinct dosage, red flag, contraindication, "
    "procedure, comparison, anatomical site, species, and patient category. "
    "For every transcript-backed fact, set timestamp_ref to the supporting "
    "block's source_sec: copy that source_sec value exactly, including its "
    "decimal point. For an OCR-only fact, use the exact anchor-frame "
    "timestamp. Never copy a clock or date from OCR into timestamp_ref. Echo "
    "lecture_id and pack_sequence exactly as given. Output valid JSON matching "
    "the provided schema."
)

CRITIC_INSTRUCTION = (
    "You are an implacable medical reviewer. Treat EVIDENCE as primary. "
    "Compare the DRAFT and the CLINICAL LEDGER against it. Every claim in "
    "the CLINICAL LEDGER and DRAFT must be supported by EVIDENCE. "
    "A faithful paraphrase, synonym, or prose summary that restates the "
    "same finding in different words is NOT a violation -- judge "
    "substance, not wording. A summary of several EVIDENCE items under "
    "one descriptive label is fine ONLY if every one of those items is "
    "still individually present somewhere in the DRAFT or CLINICAL "
    "LEDGER; silently dropping any item from an EVIDENCE list, or "
    "narrowing an exhaustive list to fewer items, IS a violation -- fail "
    "it as an omission, the same as a fabricated addition. Never treat "
    "the patient's species or the Ledger's own `category` field as a "
    "'descriptive label' eligible for this leniency -- those must match "
    "EVIDENCE exactly; a changed patient species or Ledger `category` is "
    "always a conflict, never a paraphrase. Only fail a claim that adds "
    "a fact, quantity, causal link, or red flag the EVIDENCE does not "
    "support; that changes species, Ledger `category`, a compared "
    "entity, a comparison direction, or a negation; or that omits an "
    "item from an EVIDENCE list as described above. If the Ledger "
    "conflicts with EVIDENCE, or the Draft repeats such a conflict, you "
    "must fail it: set passed to false, and in feedback name exactly "
    "which claim is unsupported or omitted, or which red flag from a "
    "supported Ledger fact was ignored. For every failure, quote the "
    "exact conflicting or omitted Draft/Ledger text and the EVIDENCE "
    "passage it contradicts or omits entirely -- a failure citing "
    "EVIDENCE text that merely uses different words for the same claim is "
    "itself wrong; never introduce a term that is absent from the Draft. "
    "Also fail any title, body, or clinical warning not written in "
    "Brazilian Portuguese (pt-BR). Verify both compared entities and the "
    "comparison direction; fail any replaced comparator, inverted "
    "relation, or changed negation. Always write feedback in Brazilian "
    "Portuguese (pt-BR). If every Ledger and Draft claim is grounded, "
    "every EVIDENCE list item is preserved, and the language requirement "
    "is satisfied, set passed to true. Output valid JSON matching the "
    "provided schema."
)

# The five graduate-level clinical categories the drafting instruction asks
# the model to cover wherever the Evidence supports them (D-16). Kept as a
# tuple, not inlined into the instruction string, so depth_report.py can
# score coverage against the same labels the prompt actually asked for.
DRAFT_COVERAGE_CATEGORIES = (
    "indicação",
    "contraindicação",
    "técnica",
    "complicações",
    "tomada de decisão cirúrgica",
)
