"""Remote egress policy: fail closed unless an operator-recorded flag allows it."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from lecture_study.llm.egress import (
    RemoteEgressPolicy,
    require_remote_egress_allowed,
)
from lecture_study.llm.gateway import (
    GatewayConfigurationError,
    RemoteEgressRefusedError,
    Stage,
)
from lecture_study.study_materials.remote_llm_policy import RegistryRemoteEgressPolicy

FLAGGED_LOCAL_ID = "925101"
UNFLAGGED_LOCAL_ID = "925102"
UNREGISTERED_LOCAL_ID = "925150"
PORTAL_ID = "10001"
OTHER_PORTAL_ID = "10002"


def _row(lecture_id: str, **extra: object) -> dict[str, object]:
    return {
        "lecture_id": lecture_id,
        "title": "Aula sintetica",
        "discipline": "Disciplina A",
        "section": "Videoaulas locais",
        "scheduled_date": "2025-09-01",
        **extra,
    }


@pytest.fixture
def registry_path(tmp_path: Path) -> Path:
    path = tmp_path / "local.yaml"
    rows = [
        _row(FLAGGED_LOCAL_ID, remote_llm_allowed=True),
        _row(UNFLAGGED_LOCAL_ID),
    ]
    path.write_text(yaml.safe_dump({"lectures": rows}), encoding="utf-8")
    return path


def _policy(
    registry_path: Path, portal_ids: tuple[str, ...] = ()
) -> RegistryRemoteEgressPolicy:
    return RegistryRemoteEgressPolicy(
        allowed_portal_lecture_ids=portal_ids, local_lectures_path=registry_path
    )


def test_policy_satisfies_the_inner_protocol(registry_path: Path) -> None:
    policy: RemoteEgressPolicy = _policy(registry_path)

    assert policy.is_remote_allowed(FLAGGED_LOCAL_ID) is True


def test_flagged_local_lecture_is_allowed(registry_path: Path) -> None:
    assert _policy(registry_path).is_remote_allowed(FLAGGED_LOCAL_ID) is True


def test_unflagged_local_lecture_is_refused(registry_path: Path) -> None:
    assert _policy(registry_path).is_remote_allowed(UNFLAGGED_LOCAL_ID) is False


def test_unregistered_reserved_id_is_refused(registry_path: Path) -> None:
    assert _policy(registry_path).is_remote_allowed(UNREGISTERED_LOCAL_ID) is False


def test_missing_registry_refuses_local_lectures(tmp_path: Path) -> None:
    policy = _policy(tmp_path / "absent.yaml")

    assert policy.is_remote_allowed(FLAGGED_LOCAL_ID) is False


def test_unreadable_registry_raises_instead_of_passing(tmp_path: Path) -> None:
    broken = tmp_path / "broken.yaml"
    broken.write_text("lectures: [unclosed", encoding="utf-8")

    with pytest.raises(GatewayConfigurationError, match="unreadable") as raised:
        _policy(broken).is_remote_allowed(FLAGGED_LOCAL_ID)

    assert raised.value.__cause__ is not None


def test_portal_lecture_is_allowed_only_when_listed(registry_path: Path) -> None:
    policy = _policy(registry_path, portal_ids=(PORTAL_ID,))

    assert policy.is_remote_allowed(PORTAL_ID) is True
    assert policy.is_remote_allowed(OTHER_PORTAL_ID) is False


def test_portal_lecture_never_loads_the_registry(tmp_path: Path) -> None:
    broken = tmp_path / "broken.yaml"
    broken.write_text("lectures: [unclosed", encoding="utf-8")

    policy = _policy(broken, portal_ids=(PORTAL_ID,))

    assert policy.is_remote_allowed(PORTAL_ID) is True
    assert policy.is_remote_allowed(OTHER_PORTAL_ID) is False


def test_construction_never_reads_the_registry(tmp_path: Path) -> None:
    broken = tmp_path / "broken.yaml"
    broken.write_text("lectures: [unclosed", encoding="utf-8")

    _policy(broken)


def test_require_allows_a_cleared_lecture(registry_path: Path) -> None:
    require_remote_egress_allowed(
        _policy(registry_path), lecture_id=FLAGGED_LOCAL_ID, stage=Stage.DRAFTING
    )


def test_require_raises_typed_error_with_lecture_and_stage(
    registry_path: Path,
) -> None:
    with pytest.raises(RemoteEgressRefusedError) as raised:
        require_remote_egress_allowed(
            _policy(registry_path), lecture_id=UNFLAGGED_LOCAL_ID, stage=Stage.CRITIC
        )

    assert raised.value.lecture_id == UNFLAGGED_LOCAL_ID
    assert raised.value.stage is Stage.CRITIC
