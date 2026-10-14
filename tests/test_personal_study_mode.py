"""ADR 0024 uncertified personal-study execution mode tests.

Covers `run_personal_study`'s phase composition (8, 9, 13, 14, 15 — the
13/14 legs now invoke the Codex generation scripts per ADR 0038) and the
guarantee that the default `run_phase`/`run_all` invocation path keeps
its fail-closed publication gate. The source-input/receipt and
generator-driven coverage was removed with the local llama.cpp route
under ADR 0038.
"""

import pytest

from lecture_study.cli import orchestrator as orchestrator_module
from lecture_study.cli.orchestrator import (
    OrchestratorContext,
    PhaseSpec,
    run_all,
    run_personal_study,
    run_phase,
)
from lecture_study.domain.errors import PipelineExecutionError
from lecture_study.quality import QualityCertificationError


class TestRunPersonalStudy:
    @pytest.mark.asyncio
    async def test_runs_exactly_phases_8_9_13_14_15_in_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        observed: list[int] = []

        def make_spec(number: int) -> PhaseSpec:
            async def run(ctx: OrchestratorContext, lecture_id: str) -> None:
                observed.append(number)

            return PhaseSpec(
                number=number,
                name=f"phase-{number}",
                supports_single_lecture=True,
                artifact_exists=lambda _ctx, _lid: False,
                run=run,
            )

        fake_registry = tuple(make_spec(n) for n in (2, 8, 9, 10, 11, 12, 13, 14, 15))
        monkeypatch.setattr(orchestrator_module, "PHASE_REGISTRY", fake_registry)

        def reject_call(_lecture_id: str) -> None:
            raise AssertionError("require_publication_ready must not be called")

        monkeypatch.setattr(
            orchestrator_module, "require_publication_ready", reject_call
        )

        results = await run_personal_study(OrchestratorContext(), "aula-001")

        assert observed == [8, 9, 13, 14, 15]
        assert [result.phase_number for result in results] == [8, 9, 13, 14, 15]
        assert all(result.status == "ran" for result in results)


class TestDefaultInvocationPathUnchanged:
    """`run_phase`/`run_all` must still hit the real, unpatched
    `require_publication_ready` fail-closed gate for phases numbered above
    10 -- restricting PHASE_REGISTRY to just phase 13 isolates that gate
    from the earlier phases' own unrelated resume/queue requirements."""

    @staticmethod
    def _unreachable_phase_13_spec() -> PhaseSpec:
        async def unreachable_run(_ctx: OrchestratorContext, _lecture_id: str) -> None:
            raise AssertionError("spec.run must not be reached")

        return PhaseSpec(13, "exams", True, lambda _c, _l: False, unreachable_run)

    @pytest.mark.asyncio
    async def test_run_phase_hints_at_run_personal_study_for_phase_13(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """2026-09-15: phase 13 is reachable through `run-personal-study`
        (ADR 0024), so `run_phase`'s own certificate-gate failure for it is
        wrapped with a hint pointing at that command instead of surfacing
        the raw `QualityCertificationError` -- found while diagnosing a
        session's own confusion over an identical failure for phase 15.
        The gate itself, and its fail-closed default, are unchanged: the
        original error is still the wrapped cause."""
        monkeypatch.setattr(
            orchestrator_module,
            "PHASE_REGISTRY",
            (self._unreachable_phase_13_spec(),),
        )
        ctx = OrchestratorContext()

        with pytest.raises(
            PipelineExecutionError, match="run-personal-study"
        ) as excinfo:
            await run_phase(ctx, 13, "aula-does-not-exist-anywhere")

        assert isinstance(excinfo.value.__cause__, QualityCertificationError)

    @pytest.mark.asyncio
    async def test_run_phase_raises_the_original_error_for_a_non_personal_study_phase(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Phase 11 has no uncertified route at all, so the hint must not
        fire for it -- the original fail-closed behavior is preserved."""

        async def unreachable_run(_ctx: OrchestratorContext, _lecture_id: str) -> None:
            raise AssertionError("spec.run must not be reached")

        monkeypatch.setattr(
            orchestrator_module,
            "PHASE_REGISTRY",
            (PhaseSpec(11, "publish", True, lambda _c, _l: False, unreachable_run),),
        )
        ctx = OrchestratorContext()

        with pytest.raises(QualityCertificationError):
            await run_phase(ctx, 11, "aula-does-not-exist-anywhere")

    @pytest.mark.asyncio
    async def test_run_all_still_raises_for_the_same_lecture(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            orchestrator_module,
            "PHASE_REGISTRY",
            (self._unreachable_phase_13_spec(),),
        )
        ctx = OrchestratorContext()

        with pytest.raises(QualityCertificationError):
            await run_all(ctx, "aula-does-not-exist-anywhere")


class TestRunPhase15NoModeBranchNeeded:
    """`_run_phase_15` needs no mode branch (Task 2's behavior block):
    the uncertified marker already travels inside the script this stage
    loads from disk, so PodcastAudioGenerator.generate_podcast is called
    the same way for every route. Since ADR 0038 the orchestrator has no
    execution-mode field at all; this pins phase 15 against ever growing
    one."""

    @pytest.mark.asyncio
    async def test_run_phase_15_calls_the_audio_generator_directly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []

        class FakePodcastAudioGenerator:
            def generate_podcast(self, lecture_id: str) -> str:
                calls.append(lecture_id)
                return "workspace/data/16_podcast_audio/aula-001/podcast_final.m4a"

        monkeypatch.setattr(
            orchestrator_module, "PodcastAudioGenerator", FakePodcastAudioGenerator
        )

        await orchestrator_module._run_phase_15(OrchestratorContext(), "aula-001")

        assert calls == ["aula-001"]
