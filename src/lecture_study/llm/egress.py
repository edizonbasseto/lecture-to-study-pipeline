"""Remote egress policy port (ADR 0045 section 2, D-14, D-15).

``llm/`` only defines the question "may this lecture reach a remote adapter?".
The answer comes from an implementation that knows the academic registry, which
lives outside this package so the dependency rule holds (D-02). Anything the
policy does not explicitly allow is refused.
"""

from __future__ import annotations

from typing import Protocol

from lecture_study.llm.gateway import RemoteEgressRefusedError, Stage


class RemoteEgressPolicy(Protocol):
    """Decides whether a lecture may be sent to a remote adapter."""

    def is_remote_allowed(self, lecture_id: str) -> bool: ...


def require_remote_egress_allowed(
    policy: RemoteEgressPolicy, *, lecture_id: str, stage: Stage
) -> None:
    """Raise ``RemoteEgressRefusedError`` unless the policy allows the lecture."""

    if not policy.is_remote_allowed(lecture_id):
        raise RemoteEgressRefusedError(lecture_id=lecture_id, stage=stage)
