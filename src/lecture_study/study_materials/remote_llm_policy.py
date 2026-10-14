"""Registry-backed remote egress policy (ADR 0045 section 2, D-14, D-15, D-19).

Two operator-owned sources decide whether a lecture may reach the remote Codex
adapter, and each id has exactly one governing source:

- a reserved local id is decided only by ``remote_llm_allowed`` on its
  ``LocalLecture`` row in the local registry;
- any other id is a portal-catalog id and is allowed only when listed in
  ``remote_llm_allowed_lecture_ids`` of the gateway configuration.

Everything else is refused. The policy contacts nothing and loads the registry
lazily, so a llama-only run never reads it.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from lecture_study.llm.gateway import GatewayConfigurationError
from lecture_study.study_materials.local_lectures import (
    LOCAL_LECTURES_PATH,
    LocalLectureError,
    is_reserved_local_id,
    load_local_lectures,
)


class RegistryRemoteEgressPolicy:
    """Remote egress decision from the local registry and the portal id list."""

    def __init__(
        self,
        *,
        allowed_portal_lecture_ids: Iterable[str],
        local_lectures_path: Path = LOCAL_LECTURES_PATH,
    ) -> None:
        self._allowed_portal_lecture_ids = frozenset(allowed_portal_lecture_ids)
        self._local_lectures_path = local_lectures_path
        self._remote_allowed_local_ids: frozenset[str] | None = None

    def is_remote_allowed(self, lecture_id: str) -> bool:
        if is_reserved_local_id(lecture_id):
            return lecture_id in self._local_remote_allowed_ids()
        return lecture_id in self._allowed_portal_lecture_ids

    def _local_remote_allowed_ids(self) -> frozenset[str]:
        if self._remote_allowed_local_ids is None:
            self._remote_allowed_local_ids = self._load_remote_allowed_local_ids()
        return self._remote_allowed_local_ids

    def _load_remote_allowed_local_ids(self) -> frozenset[str]:
        try:
            lectures = load_local_lectures(self._local_lectures_path)
        except LocalLectureError as error:
            raise GatewayConfigurationError(
                f"the local lecture registry at {self._local_lectures_path} is "
                "unreadable; the codex adapter is refused for local lectures"
            ) from error
        return frozenset(
            lecture.lecture_id for lecture in lectures if lecture.remote_llm_allowed
        )
