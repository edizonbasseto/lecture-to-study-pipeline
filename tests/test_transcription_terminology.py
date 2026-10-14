"""Specialist-terminology tests: the fascia rewrite that the vocabulary
prompt provably does not fix, and the anatomically legitimate "facial"
that must survive it untouched."""

import json
from pathlib import Path

import pytest

from lecture_study.domain.models import TranscriptionSegment
from lecture_study.transcription.terminology import (
    DOMAIN_VOCABULARY_PROMPT,
    correct_specialist_terminology,
)
from lecture_study.transcription import pipeline as pipeline_module
from lecture_study.transcription.pipeline import TranscriptionOrchestrator


def _segment(text: str, start_time: float = 0.0) -> TranscriptionSegment:
    return TranscriptionSegment(
        start_time=start_time, end_time=start_time + 1.0, text=text
    )


class TestFasciaTerminologyCorrection:
    @pytest.mark.parametrize(
        ("transcribed", "expected"),
        [
            (
                "e uma camada facial de profundidade.",
                "e uma camada fascial de profundidade.",
            ),
            (
                "Quem que define o que é plano facial de profundidade?",
                "Quem que define o que é plano fascial de profundidade?",
            ),
            (
                "Qual é a definição de margem facial de profundidade?",
                "Qual é a definição de margem fascial de profundidade?",
            ),
            (
                "remover duas camadas faciais de profundidade.",
                "remover duas camadas fasciais de profundidade.",
            ),
        ],
    )
    def test_rewrites_the_fascia_context(self, transcribed: str, expected: str):
        segments, corrections = correct_specialist_terminology(
            [_segment(transcribed, start_time=113.8)]
        )

        assert segments[0].text == expected
        assert len(corrections) == 1
        assert corrections[0].start_time == 113.8
        assert corrections[0].original_text == transcribed
        assert corrections[0].term == "fascial"

    @pytest.mark.parametrize(
        ("previous_text", "segment_text", "expected"),
        [
            ("uma camada", "facial", "fascial"),
            ("a próxima camada", "facial", "fascial"),
            ("até o meu plano", "facial de profundidade.", "fascial"),
        ],
    )
    def test_rewrites_a_phrase_split_across_two_segments(
        self, previous_text: str, segment_text: str, expected: str
    ):
        """Measured on three real occurrences (10007 at 7378.9 and 7759.4,
        10002 at 670.1): the noun ends one segment and the adjective opens
        the next."""
        segments, corrections = correct_specialist_terminology(
            [
                _segment("o tumor está abaixo da fáscia", start_time=0.0),
                _segment(previous_text, start_time=1.0),
                _segment(segment_text, start_time=2.0),
            ]
        )

        assert expected in segments[2].text
        assert corrections[-1].start_time == 2.0

    @pytest.mark.parametrize(
        "anchorless_text",
        [
            "é que são camadas faciais.",
            "O que é camada facial?",
            "Camadas faciais.",
            "São as camadas faciais",
        ],
    )
    def test_rewrites_an_anchorless_fragment_in_a_fascia_lecture(
        self, anchorless_text: str
    ):
        """Measured on lecture 10007: this speaker is segmented into
        fragments carrying no depth anchor of their own. Left uncorrected
        they would contradict the corrected sentence beside them."""
        segments, _ = correct_specialist_terminology(
            [
                _segment("a camada facial de profundidade do tumor", start_time=0.0),
                _segment(anchorless_text, start_time=10.0),
            ]
        )

        assert "facial" not in segments[1].text
        assert "fascia" in segments[1].text.lower()

    def test_audits_every_term_rewritten_in_one_segment(self):
        _, corrections = correct_specialist_terminology(
            [
                _segment(
                    "o estopatológico mostrou a camada facial de profundidade",
                    start_time=5.0,
                )
            ]
        )

        assert sorted(correction.term for correction in corrections) == [
            "fascial",
            "histopatológico",
        ]

    def test_preserves_an_all_caps_occurrence(self):
        segments, _ = correct_specialist_terminology(
            [_segment("ESTOPATOLÓGICO em caixa alta")]
        )

        assert segments[0].text == "HISTOPATOLÓGICO em caixa alta"

    def test_leaves_the_phrase_untouched_without_a_depth_context(self):
        """ "plano facial" is also a craniometric term and "margem facial"
        a dental one; without a depth context nothing is rewritten."""
        segments, corrections = correct_specialist_terminology(
            [_segment("a margem facial do dente apresenta cálculo")]
        )

        assert segments[0].text == "a margem facial do dente apresenta cálculo"
        assert corrections == []

    def test_rewrites_a_legitimate_phrase_inside_a_fascia_lecture(self):
        """The accepted cost of deciding per lecture (ADR 0028): once a
        lecture discusses fascial depth, a legitimate dental "margem
        facial" in the same lecture is rewritten too. No such lecture
        exists in the corpus, and every rewrite is in the audit record."""
        segments, corrections = correct_specialist_terminology(
            [
                _segment("vamos remover uma camada facial de profundidade do tumor"),
                _segment("a margem facial do dente apresenta cálculo", start_time=2.0),
            ]
        )

        assert segments[1].text == "a margem fascial do dente apresenta cálculo"
        assert corrections[-1].original_text == (
            "a margem facial do dente apresenta cálculo"
        )

    @pytest.mark.parametrize(
        ("transcribed", "expected"),
        [
            ("seguida do estopatológico", "seguida do histopatológico"),
            (
                "não é um diagnóstico estopatológico",
                "não é um diagnóstico histopatológico",
            ),
            ("o laboratório de estopatologia", "o laboratório de histopatologia"),
            ("lá nos estopatológicos", "lá nos histopatológicos"),
        ],
    )
    def test_corrects_histopathology_without_needing_context(
        self, transcribed: str, expected: str
    ):
        """55 occurrences across 16 of the 25 lectures. "estopatológico"
        is not a Portuguese word, so no context is needed."""
        segments, corrections = correct_specialist_terminology([_segment(transcribed)])

        assert segments[0].text == expected
        assert corrections[0].term == "histopatológico"

    @pytest.mark.parametrize(
        "legitimate_text",
        [
            "causando uma paralisia facial, que é o nervo facial",
            "a gente já vê uma deformidade facial",
            "as veias língua facial e a maxilar",
            "prurido facial, ataxia, fraqueza",
            "Aquela dobra facial aqui no nariz",
        ],
    )
    def test_leaves_anatomically_legitimate_facial_untouched(
        self, legitimate_text: str
    ):
        segments, corrections = correct_specialist_terminology(
            [_segment(legitimate_text)]
        )

        assert segments[0].text == legitimate_text
        assert corrections == []

    def test_reports_no_correction_for_an_already_correct_transcript(self):
        segments, corrections = correct_specialist_terminology(
            [_segment("um plano fascial de profundidade")]
        )

        assert segments[0].text == "um plano fascial de profundidade"
        assert corrections == []

    def test_the_vocabulary_prompt_names_the_truncated_terms(self):
        for term in (
            "biópsia excisional",
            "biópsia incisional",
            "histopatológico",
        ):
            assert term in DOMAIN_VOCABULARY_PROMPT

    def test_the_vocabulary_prompt_claims_no_discipline_and_no_fascia_term(self):
        """The prompt is carried into every window of every lecture of
        every discipline, so asserting an oncology context or spelling
        "fascial" would bias the decoder against the many lectures where
        "nervo facial" is the correct reading."""
        for biasing_term in ("oncológica", "fascial", "facial", "fáscia"):
            assert biasing_term not in DOMAIN_VOCABULARY_PROMPT


