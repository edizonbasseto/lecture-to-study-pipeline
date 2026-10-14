from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace

import pytest

from lecture_study.flashcards import generator as flashcard_generator_module
from lecture_study.publishing import pipeline as publishing_pipeline_module
from lecture_study.quality import DeliveryArtifactKind, write_delivery_outputs
from lecture_study.tts import generator as tts_generator_module


@pytest.fixture
def legacy_certification_bypass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def verify_publishing(lecture_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            document=publishing_pipeline_module.PublishingOrchestrator._load_canonical_document(
                lecture_id
            ),
            global_index=publishing_pipeline_module.PublishingOrchestrator._load_global_index(
                lecture_id
            ),
        )

    def verify_flashcards(lecture_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            global_index=flashcard_generator_module.AnkiDeckGenerator._load_global_index(
                lecture_id
            )
        )

    def issue_publishing(
        lecture_id: str,
        artifact_kind: DeliveryArtifactKind,
        *,
        expected_output_bytes: Mapping[str, bytes],
    ) -> None:
        write_delivery_outputs(
            publishing_pipeline_module.PUBLISHED_OUTPUT_DIR,
            lecture_id,
            artifact_kind,
            expected_output_bytes,
        )

    def issue_flashcards(
        lecture_id: str,
        artifact_kind: DeliveryArtifactKind,
        *,
        expected_output_bytes: Mapping[str, bytes],
    ) -> None:
        write_delivery_outputs(
            flashcard_generator_module.FLASHCARDS_OUTPUT_DIR,
            lecture_id,
            artifact_kind,
            expected_output_bytes,
        )

    monkeypatch.setattr(
        publishing_pipeline_module,
        "require_publication_ready",
        verify_publishing,
    )
    monkeypatch.setattr(
        flashcard_generator_module,
        "require_publication_ready",
        verify_flashcards,
    )
    monkeypatch.setattr(
        publishing_pipeline_module,
        "issue_deterministic_delivery",
        issue_publishing,
    )
    monkeypatch.setattr(
        flashcard_generator_module,
        "issue_deterministic_delivery",
        issue_flashcards,
    )
    monkeypatch.setattr(
        tts_generator_module,
        "require_podcast_script_ready",
        lambda _script: None,
    )
