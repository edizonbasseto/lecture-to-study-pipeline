"""Semantic ledger groundedness diagnostic tests.

Covers the three tiers of `lecture_study.semantic.ledger_groundedness`
against already-loaded, in-memory `SemanticLedger`/`EvidencePack`/
`LectureTranscriptionResult` models -- no filesystem access, no
subprocess, no inference happens anywhere in this module or in this test
suite. Every transcript passage below is a synthetic fixture modeled on an
observed defect: a short Portuguese teaching dialogue written to reproduce
the exact structural properties each tier is calibrated against (word
spacing, numeral adjacency, and a hypothetical the speaker corrects),
without quoting any real recording.
"""

from lecture_study.domain.models import (
    CanonicalFrame,
    EvidencePack,
    LectureTranscriptionResult,
    SemanticFact,
    SemanticLedger,
    TranscriptionSegment,
)
from lecture_study.semantic.ledger_groundedness import (
    GroundednessTier,
    canonical_numeral,
    find_numeric_collocation_candidates,
    find_term_absence_candidates,
    find_timestamp_traceability_candidates,
    is_numeral_token,
    tokenize,
)


def _fact(
    entity_name: str,
    clinical_context: str = "",
    timestamp_ref: float = 0.0,
    category: str = "instrumento",
) -> SemanticFact:
    return SemanticFact(
        entity_name=entity_name,
        category=category,
        clinical_context=clinical_context,
        timestamp_ref=timestamp_ref,
    )


def _ledger(
    facts: list[SemanticFact], lecture_id: str = "10002", pack_sequence: int = 2
) -> SemanticLedger:
    return SemanticLedger(
        lecture_id=lecture_id, pack_sequence=pack_sequence, facts=facts
    )


def _segments(quotes: tuple[tuple[float, str], ...]) -> list[TranscriptionSegment]:
    return [
        TranscriptionSegment(start_time=start, end_time=start + 3.0, text=text)
        for start, text in quotes
    ]


def _transcript(
    quotes: tuple[tuple[float, str], ...],
    frames: list[CanonicalFrame] | None = None,
    lecture_id: str = "10002",
) -> LectureTranscriptionResult:
    return LectureTranscriptionResult(
        lecture_id=lecture_id,
        frames=frames if frames is not None else [],
        segments=_segments(quotes),
    )


# Synthetic pack 2 dialogue modeled on an observed defect. It carries both
# calibration facts: the reusable-pen claim ("caneta" and "reutilizável"
# are both spoken, but in different sentences, many token positions apart)
# and the "nylon três zeros" gauge claim (fabricated -- "três zeros" is
# never said; only "dois zeros" is, twice, and the lone "três" sits four
# token positions away from the nearest "zeros").
_PACK_2_QUOTES: tuple[tuple[float, str], ...] = (
    (492.6, "Antes da incisão, vou marcar a pele com esta caneta cirúrgica."),
    (497.24, "A caneta de tinta azul resiste melhor ao álcool da antissepsia."),
    (501.26, "Outras cores também servem, mas desbotam com mais facilidade."),
    (505.58, "Este modelo é reutilizável, dá para esterilizar e usar de novo."),
    (509.4, "O descartável é mais barato, mas é de uso único, tá?"),
    (593.86, "Pronto, pele marcada."),
    (596.86, "Alguém tem nylon dois zeros?"),
    (599.0, "Só achei estes três."),
    (601.66, "Peguem um nylon dois zeros, porque a agulha é um pouco maior,"),
    (607.78, "e o que importa aqui é a agulha, não a espessura do fio, tá?"),
    (614.5, "Depois prendam o tecido antes de fazer a incisão com a lâmina."),
)

