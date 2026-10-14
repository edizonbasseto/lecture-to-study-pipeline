"""Build complete and summarized pt-BR personal study-guide editions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from itertools import batched
from pathlib import PurePosixPath
from typing import Final, Iterable, Mapping, Sequence

from lecture_study.domain.models import LearningUnitDraft, SemanticLedger
from lecture_study.study_materials.academic_typst import (
    academic_document_start,
    render_academic_body,
    render_academic_figure,
    render_academic_warning_block,
    typst_string,
)
from lecture_study.study_materials.content_identity import content_identity

_ADMINISTRATIVE_TITLE_MARKERS: Final = (
    "boas-vindas",
    "consultora",
    "contato e informações",
    "encerramento da aula",
    "instruções de",
    "localização da instituição",
    "rede social",
)
_SPEAKER_INTRODUCTION_TITLE: Final = re.compile(
    r"\bsobre (?:a|o) (?:docente|professora?|palestrante)\b", re.IGNORECASE
)
_SUPPLEMENTARY_VIDEO_TITLE_SUFFIX: Final = re.compile(
    r"\s*\bsuper video\s*$", re.IGNORECASE
)
_SUMMARY_KEYWORDS: Final = (
    "alerta",
    "avaliação",
    "contraindicado",
    "diagnóstico",
    "evitar",
    "indicado",
    "não ",
    "procedimento",
    "recomendado",
    "risco",
    "teste",
    "tratamento",
    "úlcera",
)
_TERM_REPLACEMENTS: Final = (
    (r"\bcorneal ulcer treatment\b", "tratamento da úlcera de córnea"),
    (r"\bo crosslinking é usado\b", "a reticulação corneana é usada"),
    (r"\bo crosslinking\b", "a reticulação corneana"),
    (r"\bcrosslinking\b", "reticulação corneana"),
    (r"\bmelting\b", "ceratomalácia"),
    (r"\blaser pointer\b", "ponteiro laser"),
    (r"\btest of menace\b", "teste de ameaça"),
    (r"\bmenace response\b", "resposta de ameaça"),
    (r"\bflap\b", "retalho"),
    (r"\bflop\b", "retalho"),
    (r"\s*\(strips\)", ""),
    (r"\bflare\b", "efeito Tyndall"),
    (r"\bhypopyon\b", "hipópio"),
    (r"\bpocket pets\b", "animais de companhia não convencionais"),
    (r"\bred_flag\b", "alerta clínico"),
    (r"\bcomplications\b", "complicações"),
    (r"\bcondition\b", "condição"),
    (r"\bstress\b", "estresse"),
    (r"\bneuro-ophthalmic alterations\b", "alterações neuro-oftálmicas"),
    (r"\bdisease of (?:the )?larynx\b", "doença da laringe"),
    (r"\bneurological disorders?\b", "distúrbios neurológicos"),
    (r"\banesthesia\b", "anestesia"),
    (r"\bvomiting\b", "vômito"),
    (r"\bpalatine cleft\b", "fenda palatina"),
    (r"\bvon Langenbeck technique\b", "técnica de von Langenbeck"),
    (r"\bneurooftalmicas\b", "neuro-oftálmicas"),
    (r"\btopical medication\b", "medicação tópica"),
    (r"\bmidriase\b", "midríase"),
    (r"\bfluorescina\b", "fluoresceína"),
    (r"\bOxnest\b", "Oxinest"),
    (r"\bhidroxiridina\b", "idoxuridina"),
    (r"\bfanciclovir\b", "famciclovir"),
    (r"\bcarcinoma urotenal\b", "carcinoma urotelial"),
    (r"\bcetologia\b", "citologia"),
    (r"\bdivulgação romba\b", "divulsão romba"),
    (
        r"\bvaso epigástrica superficial caudal\b",
        "vaso epigástrico superficial caudal",
    ),
    (r"\bsarcoma de inflamação felino\b", "sarcoma de aplicação felino"),
    (r"\búnçera\b", "úlcera"),
    (r"\bantibiôtico\b", "antibiótico"),
    (r"\breflexo popular fotomotor\b", "reflexo pupilar fotomotor"),
    (r"\bgase\b", "gaze"),
    (r"\bcotonete estéreo\b", "cotonete estéril"),
    (
        r"\bnem deve estar relacionada à funcionalidade\b",
        "nem deve estar relacionado à funcionalidade",
    ),
    (r"\bsuper video\b", "vídeo complementar"),
    (r"\bdisease stable\b", "doença estável"),
    (r"\bclinical_context\b", "contexto clínico"),
    (r"\banatomical_site\b", "sítio anatômico"),
    (r"\bstatistic\b", "estatística"),
    (r"\bdisease\b", "doença"),
    (r"\binstitution\b", "instituição"),
    (r"\bperson\b", "pessoa"),
    (r"\btreatment\b", "tratamento"),
    (r"\bprocedure\b", "procedimento"),
    (r"\banatomy\b", "anatomia"),
    (r"\bcontraindication\b", "contraindicação"),
    (r"\bcomplication\b", "complicação"),
    (r"\bmedication\b", "medicação"),
    (r"\bsymptom\b", "sintoma"),
    (r"\bpatient_category\b", "categoria de paciente"),
    (r"\bpatient_characteristic\b", "característica do paciente"),
    (r"\bpatient_species\b", "espécie do paciente"),
    (r"\brisk_factor\b", "fator de risco"),
    (r"\bpathology\b", "patologia"),
    (r"\boutcome\b", "desfecho"),
    (r"\bmitigation\b", "mitigação"),
    (r"\bmeasurement\b", "medida"),
    (r"\bdiagnosis\b", "diagnóstico"),
    (r"\bfinding\b", "achado"),
    (r"\bspecies\b", "espécie"),
    (r"\borganism\b", "organismo"),
    (r"\bequipment\b", "equipamento"),
    (r"\bprocess\b", "processo"),
    (r"\btool\b", "instrumento"),
    (r"\bnerve\b", "nervo"),
    (r"\bduct\b", "conduto"),
    (r"\bhematomas orais\b", "hematomas aurais"),
    (r"\botitis\b", "otite"),
    (r"\botolite\b", "otite"),
    (r"\bamenaza\b", "ameaça"),
    (r"\bunocular\b", "monocular"),
)
# One chapter of an operator-supplied plan: (chapter title, first pack
# sequence, last pack sequence), both bounds inclusive.
ChapterPlan = Sequence[tuple[str, int, int]]
_MAXIMUM_SUMMARY_POINTS_PER_CHAPTER: Final = 12
_MAXIMUM_SUMMARY_WARNINGS_PER_CHAPTER: Final = 8
_WARNING_MARKERS: Final = (
    "alert",
    "complica",
    "contraindic",
    "cuidado",
    "evitar",
    "não ",
    "risco",
    "toxic",
)
_STRUCTURED_STUDY_FACT = re.compile(
    r"^(.{3,90}?)\s+\([^)]+\)\.\s+",
    flags=re.IGNORECASE,
)
_CATEGORIZED_STUDY_TERM = re.compile(
    r"^(.{3,90}?)\s+\([^)]+\)\.?$",
    flags=re.IGNORECASE,
)
_UNINFORMATIVE_ASSERTION_TOKENS: Final = frozenset(
    {
        "a",
        "as",
        "e",
        "esta",
        "está",
        "estao",
        "estão",
        "foi",
        "foram",
        "o",
        "os",
        "paciente",
        "realizada",
        "realizadas",
        "realizado",
        "realizados",
    }
)
_MINIMUM_LEXICAL_STEM_LENGTH: Final = 6


class StudyEditionKind(StrEnum):
    """The two mandatory personal-study editions."""

    COMPLETE = "complete"
    SUMMARY = "summary"

    @property
    def pt_br_label(self) -> str:
        if self is StudyEditionKind.COMPLETE:
            return "Apostila completa"
        return "Apostila resumida"


@dataclass(frozen=True, slots=True)
class SelectedFigure:
    """One captioned lecture frame chosen to illustrate a study section --
    embedded inline in the complete edition (2026-09-15: the operator
    rejected a separate figures-only atlas as not making sense; a figure
    belongs next to the text that explains it).

    relative_path construction-time rejection of an absolute or
    parent-referencing path is one of two independent path checks (the
    second is the staging step's own safe-path assertion in
    scripts/generate_personal_study_guides.py) -- restored here after a
    2026-09-15 security review finding: the equivalent check on the
    removed figure-atlas document's StudyDocumentFigure was deleted along
    with that document, leaving this, the one remaining figure-path type,
    without it."""

    relative_path: str
    caption: str

    def __post_init__(self) -> None:
        path = PurePosixPath(self.relative_path)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(
                "figure image path must be relative with no parent "
                f"reference: {self.relative_path!r}"
            )


@dataclass(frozen=True, slots=True)
class StudySection:
    """One rendered section in a study-guide chapter."""

    title: str
    body: str
    warnings: tuple[str, ...]
    source_sequences: tuple[int, ...]
    figures: tuple[SelectedFigure, ...] = ()


@dataclass(frozen=True, slots=True)
class StudyChapter:
    """One thematic chapter with one or more study sections."""

    title: str
    sections: tuple[StudySection, ...]


@dataclass(frozen=True, slots=True)
class StudyEdition:
    """One complete or summarized study-guide representation."""

    kind: StudyEditionKind
    lecture_id: str
    lecture_title: str
    chapters: tuple[StudyChapter, ...]
    source_unit_count: int


@dataclass(frozen=True, slots=True)
class _RankedPoint:
    text: str
    score: int
    source_sequence: int


def study_fact_subject(content: str) -> str | None:
    """Return the subject of one categorized study fact."""

    cleaned = _clean_study_fact_text(content).rstrip(".;:")
    for pattern in (_STRUCTURED_STUDY_FACT, _CATEGORIZED_STUDY_TERM):
        match = pattern.match(cleaned)
        if match:
            return match.group(1).rstrip(" ,;:")
    return None


def study_fact_without_category(content: str) -> str:
    """Remove a structured fact's category while retaining its assertion."""

    cleaned = _clean_study_fact_text(content).rstrip(".;:")
    structured_fact = _STRUCTURED_STUDY_FACT.match(cleaned)
    if structured_fact is None:
        return cleaned
    subject = structured_fact.group(1)
    assertion = cleaned[structured_fact.end() :]
    return f"{subject} {assertion}".strip()


