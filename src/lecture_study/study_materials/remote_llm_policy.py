"""Registry-backed remote egress policy (ADR 0045 section 2, D-14, D-15, D-19).

Two operator-owned sources decide whether a lecture may reach the remote Codex
adapter, and each id has exactly one governing source:

- a reserved local id is decided only by ``remote_llm_allowed`` on its
  ``LocalLecture`` row in the local registry;
- any other id is a portal-catalog id and is allowed only when listed in
  ``remote_llm_allowed_lecture_ids`` of the gateway configuration.

An id present in both sources is a configuration error (OPEN-2, option A).

Everything else is refused. The policy contacts nothing and loads the registry
lazily, so a llama-only run never reads it.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from lecture_study.llm.gateway import GatewayConfigurationError
from lecture_study.study_materials.local_lectures import (
    LOCAL_LECTURES_PATH,
    LocalLecture,
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
        self._local_lectures: tuple[LocalLecture, ...] | None = None

    def is_remote_allowed(self, lecture_id: str) -> bool:
        if is_reserved_local_id(lecture_id):
            return lecture_id in self._remote_allowed_local_ids()
        return lecture_id in self._allowed_portal_lecture_ids

    def require_consistent_sources(self) -> None:
        """Raise when an id is governed by both the registry and the portal list.

        OPEN-2, operator answer option A: a lecture id present in the local
        registry and in ``remote_llm_allowed_lecture_ids`` is a configuration
        error, never a warning and never "registry wins".
        """

        conflicting_ids = sorted(
            {lecture.lecture_id for lecture in self._registered_lectures()}
            & self._allowed_portal_lecture_ids
        )
        if conflicting_ids:
            raise GatewayConfigurationError(
                "lecture ids listed in remote_llm_allowed_lecture_ids are also "
                f"registered as local lectures: {', '.join(conflicting_ids)}; "
                "local lectures carry remote_llm_allowed in the local registry "
                "and must not be listed in remote_llm_allowed_lecture_ids"
            )

    def _remote_allowed_local_ids(self) -> frozenset[str]:
        self.require_consistent_sources()
        flags_by_id: dict[str, list[bool]] = {}
        for lecture in self._registered_lectures():
            flags_by_id.setdefault(lecture.lecture_id, []).append(
                lecture.remote_llm_allowed
            )
        duplicated = sorted(i for i, flags in flags_by_id.items() if len(flags) > 1)
        if duplicated:
            raise GatewayConfigurationError(
                "the local lecture registry lists ids more than once: "
                + ", ".join(duplicated)
                + "; remote clearance is refused until each id has one row"
            )
        return frozenset(i for i, flags in flags_by_id.items() if all(flags))

    def _registered_lectures(self) -> tuple[LocalLecture, ...]:
        if self._local_lectures is None:
            self._local_lectures = self._load_registered_lectures()
        return self._local_lectures

    def _load_registered_lectures(self) -> tuple[LocalLecture, ...]:
        try:
            return tuple(load_local_lectures(self._local_lectures_path))
        except LocalLectureError as error:
            raise GatewayConfigurationError(
                f"the local lecture registry at {self._local_lectures_path} is "
                "unreadable; the codex adapter is refused for local lectures"
            ) from error