# Synthetic dialogue modeled on an observed defect: a Socratic "1
# centímetro" hypothetical the speaker immediately corrects. "margem" --
# the pre-correction fact's entity_name -- occurs once, in the correction
# itself, an ordinary word this checker's term-presence tiers cannot see is
# being used to reject the hypothetical rather than affirm it.
_MARGEM_QUOTES: tuple[tuple[float, str], ...] = (
    (157.42, "E se o paciente também vai receber eletroquimioterapia?"),
    (167.8, "Ainda não tenho o histopatológico, mas vou associar a eletroquimio."),
    (171.92, "Então eu retiro, sei lá, 1 centímetro, porque tenho a eletroquimio?"),
    (174.86, "Na verdade, pessoal, não é assim."),
    (177.64, "A eletroquimioterapia é sempre uma terapia adjuvante."),
    (184.42, "Ela só entra quando eu não consigo a margem cirúrgica ideal."),
)

# Fabricated fact: "três zeros" appears nowhere in the dialogue; the only
# gauge the speaker names is "dois zeros".
_NYLON_TRES_ZEROS_FACT = _fact(
    entity_name="nylon três zeros",
    clinical_context="fio com agulha maior",
    timestamp_ref=614.5,
)

# Supported fact: both of its words are spoken in pack 2, in different
# sentences.
_CANETA_REUTILIZAVEL_FACT = _fact(
    entity_name="caneta reutilizável",
    clinical_context="caneta que pode ser utilizada várias vezes",
    timestamp_ref=484.12,
)

# Legitimate counterpart the same pair of tiers must not flag: "dois
# zeros" is what the speaker actually says, twice, always with the numeral
# immediately adjacent to "zeros".
_NYLON_DOIS_ZEROS_FACT = _fact(
    entity_name="nylon dois zeros",
    clinical_context="fio recomendado para sutura",
    timestamp_ref=614.5,
)

# Affirmed-hypothetical (Shape A) fact. Its entity_name is the ordinary,
# ubiquitous word "margem" -- the fabricated content lives entirely in
# clinical_context, a field this checker's term-presence tiers never read.
_PRE_CORRECTION_MARGEM_FACT = _fact(
    entity_name="margem",
    category="medida",
    clinical_context="1 centímetro de margem se tiver eletroquimioterapia",
    timestamp_ref=165.52,
)


class TestTokenize:
    def test_decimal_percentage_kept_as_one_literal(self) -> None:
        assert tokenize("Taxa de sucesso de 86,5% dos casos") == (
            "taxa",
            "de",
            "sucesso",
            "de",
            "86,5%",
            "dos",
            "casos",
        )

    def test_hyphenated_range_kept_as_one_literal(self) -> None:
        assert tokenize("Retorno em 24-48 horas") == ("retorno", "em", "24-48", "horas")

    def test_case_and_accent_folding(self) -> None:
        assert tokenize("MARGEM Cirúrgica") == ("margem", "cirurgica")


class TestIsNumeralToken:
    def test_indefinite_articles_are_not_numerals(self) -> None:
        assert is_numeral_token("um") is False
        assert is_numeral_token("uma") is False

    def test_zero_words_are_not_numerals(self) -> None:
        # "zero"/"zeros" denote a suture-gauge unit in this domain, not a
        # count -- see the module docstring.
        assert is_numeral_token("zero") is False
        assert is_numeral_token("zeros") is False

    def test_digit_literal_is_a_numeral(self) -> None:
        assert is_numeral_token("3") is True

    def test_portuguese_number_word_is_a_numeral(self) -> None:
        assert is_numeral_token("tres") is True
        assert is_numeral_token("dois") is True

    def test_ordinary_word_is_not_a_numeral(self) -> None:
        assert is_numeral_token("agulha") is False


class TestCanonicalNumeral:
    def test_number_word_maps_to_digit_form(self) -> None:
        assert canonical_numeral("tres") == "3"
        assert canonical_numeral("dois") == "2"

    def test_digit_literal_is_unchanged(self) -> None:
        assert canonical_numeral("3") == "3"