def study_fact_assertion(content: str) -> str:
    """Return only the assertion carried by a structured study fact."""

    cleaned = _clean_study_fact_text(content).rstrip(".;:")
    structured_fact = _STRUCTURED_STUDY_FACT.match(cleaned)
    if structured_fact is None:
        return cleaned
    return cleaned[structured_fact.end() :].strip()


def study_fact_assertion_identity(content: str) -> str:
    """Return the assertion identity without its optional category wrapper."""

    return content_identity(study_fact_assertion(content))


def study_assertion_identity(content: str) -> str:
    """Preserve distinct categorized subjects that share one assertion."""

    cleaned = _clean_study_fact_text(content).rstrip(".;:")
    structured_fact = _STRUCTURED_STUDY_FACT.match(cleaned)
    if structured_fact is None:
        return content_identity(cleaned)
    subject_identity = content_identity(structured_fact.group(1))
    assertion_identity = content_identity(cleaned[structured_fact.end() :])
    return f"{subject_identity} :: {assertion_identity}"


def has_informative_study_content(content: str) -> bool:
    """Reject clinical-alert labels and structured facts without an assertion."""

    cleaned = _clean_study_fact_text(content).rstrip(" ;:")
    if _CATEGORIZED_STUDY_TERM.fullmatch(cleaned):
        return False
    structured_fact = _STRUCTURED_STUDY_FACT.match(cleaned)
    if structured_fact is None:
        return True
    subject = structured_fact.group(1)
    assertion = cleaned[structured_fact.end() :]
    return _assertion_adds_information(subject=subject, assertion=assertion)


