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
from lecture_study.study_materials import remote_llm_policy
from lecture_study.study_materials.local_lectures import LocalLecture
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


_REGISTRY_ROW = (
    "- lecture_id: '925101'\n  title: T\n  discipline: D\n  section: S\n"
    "  scheduled_date: 2025-07-01\n"
)


@pytest.mark.parametrize(
    "registry_text",
    [
        "lectures:\n"
        + _REGISTRY_ROW
        + "  remote_llm_allowed: false\n  remote_llm_allowed: true\n",
        "lectures:\n"
        + _REGISTRY_ROW
        + "  remote_llm_allowed: true\n  remote_llm_allowed: false\n",
        "lectures: []\nlectures:\n" + _REGISTRY_ROW + "  remote_llm_allowed: true\n",
    ],
    ids=["false-then-true", "true-then-false", "two-top-level-blocks"],
)
def test_registry_with_repeated_keys_refuses_remote_clearance(
    tmp_path: Path, registry_text: str
) -> None:
    path = tmp_path / "repeated.yaml"
    path.write_text(registry_text, encoding="utf-8")

    with pytest.raises(GatewayConfigurationError, match="unreadable"):
        _policy(path).is_remote_allowed(FLAGGED_LOCAL_ID)

    with pytest.raises(GatewayConfigurationError):
        require_remote_egress_allowed(
            _policy(path), lecture_id=FLAGGED_LOCAL_ID, stage=Stage.DRAFTING
        )


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


def test_open2_id_in_registry_and_portal_list_is_a_configuration_error(
    registry_path: Path,
) -> None:
    policy = _policy(registry_path, portal_ids=(UNFLAGGED_LOCAL_ID, PORTAL_ID))

    with pytest.raises(GatewayConfigurationError) as raised:
        policy.require_consistent_sources()

    assert UNFLAGGED_LOCAL_ID in str(raised.value)
    assert PORTAL_ID not in str(raised.value)
    assert "must not be listed in remote_llm_allowed_lecture_ids" in str(raised.value)


def test_open2_conflict_also_fails_a_local_lookup_never_granting_the_list(
    registry_path: Path,
) -> None:
    policy = _policy(registry_path, portal_ids=(UNFLAGGED_LOCAL_ID,))

    with pytest.raises(GatewayConfigurationError):
        policy.is_remote_allowed(UNFLAGGED_LOCAL_ID)
    with pytest.raises(GatewayConfigurationError):
        policy.is_remote_allowed(FLAGGED_LOCAL_ID)


def test_open2_conflict_names_every_conflicting_id_in_order(
    registry_path: Path,
) -> None:
    policy = _policy(registry_path, portal_ids=(UNFLAGGED_LOCAL_ID, FLAGGED_LOCAL_ID))

    with pytest.raises(GatewayConfigurationError) as raised:
        policy.require_consistent_sources()

    assert f"{FLAGGED_LOCAL_ID}, {UNFLAGGED_LOCAL_ID}" in str(raised.value)


def test_disjoint_sources_are_consistent(registry_path: Path) -> None:
    _policy(registry_path, portal_ids=(PORTAL_ID,)).require_consistent_sources()


def test_consistency_check_on_a_missing_registry_passes(tmp_path: Path) -> None:
    _policy(
        tmp_path / "absent.yaml", portal_ids=(PORTAL_ID,)
    ).require_consistent_sources()


@pytest.mark.parametrize("flags", [(True, False), (False, True), (True, True)])
def test_duplicate_registry_rows_never_allow_remote_egress(
    tmp_path: Path, flags: tuple[bool, bool]
) -> None:
    path = tmp_path / "duplicated.yaml"
    rows = [_row(FLAGGED_LOCAL_ID, remote_llm_allowed=flag) for flag in flags]
    path.write_text(yaml.safe_dump({"lectures": rows}), encoding="utf-8")

    with pytest.raises(GatewayConfigurationError, match="unreadable"):
        _policy(path).is_remote_allowed(FLAGGED_LOCAL_ID)


@pytest.mark.parametrize("flags", [(True, False), (False, True), (True, True)])
def test_policy_refuses_duplicate_rows_even_if_the_loader_lets_them_through(
    registry_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flags: tuple[bool, bool],
) -> None:
    rows = [
        LocalLecture.model_validate(
            _row(FLAGGED_LOCAL_ID, remote_llm_allowed=flag), strict=False
        )
        for flag in flags
    ]
    monkeypatch.setattr(remote_llm_policy, "load_local_lectures", lambda path: rows)

    with pytest.raises(GatewayConfigurationError, match=FLAGGED_LOCAL_ID):
        _policy(registry_path).is_remote_allowed(FLAGGED_LOCAL_ID)