class TestFindTermAbsenceCandidates:
    def test_um_sinal_clinico_comum_fact_is_not_reported(self) -> None:
        # A non-numeral fact whose every word is real and present.
        ledger = _ledger([_fact(entity_name="sinal clínico comum")])
        transcript = _transcript(((10.0, "Esse é um sinal clínico comum em cães."),))
        assert find_term_absence_candidates(ledger, transcript) == []

    def test_caneta_reutilizavel_fact_is_not_reported(self) -> None:
        # Calibration case: every word of the entity_name occurs somewhere
        # in the lecture ("caneta" twice, "reutilizável" once, 22 token
        # positions apart) even though the exact phrase is never
        # spoken contiguously -- this is paraphrase/compression, not
        # fabrication, and the union-of-individual-words design is what
        # keeps this tier from flagging it. See the module docstring.
        ledger = _ledger([_CANETA_REUTILIZAVEL_FACT])
        transcript = _transcript(_PACK_2_QUOTES)
        assert find_term_absence_candidates(ledger, transcript) == []

    def test_pre_correction_margem_fact_is_not_reported(self) -> None:
        # The locked blind spot: "margem" is an ordinary, present word:
        # entity_name carries no trace of the fabricated "1 centímetro"
        # claim, which lives entirely in clinical_context.
        ledger = _ledger([_PRE_CORRECTION_MARGEM_FACT], pack_sequence=1)
        transcript = _transcript(_MARGEM_QUOTES)
        assert find_term_absence_candidates(ledger, transcript) == []

    def test_fact_with_a_wholly_unspoken_word_is_reported(self) -> None:
        ledger = _ledger([_fact(entity_name="reusable marking pen")])
        transcript = _transcript(_PACK_2_QUOTES)
        candidates = find_term_absence_candidates(ledger, transcript)
        assert len(candidates) == 1
        assert candidates[0].tier is GroundednessTier.TERM_ABSENCE
        assert "reusable" in candidates[0].detail
        assert "marking" in candidates[0].detail
        assert "pen" in candidates[0].detail


class TestFindNumericCollocationCandidates:
    def test_tres_zeros_is_reported_despite_bare_tres_occurring_elsewhere(self) -> None:
        # "três" occurs in the transcript ("Só achei estes três.",
        # 599.0s, unrelated to gauge size) -- a bare-token absence check
        # would miss this fact entirely. The collocation tier still
        # reports it: "três" never sits within the bounded gap of
        # "zeros" anywhere in the lecture.
        ledger = _ledger([_NYLON_TRES_ZEROS_FACT])
        transcript = _transcript(_PACK_2_QUOTES)
        candidates = find_numeric_collocation_candidates(ledger, transcript)
        assert len(candidates) == 1
        assert candidates[0].tier is GroundednessTier.NUMERIC_COLLOCATION
        assert candidates[0].fact == _NYLON_TRES_ZEROS_FACT
        assert "tres" in candidates[0].detail
        assert "zeros" in candidates[0].detail

    def test_dois_zeros_is_not_reported(self) -> None:
        # The legitimate counterpart: "dois" sits immediately adjacent to
        # "zeros" every time the lecturer says it.
        ledger = _ledger([_NYLON_DOIS_ZEROS_FACT])
        transcript = _transcript(_PACK_2_QUOTES)
        assert find_numeric_collocation_candidates(ledger, transcript) == []

    def test_um_sinal_clinico_comum_fact_is_not_reported(self) -> None:
        # "um" is an indefinite article, not a numeral claim -- no
        # numeral token exists in this entity_name at all.
        ledger = _ledger([_fact(entity_name="X é um sinal clínico comum")])
        transcript = _transcript(_PACK_2_QUOTES)
        assert find_numeric_collocation_candidates(ledger, transcript) == []

    def test_pre_correction_margem_fact_is_not_reported(self) -> None:
        # The locked blind spot, restated for this tier: entity_name
        # "margem" carries no numeral at all -- the "1" lives only in
        # clinical_context, a field this tier never reads.
        ledger = _ledger([_PRE_CORRECTION_MARGEM_FACT], pack_sequence=1)
        transcript = _transcript(_MARGEM_QUOTES)
        assert find_numeric_collocation_candidates(ledger, transcript) == []