def _assertion_adds_information(*, subject: str, assertion: str) -> bool:
    subject_tokens = content_identity(subject).split()
    assertion_tokens = content_identity(assertion).split()
    if not assertion_tokens:
        return False
    return any(
        token not in _UNINFORMATIVE_ASSERTION_TOKENS
        and not any(_shares_lexical_stem(token, item) for item in subject_tokens)
        for token in assertion_tokens
    )


def _shares_lexical_stem(left: str, right: str) -> bool:
    comparable_length = min(len(left), len(right))
    if comparable_length < _MINIMUM_LEXICAL_STEM_LENGTH:
        return left == right
    return left[:_MINIMUM_LEXICAL_STEM_LENGTH] == right[:_MINIMUM_LEXICAL_STEM_LENGTH]


def _has_informative_summary_content(content: str) -> bool:
    cleaned = _clean_study_fact_text(content).rstrip(" ;:")
    return _CATEGORIZED_STUDY_TERM.fullmatch(
        cleaned
    ) is None and has_informative_study_content(cleaned)


def _clean_study_fact_text(content: str) -> str:
    without_headings = re.sub(r"(?m)^\s*#{1,6}\s*", "", content)
    without_bullets = re.sub(r"(?m)^\s*[-*]\s+", "", without_headings)
    return without_bullets.replace("**", "").replace("__", "").strip()


