"""Stage metrics read from the provenance run records (ADR 0047, DEMO-03).

A run record holds counters only. Every model request of a generation script
goes through one ``complete_json`` call, which sends one first attempt and at
most one retry, and never retries a gateway error. Per gateway entry, ``calls
- retry_calls`` is therefore the number of requests and ``retry_calls`` the
number whose first attempt failed the schema or contract check.

The critic script runs two gateways under one record: each review is one
critic-stage request and each repair redraft one drafting-stage request.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lecture_study.llm.gateway import AdapterName, Stage
from lecture_study.llm.run_record import GatewayUsage, LlmRunRecord, RunStatus

__all__ = [
    "CriticMetrics",
    "RunMetricsError",
    "StageMetrics",
    "critic_metrics",
    "parse_run_record",
    "stage_metrics",
]


class RunMetricsError(RuntimeError):
    """A run record cannot be turned into metrics."""


class StageMetrics(BaseModel):
    """Requests, first-attempt failures and timings of one LLM stage run."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    script_stage: Stage
    adapter_name: AdapterName
    model_identifier: str
    engine_version: str | None
    record_status: RunStatus
    superseded: bool
    requests: int = Field(ge=0)
    retries: int = Field(ge=0)
    wall_seconds: float = Field(ge=0)
    call_seconds: float = Field(ge=0)

    def first_attempt_passes(self) -> int:
        return self.requests - self.retries


class CriticMetrics(BaseModel):
    """Review outcomes of the critic stage, counted over reviews."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    record_status: RunStatus
    reviews: int = Field(ge=0)
    redrafts: int = Field(ge=0)
    units: int = Field(ge=0)
    final_rejected_units: int = Field(ge=0)
    rejected_reviews: int = Field(ge=0)


def parse_run_record(text: str) -> LlmRunRecord:
    """Validate a run record; failures name field locations only."""
    try:
        return LlmRunRecord.model_validate_json(text, strict=False)
    except ValidationError as error:
        locations = ", ".join(
            ".".join(str(part) for part in problem["loc"]) for problem in error.errors()
        )
        raise RunMetricsError(f"run record is malformed at: {locations}") from None


def stage_metrics(record: LlmRunRecord, *, superseded: bool = False) -> StageMetrics:
    """Metrics of the gateway entry that belongs to the record's own stage."""
    usage = _required_usage(record, record.script_stage)
    requests = _requests(usage)
    if usage.retry_calls > requests:
        raise RunMetricsError(
            f"the {record.script_stage.value} record counts more retries than requests"
        )
    return StageMetrics(
        script_stage=record.script_stage,
        adapter_name=usage.adapter_name,
        model_identifier=usage.model_identifier,
        engine_version=usage.engine_version,
        record_status=record.status,
        superseded=superseded,
        requests=requests,
        retries=usage.retry_calls,
        wall_seconds=record.wall_seconds,
        call_seconds=usage.call_seconds,
    )


def critic_metrics(
    record: LlmRunRecord, unit_review_passed: Sequence[bool]
) -> CriticMetrics:
    """Rejections are redrafts plus units whose last review still rejected."""
    if record.script_stage is not Stage.CRITIC:
        raise RunMetricsError("critic metrics need the record of the critic stage")
    reviews = _requests(_required_usage(record, Stage.CRITIC))
    drafting_usage = _optional_usage(record, Stage.DRAFTING)
    redrafts = 0 if drafting_usage is None else _requests(drafting_usage)
    final_rejected_units = sum(not passed for passed in unit_review_passed)
    rejected_reviews = redrafts + final_rejected_units
    if rejected_reviews > reviews:
        raise RunMetricsError("the critic record counts more rejections than reviews")
    return CriticMetrics(
        record_status=record.status,
        reviews=reviews,
        redrafts=redrafts,
        units=len(unit_review_passed),
        final_rejected_units=final_rejected_units,
        rejected_reviews=rejected_reviews,
    )


def _requests(usage: GatewayUsage) -> int:
    requests = usage.calls - usage.retry_calls
    if requests < 0:
        raise RunMetricsError(
            f"the {usage.stage.value} gateway entry counts more retry calls than calls"
        )
    return requests


def _required_usage(record: LlmRunRecord, stage: Stage) -> GatewayUsage:
    usage = _optional_usage(record, stage)
    if usage is None:
        raise RunMetricsError(f"the record has no gateway entry for {stage.value}")
    return usage


def _optional_usage(record: LlmRunRecord, stage: Stage) -> GatewayUsage | None:
    entries = [usage for usage in record.gateways if usage.stage is stage]
    if len(entries) > 1:
        raise RunMetricsError(
            f"the record has several gateway entries for {stage.value}"
        )
    return entries[0] if entries else None
