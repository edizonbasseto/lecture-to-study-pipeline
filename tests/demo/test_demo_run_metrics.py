"""Stage metrics and critic rejection counts from provenance run records."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from lecture_study.demo.run_metrics import (
    RunMetricsError,
    critic_metrics,
    parse_run_record,
    stage_metrics,
)
from lecture_study.llm.gateway import (
    AdapterName,
    GatewayIdentity,
    LlmRequest,
    LlmResponse,
    Stage,
)
from lecture_study.llm.run_record import (
    GatewayUsage,
    LlmRunRecord,
    RunStatus,
    record_llm_run,
    run_record_path,
)

MOMENT = datetime(2026, 10, 15, 19, 0, 0, tzinfo=UTC)


def usage(stage: Stage, *, calls: int, retry_calls: int = 0) -> GatewayUsage:
    return GatewayUsage(
        stage=stage,
        adapter_name=AdapterName.LLAMA_CPP,
        model_identifier="qwen-test",
        model_sha256=None,
        engine_version="engine-1",
        calls=calls,
        retry_calls=retry_calls,
        call_seconds=4.5,
    )


def record(
    script_stage: Stage,
    *gateways: GatewayUsage,
    status: RunStatus = "succeeded",
) -> LlmRunRecord:
    return LlmRunRecord(
        lecture_id="demo-local",
        script_stage=script_stage,
        schema_name="Schema",
        status=status,
        started_at=MOMENT,
        finished_at=MOMENT,
        wall_seconds=12.0,
        gateways=gateways,
    )


def test_stage_metrics_count_requests_and_retries() -> None:
    exams = record(Stage.EXAMS, usage(Stage.EXAMS, calls=3, retry_calls=1))

    metrics = stage_metrics(exams)

    assert (metrics.requests, metrics.retries) == (2, 1)
    assert metrics.first_attempt_passes() == 1
    assert metrics.record_status == "succeeded"
    assert metrics.superseded is False
    assert metrics.wall_seconds == 12.0
    assert metrics.call_seconds == 4.5
    assert metrics.engine_version == "engine-1"


def test_stage_metrics_keep_a_failed_record_status() -> None:
    failed = record(Stage.PODCAST, usage(Stage.PODCAST, calls=1), status="failed")

    assert stage_metrics(failed).record_status == "failed"


def test_stage_metrics_mark_a_superseded_record() -> None:
    semantic = record(Stage.SEMANTIC, usage(Stage.SEMANTIC, calls=1))

    assert stage_metrics(semantic, superseded=True).superseded is True


def test_stage_metrics_need_the_entry_of_the_records_own_stage() -> None:
    mismatched = record(Stage.EXAMS, usage(Stage.PODCAST, calls=1))

    with pytest.raises(RunMetricsError, match="exams"):
        stage_metrics(mismatched)


def test_stage_metrics_refuse_more_retry_calls_than_calls() -> None:
    impossible = record(Stage.EXAMS, usage(Stage.EXAMS, calls=1, retry_calls=2))

    with pytest.raises(RunMetricsError):
        stage_metrics(impossible)


def test_stage_metrics_refuse_more_retries_than_requests() -> None:
    impossible = record(Stage.EXAMS, usage(Stage.EXAMS, calls=2, retry_calls=2))

    with pytest.raises(RunMetricsError, match="more retries than requests"):
        stage_metrics(impossible)


def test_critic_metrics_count_reviews_redrafts_and_rejections() -> None:
    critic = record(
        Stage.CRITIC,
        usage(Stage.CRITIC, calls=2),
        usage(Stage.DRAFTING, calls=1),
    )

    metrics = critic_metrics(critic, [True])

    assert metrics.reviews == 2
    assert metrics.redrafts == 1
    assert metrics.units == 1
    assert metrics.final_rejected_units == 0
    assert metrics.rejected_reviews == 1


def test_critic_metrics_without_drafting_entry_have_no_redrafts() -> None:
    critic = record(Stage.CRITIC, usage(Stage.CRITIC, calls=1))

    metrics = critic_metrics(critic, [True])

    assert (metrics.reviews, metrics.redrafts, metrics.rejected_reviews) == (1, 0, 0)


def test_critic_metrics_count_a_finally_rejected_unit() -> None:
    critic = record(
        Stage.CRITIC,
        usage(Stage.CRITIC, calls=3),
        usage(Stage.DRAFTING, calls=2),
    )

    metrics = critic_metrics(critic, [False])

    assert metrics.final_rejected_units == 1
    assert metrics.rejected_reviews == 3


def test_critic_metrics_ignore_retries_of_a_review_request() -> None:
    critic = record(Stage.CRITIC, usage(Stage.CRITIC, calls=2, retry_calls=1))

    assert critic_metrics(critic, [True]).reviews == 1


def test_critic_metrics_refuse_other_stage_records() -> None:
    with pytest.raises(RunMetricsError, match="critic stage"):
        critic_metrics(record(Stage.EXAMS, usage(Stage.EXAMS, calls=1)), [True])


def test_critic_metrics_refuse_more_rejections_than_reviews() -> None:
    critic = record(Stage.CRITIC, usage(Stage.CRITIC, calls=1))

    with pytest.raises(RunMetricsError, match="more rejections"):
        critic_metrics(critic, [False, False])


def test_critic_metrics_keep_a_failed_record_status() -> None:
    failed = record(Stage.CRITIC, usage(Stage.CRITIC, calls=1), status="failed")

    assert critic_metrics(failed, []).record_status == "failed"


def test_parse_run_record_rejects_an_unknown_field_naming_its_location() -> None:
    text = record(Stage.EXAMS, usage(Stage.EXAMS, calls=1)).model_dump_json()
    tampered = text.replace('"schema_name"', '"surprise":"x","schema_name"')

    with pytest.raises(RunMetricsError, match="surprise") as raised:
        parse_run_record(tampered)

    assert "qwen-test" not in str(raised.value)


def test_parse_run_record_accepts_a_record_written_by_record_llm_run(
    tmp_path: Path,
) -> None:
    gateway = _StubGateway()
    with record_llm_run(
        lecture_id="demo-local",
        script_stage=Stage.EXAMS,
        schema_name="MockExam",
        root=tmp_path,
    ) as recorder:
        tracked = recorder.track(gateway, stage=Stage.EXAMS)
        tracked.complete(LlmRequest(Stage.EXAMS, "prompt"))
        tracked.complete(LlmRequest(Stage.EXAMS, "prompt", is_retry=True))

    written = run_record_path(tmp_path, "demo-local", Stage.EXAMS).read_text(
        encoding="utf-8"
    )
    metrics = stage_metrics(parse_run_record(written))

    assert (metrics.requests, metrics.retries) == (1, 1)
    assert metrics.model_identifier == "stub-model"


class _StubGateway:
    def complete(self, request: LlmRequest) -> LlmResponse:
        return LlmResponse(b"{}")

    def describe(self) -> GatewayIdentity:
        return GatewayIdentity(
            adapter_name=AdapterName.LLAMA_CPP,
            model_identifier="stub-model",
            engine_version="stub-engine",
        )