def build_ledger_grounded_drafts(
    *,
    lecture_id: str,
    drafts: Sequence[LearningUnitDraft],
    ledgers: Sequence[SemanticLedger],
) -> tuple[LearningUnitDraft, ...]:
    """Replace free-form prose with exact, timestamp-grounded ledger facts.

    Not called from `main()` in scripts/generate_personal_study_guides.py
    (2026-09-15): routing every draft through this unconditionally was the
    actual root cause of the "bullet list instead of prose" defect the
    operator rejected the corpus over -- it discarded the drafting stage's
    own grounded prose and rebuilt every apostila as a bare bullet list.
    Kept here, public and unit-tested, as a still-correct utility should a
    future document type need literal ledger-fact bullets again; the
    regression guard in
    tests/test_personal_study_generator.py::TestApostilaUsesDraftingProseDirectly
    asserts it does not silently creep back into `main()`."""

    drafts_by_sequence = {draft.pack_sequence: draft for draft in drafts}
    ledgers_by_sequence = {ledger.pack_sequence: ledger for ledger in ledgers}
    if len(drafts_by_sequence) != len(drafts) or len(ledgers_by_sequence) != len(
        ledgers
    ):
        raise ValueError("rascunhos e ledgers devem ter sequências únicas")
    if set(drafts_by_sequence) != set(ledgers_by_sequence):
        raise ValueError("rascunhos e ledgers devem cobrir as mesmas sequências")

    grounded: list[LearningUnitDraft] = []
    for sequence in sorted(drafts_by_sequence):
        draft = drafts_by_sequence[sequence]
        ledger = ledgers_by_sequence[sequence]
        if str(draft.lecture_id) != lecture_id or str(ledger.lecture_id) != lecture_id:
            raise ValueError("rascunho ou ledger pertence a outra aula")
        body_lines = _deduplicate_exact_text(
            (
                f"- **{_normalize_pt_br(fact.entity_name)} "
                f"({_normalize_pt_br(fact.category)}).** "
                f"{_normalize_pt_br(fact.clinical_context)}"
            )
            for fact in ledger.facts
        )
        warnings = _deduplicate_exact_text(
            _normalize_pt_br(fact.clinical_context)
            for fact in ledger.facts
            if _is_warning_fact(fact.category, fact.clinical_context)
        )
        grounded.append(
            LearningUnitDraft(
                lecture_id=draft.lecture_id,
                pack_sequence=sequence,
                title=draft.title,
                body_markdown="\n".join(body_lines),
                clinical_warnings=list(warnings),
            )
        )
    return tuple(grounded)


def _is_warning_fact(category: str, clinical_context: str) -> bool:
    searchable = f"{category} {clinical_context}".casefold()
    return any(marker in searchable for marker in _WARNING_MARKERS)


