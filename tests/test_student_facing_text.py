"""Student-facing text guard tests: the two defects lecture 10002's first
real mock exam exhibited -- English questions and the internal "Evidence
Pack" name addressed to the student -- must be detected offline, and
correct pt-BR clinical prose must never be flagged."""

import pytest

from lecture_study.quality.student_facing_text import (
    describe_student_facing_text_defects,
    student_facing_text_defects,
)


_ENGLISH_QUESTION_FROM_THE_REAL_RUN = (
    "A 6-year-old dog presents with a 1.2 cm dermal mass. Which biopsy "
    "technique is preferred according to the Evidence Pack?"
)
_LEAKING_EXPLANATION_FROM_THE_REAL_RUN = (
    "O Evidence Pack indica que biópsia incisional é preferida para lesões "
    "com diâmetro maior que 0,5 cm."
)


def _defect_descriptions(location: str, text: str) -> list[str]:
    return [defect.defect for defect in student_facing_text_defects([(location, text)])]


class TestStudentFacingTextDefects:
    def test_english_question_from_the_real_run_is_reported(self) -> None:
        assert "is not written in Brazilian Portuguese" in _defect_descriptions(
            "question 1", _ENGLISH_QUESTION_FROM_THE_REAL_RUN
        )

    def test_internal_name_in_an_otherwise_portuguese_sentence_is_reported(
        self,
    ) -> None:
        assert "refers to an internal pipeline name" in _defect_descriptions(
            "question 1 explanation", _LEAKING_EXPLANATION_FROM_THE_REAL_RUN
        )

    def test_spoken_unit_id_is_reported(self) -> None:
        assert "refers to an internal pipeline name" in _defect_descriptions(
            "line 4 (Expert)", "Como visto em [unit-003], a fáscia define o plano."
        )

    def test_clean_clinical_portuguese_is_not_reported(self) -> None:
        assert (
            student_facing_text_defects(
                [
                    (
                        "question 1",
                        "Um cão de 6 anos apresenta massa dérmica de 1,2 cm. "
                        "Qual técnica de biópsia é indicada nesse caso?",
                    ),
                    (
                        "question 1 explanation",
                        "A biópsia incisional é indicada para lesões com "
                        "diâmetro maior que 0,5 cm, pois preserva margens "
                        "para a excisão definitiva.",
                    ),
                ]
            )
            == []
        )

    def test_english_origin_surgical_vocabulary_alone_is_not_reported(self) -> None:
        assert (
            student_facing_text_defects(
                [
                    (
                        "card 2 answer",
                        "O flap de avanço cobre o defeito após a excisão do "
                        "shunt portossistêmico.",
                    )
                ]
            )
            == []
        )

    @pytest.mark.parametrize(
        "clinical_sentence",
        [
            "A contaminação do material cirúrgico aumenta o risco de infecção.",
            "A drenagem do conteúdo purulento precede a lavagem da cavidade.",
            "O material orgânico remanescente no leito deve ser removido.",
            "Avalia-se o conteúdo gástrico antes da indução anestésica.",
            "Utiliza-se material de sutura absorvível no plano subcutâneo.",
        ],
    )
    def test_surgical_prose_naming_material_or_content_is_not_reported(
        self, clinical_sentence: str
    ) -> None:
        assert student_facing_text_defects([("q1", clinical_sentence)]) == []

    def test_a_hyphenated_surgical_technique_is_not_reported(self) -> None:
        assert (
            student_facing_text_defects(
                [("question 2 option A", "Técnica de pull-through retal")]
            )
            == []
        )

    def test_a_short_english_option_is_reported(self) -> None:
        assert "is not written in Brazilian Portuguese" in _defect_descriptions(
            "question 1 option C", "Complete excision with wide margins"
        )

    def test_a_short_portuguese_option_is_not_reported(self) -> None:
        assert (
            student_facing_text_defects(
                [("question 1 option C", "Excisão completa com margens amplas")]
            )
            == []
        )

    def test_evidence_from_generated_text_is_collapsed_and_capped(self) -> None:
        defects = student_facing_text_defects(
            [("q1", "segundo a transcri" + "a" * 5000)]
        )

        assert len(defects) == 1
        assert len(defects[0].evidence) <= 83
        assert "\n" not in defects[0].evidence

    def test_description_names_the_location_and_the_evidence(self) -> None:
        defects = student_facing_text_defects(
            [("question 3", _LEAKING_EXPLANATION_FROM_THE_REAL_RUN)]
        )

        description = describe_student_facing_text_defects(defects)

        assert "question 3" in description
        assert "evidence pack" in description
