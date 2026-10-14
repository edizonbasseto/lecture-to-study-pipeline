"""shuffle_question_options tests: the correct answer's TEXT must survive
a reshuffle unchanged, only its LETTER may move, and the distribution
across many questions must not still cluster on one letter -- the exact
defect an audit of 2172 already-generated questions found (61% at "A",
5% at "D")."""

import random

from lecture_study.domain.models import MockQuestion
from lecture_study.exams.option_shuffle import shuffle_question_options


class _ReversingRandom:
    """A `random.Random`-shaped stand-in whose "shuffle" is a plain
    reversal -- deterministic, so a test can assert the exact resulting
    letter instead of only a property that survives any permutation."""

    def shuffle(self, sequence: list[object]) -> None:
        sequence.reverse()


def _question(correct_answer: str = "A") -> MockQuestion:
    return MockQuestion(
        question_text="Cão de 8 anos com FC 40bpm. Qual a conduta?",
        options={
            "A": "Monitorar e investigar causa.",
            "B": "Nenhuma conduta.",
            "C": "Eutanasia imediata.",
            "D": "Aumentar a dose de anestesico.",
        },
        correct_answer=correct_answer,
        explanation="Bradicardia sinusal exige monitoramento contínuo.",
    )


class TestShuffleQuestionOptions:
    def test_the_correct_text_survives_at_whatever_letter_it_lands_on(self) -> None:
        question = _question(correct_answer="A")
        correct_text = question.options["A"]

        shuffled = shuffle_question_options(question, random.Random(1))

        assert shuffled.options[shuffled.correct_answer] == correct_text

    def test_every_option_text_is_preserved_as_a_set_only_reordered(self) -> None:
        question = _question(correct_answer="A")

        shuffled = shuffle_question_options(question, random.Random(7))

        assert set(shuffled.options.values()) == set(question.options.values())
        assert set(shuffled.options) == {"A", "B", "C", "D"}

    def test_question_text_and_explanation_are_untouched(self) -> None:
        question = _question(correct_answer="A")

        shuffled = shuffle_question_options(question, random.Random(2))

        assert shuffled.question_text == question.question_text
        assert shuffled.explanation == question.explanation

    def test_two_identically_worded_distractors_never_confuse_which_is_correct(
        self,
    ) -> None:
        # Regression: matching the correct answer by TEXT after shuffling
        # would misfire here, since two options share the same wording --
        # a deterministic "shuffle" (reverse) makes the expected letter
        # exact, so a text-matching implementation (which would always
        # resolve to the ORIGINAL "A") is caught red-handed.
        question = MockQuestion(
            question_text="Pergunta",
            options={
                "A": "Conduta X.",  # same wording as the real correct answer
                "B": "Conduta X.",  # the actual correct answer
                "C": "Conduta Y.",
                "D": "Conduta Z.",
            },
            correct_answer="B",
            explanation="Explicação.",
        )

        shuffled = shuffle_question_options(question, _ReversingRandom())

        # Reversing (A,B,C,D) -> (D,C,B,A) puts the entry that WAS at "B"
        # (the true correct one, tracked by identity) at letter "C".
        assert shuffled.correct_answer == "C"

    def test_distribution_across_many_questions_is_not_clustered_on_one_letter(
        self,
    ) -> None:
        """Mutation-testing-style check on the real-world defect: over a
        large sample, the correct answer must not still land on "A" far
        more often than the other three letters."""
        random_source = random.Random(42)
        landing_letters = [
            shuffle_question_options(
                _question(correct_answer="A"), random_source
            ).correct_answer
            for _ in range(4000)
        ]

        counts = {letter: landing_letters.count(letter) for letter in "ABCD"}
        for letter, count in counts.items():
            assert 800 < count < 1200, f"letter {letter} landed {count}/4000 times"