def build_personal_study_editions(
    *,
    lecture_id: str,
    lecture_title: str,
    drafts: Sequence[LearningUnitDraft],
    figures_by_pack_sequence: Mapping[int, tuple[SelectedFigure, ...]] | None = None,
    chapter_plan: ChapterPlan = (),
) -> dict[StudyEditionKind, StudyEdition]:
    """Build the mandatory complete and summarized editions.
    figures_by_pack_sequence (2026-09-15) places each selected figure in
    the COMPLETE edition's matching section only -- the summary edition
    stays text-only by design, it is meant to be a short revision pass,
    not a second illustrated document. An empty chapter_plan groups the
    clinical units into numbered parts of ten; a non-empty one names each
    chapter and routes every unit outside its ranges to a trailing
    complementary chapter."""

    clinical_drafts = _validated_clinical_drafts(lecture_id, drafts)
    complete_chapters = _build_complete_chapters(
        clinical_drafts, chapter_plan, figures_by_pack_sequence
    )
    complete = StudyEdition(
        kind=StudyEditionKind.COMPLETE,
        lecture_id=lecture_id,
        lecture_title=_normalize_lecture_title(lecture_title),
        chapters=complete_chapters,
        source_unit_count=len(clinical_drafts),
    )
    summary = StudyEdition(
        kind=StudyEditionKind.SUMMARY,
        lecture_id=lecture_id,
        lecture_title=_normalize_lecture_title(lecture_title),
        chapters=_build_summary_chapters(complete_chapters),
        source_unit_count=len(clinical_drafts),
    )
    return {
        StudyEditionKind.COMPLETE: complete,
        StudyEditionKind.SUMMARY: summary,
    }


def render_study_edition_markdown(edition: StudyEdition) -> str:
    """Render one edition as human-readable pt-BR Markdown."""

    lines = [
        f"# {edition.lecture_title}",
        "",
        f"## {edition.kind.pt_br_label}",
        "",
        (
            f"Material pessoal de estudo elaborado a partir de "
            f"{edition.source_unit_count} unidades clínicas da aula "
            f"{edition.lecture_id}."
        ),
        "",
        "## Sumário",
        "",
    ]
    lines.extend(f"- {chapter.title}" for chapter in edition.chapters)
    for chapter in edition.chapters:
        lines.extend(("", f"# {chapter.title}", ""))
        for section in chapter.sections:
            lines.extend((f"## {section.title}", ""))
            for figure in section.figures:
                lines.extend(
                    (
                        f"![{figure.caption}]({figure.relative_path})",
                        "",
                        f"*{figure.caption}*",
                        "",
                    )
                )
            lines.extend((section.body.strip(), ""))
            if section.warnings:
                lines.extend(("### Alertas e pontos de atenção", ""))
                lines.extend(f"- {warning}" for warning in section.warnings)
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_study_edition_typst(edition: StudyEdition) -> str:
    """Render one edition as a self-contained Typst document."""

    source_note = (
        f"Material pessoal de estudo - {edition.source_unit_count} unidades "
        f"clínicas - aula {edition.lecture_id}"
    )
    parts = academic_document_start(
        title=edition.lecture_title,
        subtitle=edition.kind.pt_br_label,
        source_note=source_note,
    )
    parts.extend(
        (
            "= Sumário",
            "#outline(title: none, indent: 1.2em)",
            "#pagebreak()",
        )
    )
    for chapter in edition.chapters:
        parts.append(f"#heading(level: 1)[#text({typst_string(chapter.title)})]")
        parts.append("#parbreak()")
        for section in chapter.sections:
            parts.append(f"#heading(level: 2)[#text({typst_string(section.title)})]")
            parts.append("#parbreak()")
            for figure in section.figures:
                parts.append(
                    render_academic_figure(figure.relative_path, figure.caption)
                )
            parts.extend(render_academic_body(section.body))
            if section.warnings:
                parts.append(render_academic_warning_block(section.warnings))
    return "\n".join(parts) + "\n"


