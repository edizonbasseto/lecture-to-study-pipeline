"""Word error rate: normalization, hand-computed counts and the empty reference."""

import unicodedata

import pytest

from lecture_study.demo.wer import normalized_words, word_error_counts


def counts_between(reference: str, hypothesis: str) -> tuple[int, int, int, int]:
    counts = word_error_counts(
        normalized_words(reference), normalized_words(hypothesis)
    )
    return (
        counts.reference_words,
        counts.substitutions,
        counts.deletions,
        counts.insertions,
    )


def test_identical_texts_have_no_errors() -> None:
    counts = word_error_counts(
        normalized_words("a margem profunda"), normalized_words("a margem profunda")
    )

    assert counts.errors() == 0
    assert counts.rate() == 0.0


def test_one_substituted_word_is_one_substitution() -> None:
    assert counts_between("a margem fascial profunda", "a margem facial profunda") == (
        4,
        1,
        0,
        0,
    )


def test_one_missing_word_is_one_deletion() -> None:
    assert counts_between("a margem fascial profunda", "a margem profunda") == (
        4,
        0,
        1,
        0,
    )


def test_one_extra_word_is_one_insertion() -> None:
    assert counts_between("a margem profunda", "a margem fascial profunda") == (
        3,
        0,
        0,
        1,
    )


def test_empty_hypothesis_deletes_every_reference_word() -> None:
    assert counts_between("a margem profunda", "") == (3, 0, 3, 0)


def test_punctuation_and_capitals_are_ignored() -> None:
    assert normalized_words("Histopatológico, ") == normalized_words("histopatológico")


def test_hyphenated_words_stay_whole() -> None:
    assert normalized_words("pré-operatório e o d'água") == (
        "pré-operatório",
        "e",
        "o",
        "d'água",
    )


def test_decomposed_accents_match_composed_accents() -> None:
    composed = "histopatológico"
    decomposed = unicodedata.normalize("NFD", composed)

    assert decomposed != composed
    assert normalized_words(decomposed) == normalized_words(composed)


def test_rate_is_errors_over_reference_words() -> None:
    counts = word_error_counts(
        normalized_words("um dois tres quatro"), normalized_words("um dois cinco")
    )

    assert (counts.substitutions, counts.deletions, counts.insertions) == (1, 1, 0)
    assert counts.rate() == 0.5


def test_empty_reference_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one word"):
        word_error_counts((), normalized_words("qualquer texto"))