class TestFindTimestampTraceabilityCandidates:
    def test_timestamp_inside_pack_window_is_not_reported(self) -> None:
        ledger = _ledger([_fact(entity_name="margem", timestamp_ref=500.0)])
        pack = EvidencePack(
            lecture_id="10002", pack_sequence=2, start_time=450.76, end_time=900.4
        )
        assert find_timestamp_traceability_candidates(ledger, pack) == []

    def test_timestamp_at_window_end_is_reported(self) -> None:
        # Half-open: end_time itself belongs to the next pack, not this one.
        ledger = _ledger([_fact(entity_name="margem", timestamp_ref=900.4)])
        pack = EvidencePack(
            lecture_id="10002", pack_sequence=2, start_time=450.76, end_time=900.4
        )
        candidates = find_timestamp_traceability_candidates(ledger, pack)
        assert len(candidates) == 1
        assert candidates[0].tier is GroundednessTier.TIMESTAMP_TRACEABILITY

    def test_timestamp_before_window_start_is_reported(self) -> None:
        ledger = _ledger([_fact(entity_name="margem", timestamp_ref=10.0)])
        pack = EvidencePack(
            lecture_id="10002", pack_sequence=2, start_time=450.76, end_time=900.4
        )
        candidates = find_timestamp_traceability_candidates(ledger, pack)
        assert len(candidates) == 1


class TestTiersAreIndependent:
    def test_checker_reports_nylon_tres_zeros_and_not_caneta_reutilizavel(self) -> None:
        # The end-to-end calibration claim: across all three tiers, the
        # fabricated fact is reported and the verbatim-supported one is
        # not.
        ledger = _ledger([_NYLON_TRES_ZEROS_FACT, _CANETA_REUTILIZAVEL_FACT])
        transcript = _transcript(_PACK_2_QUOTES)
        pack = EvidencePack(
            lecture_id="10002", pack_sequence=2, start_time=450.76, end_time=900.4
        )

        term_absence = find_term_absence_candidates(ledger, transcript)
        numeric_collocation = find_numeric_collocation_candidates(ledger, transcript)
        timestamp_traceability = find_timestamp_traceability_candidates(ledger, pack)

        reported_facts = {
            candidate.fact.entity_name
            for candidate in (
                *term_absence,
                *numeric_collocation,
                *timestamp_traceability,
            )
        }
        assert "nylon três zeros" in reported_facts
        assert "caneta reutilizável" not in reported_facts

    def test_tiers_never_share_a_candidate(self) -> None:
        ledger = _ledger([_NYLON_TRES_ZEROS_FACT])
        transcript = _transcript(_PACK_2_QUOTES)
        term_absence = find_term_absence_candidates(ledger, transcript)
        numeric_collocation = find_numeric_collocation_candidates(ledger, transcript)
        term_absence_tiers = {candidate.tier for candidate in term_absence}
        numeric_collocation_tiers = {
            candidate.tier for candidate in numeric_collocation
        }
        assert term_absence_tiers <= {GroundednessTier.TERM_ABSENCE}
        assert numeric_collocation_tiers <= {GroundednessTier.NUMERIC_COLLOCATION}


class TestCheckerIsPure:
    def test_operates_on_in_memory_models_only(self) -> None:
        # No file on disk is referenced anywhere in this test: every
        # model is constructed directly from literal Python fixtures.
        ledger = _ledger([_fact(entity_name="tecido")])
        transcript = _transcript(((1.0, "O tecido foi removido."),))
        pack = EvidencePack(
            lecture_id="10002", pack_sequence=1, start_time=0.0, end_time=10.0
        )
        assert find_term_absence_candidates(ledger, transcript) == []
        assert find_numeric_collocation_candidates(ledger, transcript) == []
        assert find_timestamp_traceability_candidates(ledger, pack) == []