def _validated_clinical_drafts(
    lecture_id: str,
    drafts: Sequence[LearningUnitDraft],
) -> tuple[LearningUnitDraft, ...]:
    ordered = tuple(sorted(drafts, key=lambda draft: draft.pack_sequence))
    seen_sequences: set[int] = set()
    clinical: list[LearningUnitDraft] = []
    for draft in ordered:
        if str(draft.lecture_id) != lecture_id:
            raise ValueError(
                f"draft lecture {draft.lecture_id!r} does not match lecture "
                f"{lecture_id!r}"
            )
        if draft.pack_sequence in seen_sequences:
            raise ValueError(f"duplicate draft sequence {draft.pack_sequence}")
        seen_sequences.add(draft.pack_sequence)
        if not is_administrative_study_unit(draft.title):
            clinical.append(draft)
    if not clinical:
        raise ValueError(f"lecture {lecture_id!r} has no clinical study units")
    return tuple(clinical)


def is_administrative_study_unit(title: str) -> bool:
    """Return whether a draft title represents non-clinical administration."""

    normalized = _normalize_pt_br(title).casefold()
    return _SPEAKER_INTRODUCTION_TITLE.search(normalized) is not None or any(
        marker in normalized for marker in _ADMINISTRATIVE_TITLE_MARKERS
    )


def _build_complete_chapters(
    drafts: Sequence[LearningUnitDraft],
    plan: ChapterPlan,
    figures_by_pack_sequence: Mapping[int, tuple[SelectedFigure, ...]] | None = None,
) -> tuple[StudyChapter, ...]:
    if not plan:
        return tuple(
            StudyChapter(
                title=f"Parte {index}",
                sections=tuple(
                    _complete_section(draft, figures_by_pack_sequence)
                    for draft in group
                ),
            )
            for index, group in enumerate(batched(drafts, 10), start=1)
        )
    chapters: list[StudyChapter] = []
    assigned: set[int] = set()
    for chapter_title, first_sequence, last_sequence in plan:
        chapter_drafts = tuple(
            draft
            for draft in drafts
            if first_sequence <= draft.pack_sequence <= last_sequence
        )
        if not chapter_drafts:
            continue
        assigned.update(draft.pack_sequence for draft in chapter_drafts)
        chapters.append(
            StudyChapter(
                title=chapter_title,
                sections=tuple(
                    _complete_section(draft, figures_by_pack_sequence)
                    for draft in chapter_drafts
                ),
            )
        )
    unassigned = tuple(draft for draft in drafts if draft.pack_sequence not in assigned)
    if unassigned:
        chapters.append(
            StudyChapter(
                title="Conteúdo complementar",
                sections=tuple(
                    _complete_section(draft, figures_by_pack_sequence)
                    for draft in unassigned
                ),
            )
        )
    return tuple(chapters)


def _complete_section(
    draft: LearningUnitDraft,
    figures_by_pack_sequence: Mapping[int, tuple[SelectedFigure, ...]] | None = None,
) -> StudySection:
    figures = (
        figures_by_pack_sequence.get(draft.pack_sequence, ())
        if figures_by_pack_sequence
        else ()
    )
    return StudySection(
        title=_normalize_pt_br(draft.title),
        body=_normalize_pt_br(draft.body_markdown),
        warnings=tuple(
            _normalize_pt_br(warning) for warning in draft.clinical_warnings
        ),
        source_sequences=(draft.pack_sequence,),
        figures=figures,
    )


def _build_summary_chapters(
    complete_chapters: Sequence[StudyChapter],
) -> tuple[StudyChapter, ...]:
    return tuple(_summarize_chapter(chapter) for chapter in complete_chapters)