class TestOrchestratorAppliesTheCorrection:
    """The wiring, not the rule: a transcript written by the orchestrator
    must already carry the corrected terminology and leave the audit
    record beside it."""

    @staticmethod
    def _write_queue(queue_path: Path, lecture_id: str) -> None:
        queue_path.write_text(
            json.dumps(
                [
                    {
                        "id": lecture_id,
                        "title": "Aula",
                        "subject": "Disciplina B",
                        "url": "https://ead.example.org/mod/supervideo/view.php?id=1",
                        "estimated_duration_sec": 0.0,
                    }
                ]
            )
        )

    @staticmethod
    def _write_canonical_handoff(
        canonical_dir: Path, lecture_id: str, audio_path: Path
    ) -> None:
        canonical_dir.mkdir(parents=True, exist_ok=True)
        (canonical_dir / f"{lecture_id}.json").write_text(
            json.dumps(
                {
                    "lecture_id": lecture_id,
                    "audio_path": str(audio_path),
                    "frames": [],
                }
            )
        )

    @pytest.mark.asyncio
    async def test_persisted_transcript_carries_the_corrected_terminology(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.setattr(
            pipeline_module, "CANONICAL_INPUT_DIR", tmp_path / "04_canonical"
        )
        monkeypatch.setattr(
            pipeline_module, "TRANSCRIBED_OUTPUT_DIR", tmp_path / "05_transcribed"
        )
        queue_path = tmp_path / "queue.json"
        self._write_queue(queue_path, "aula-001")
        audio_path = tmp_path / "02_audio" / "aula-001.wav"
        self._write_canonical_handoff(tmp_path / "04_canonical", "aula-001", audio_path)
        transcribed = [
            _segment("seguida do estopatológico", start_time=0.0),
            _segment("e uma camada facial de profundidade.", start_time=1.0),
        ]

        async def _fake_run_transcription(audio: Path, model: Path):
            return transcribed

        monkeypatch.setattr(
            pipeline_module, "run_transcription", _fake_run_transcription
        )

        await TranscriptionOrchestrator(
            queue_path=queue_path, model_path=tmp_path / "model.bin"
        ).run()

        persisted = json.loads(
            (tmp_path / "05_transcribed" / "aula-001_transcription.json").read_text()
        )
        texts = [segment["text"] for segment in persisted["segments"]]
        assert texts == [
            "seguida do histopatológico",
            "e uma camada fascial de profundidade.",
        ]
        audit = json.loads(
            (
                tmp_path / "05_transcribed" / "aula-001_terminology_corrections.json"
            ).read_text()
        )
        assert [record["term"] for record in audit] == [
            "histopatológico",
            "fascial",
        ]


class TestTerminologyCorrectionAudit:
    def test_writes_one_audit_record_per_rewrite(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.setattr(pipeline_module, "TRANSCRIBED_OUTPUT_DIR", tmp_path)
        _, corrections = correct_specialist_terminology(
            [_segment("e uma camada facial de profundidade.", start_time=113.8)]
        )

        TranscriptionOrchestrator._save_terminology_corrections("aula-001", corrections)

        payload = json.loads(
            (tmp_path / "aula-001_terminology_corrections.json").read_text()
        )
        assert payload == [
            {
                "start_time": 113.8,
                "original_text": "e uma camada facial de profundidade.",
                "corrected_text": "e uma camada fascial de profundidade.",
                "term": "fascial",
            }
        ]

    def test_writes_an_empty_report_when_nothing_was_corrected(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.setattr(pipeline_module, "TRANSCRIBED_OUTPUT_DIR", tmp_path)

        TranscriptionOrchestrator._save_terminology_corrections("aula-001", [])

        assert (
            json.loads((tmp_path / "aula-001_terminology_corrections.json").read_text())
            == []
        )
