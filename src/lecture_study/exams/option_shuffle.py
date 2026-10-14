"""Randomizes a mock-exam question's option lettering (SITE-09 follow-up).

An audit of every already-generated exam.json (2026-09-18) found the
correct answer sitting at letter "A" 61% of the time and "D" only 5% of
the time, across 2172 questions -- the Codex mock-exam prompt asks for
"the correct option's letter" without ever asking for a random position,
and the model habitually drafts the correct option first. A four-option
multiple-choice exam where the correct answer is rarely the last option
is not a fair test of recall.
"""

import random

from lecture_study.domain.models import MockQuestion

_OPTION_LETTERS = ("A", "B", "C", "D")


def shuffle_question_options(
    question: MockQuestion, random_source: random.Random
) -> MockQuestion:
    """Return a copy of question with its four options reassigned to a
    uniformly random letter, preserving which TEXT is correct.

    Correctness is tracked by the original option's identity, not by
    matching its text afterward -- two distractors could coincidentally
    share the same wording, and text-matching would then risk picking
    the wrong one as the new correct_answer.
    """
    entries = [
        (letter == question.correct_answer, question.options[letter])
        for letter in _OPTION_LETTERS
    ]
    random_source.shuffle(entries)
    shuffled_options = {
        letter: text for letter, (_, text) in zip(_OPTION_LETTERS, entries, strict=True)
    }
    new_correct_answer = next(
        letter
        for letter, (is_correct, _) in zip(_OPTION_LETTERS, entries, strict=True)
        if is_correct
    )
    return question.model_copy(
        update={"options": shuffled_options, "correct_answer": new_correct_answer}
    )