def _summarize_chapter(chapter: StudyChapter) -> StudyChapter:
    ranked_points = [
        point for section in chapter.sections for point in _best_section_points(section)
    ]
    selected_points = _deduplicate_ranked_points(ranked_points)[
        :_MAXIMUM_SUMMARY_POINTS_PER_CHAPTER
    ]
    ordered_points = sorted(
        selected_points,
        key=lambda point: (point.source_sequence, -point.score, point.text),
    )
    body = "\n".join(f"- {point.text}" for point in ordered_points)
    point_assertions_by_sequence = {
        (point.source_sequence, study_fact_assertion_identity(point.text))
        for point in ordered_points
    }
    warnings = _deduplicate_text(
        warning
        for section in chapter.sections
        for warning in section.warnings
        if _has_informative_summary_content(warning)
        and not any(
            (
                source_sequence,
                study_fact_assertion_identity(warning),
            )
            in point_assertions_by_sequence
            for source_sequence in section.source_sequences
        )
    )[:_MAXIMUM_SUMMARY_WARNINGS_PER_CHAPTER]
    source_sequences = tuple(
        sorted(
            {
                sequence
                for section in chapter.sections
                for sequence in section.source_sequences
            }
        )
    )
    return StudyChapter(
        title=chapter.title,
        sections=(
            StudySection(
                title="Pontos essenciais",
                body=body,
                warnings=warnings,
                source_sequences=source_sequences,
            ),
        ),
    )


def _best_section_points(section: StudySection) -> tuple[_RankedPoint, ...]:
    sequence = section.source_sequences[0]
    points = [
        _RankedPoint(
            text=sentence,
            score=_summary_score(sentence),
            source_sequence=sequence,
        )
        for sentence in _sentences(section.body)
        if len(sentence) >= 30 and _has_informative_summary_content(sentence)
    ]
    if not points:
        return ()
    return (max(points, key=lambda point: (point.score, len(point.text))),)


def _summary_score(sentence: str) -> int:
    lowered = sentence.casefold()
    keyword_score = sum(3 for keyword in _SUMMARY_KEYWORDS if keyword in lowered)
    numeric_score = 2 if any(character.isdigit() for character in sentence) else 0
    concise_score = 1 if len(sentence) <= 240 else 0
    return keyword_score + numeric_score + concise_score


def _sentences(markdown: str) -> tuple[str, ...]:
    plain = _plain_text(markdown)
    return tuple(
        sentence.strip(" -•\t")
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", plain)
        if sentence.strip(" -•\t")
    )


def _deduplicate_ranked_points(
    points: Sequence[_RankedPoint],
) -> tuple[_RankedPoint, ...]:
    selected: dict[str, _RankedPoint] = {}
    for point in sorted(
        points,
        key=lambda candidate: (-candidate.score, candidate.source_sequence),
    ):
        identity = study_assertion_identity(point.text)
        selected.setdefault(identity, point)
    return tuple(selected.values())


def _deduplicate_text(values: Iterable[str]) -> tuple[str, ...]:
    selected: dict[str, str] = {}
    for value in values:
        selected.setdefault(study_assertion_identity(value), value)
    return tuple(selected.values())


def _deduplicate_exact_text(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _normalize_lecture_title(title: str) -> str:
    """Rewrite a trailing "Super Video" label as a separated pt-BR suffix,
    so "<title> Super Video" reads "<title> - vídeo complementar"."""

    single_line_title = _join_super_video_line_break(title)
    base_title, label_count = _SUPPLEMENTARY_VIDEO_TITLE_SUFFIX.subn(
        "", single_line_title
    )
    if not label_count or not base_title.strip():
        return _normalize_pt_br(title)
    return f"{_normalize_pt_br(base_title)} - vídeo complementar"


def _join_super_video_line_break(value: str) -> str:
    """Replace a real or literal escaped line break before "Super Video"
    with a space, so the label reads as part of the same line."""

    return value.replace("\\nSuper Video", " Super Video").replace(
        "\nSuper Video", " Super Video"
    )


def _normalize_pt_br(value: str) -> str:
    normalized = value.replace("\\nSuper Video", " ").replace("\nSuper Video", " ")
    for pattern, replacement in _TERM_REPLACEMENTS:
        normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
    return re.sub(r"[ \t]+", " ", normalized).strip()


def _plain_text(markdown: str) -> str:
    without_headings = re.sub(r"(?m)^\s*#{1,6}\s*", "", markdown)
    without_emphasis = without_headings.replace("**", "").replace("__", "")
    return re.sub(r"(?m)^\s*[-*]\s+", "- ", without_emphasis)
