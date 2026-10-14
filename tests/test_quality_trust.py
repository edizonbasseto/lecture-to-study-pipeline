from __future__ import annotations

import base64
import secrets
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from lecture_study.quality import trust
from lecture_study.quality.trust import (
    QUALITY_HMAC_KEY_ENV,
    HmacQualityTrust,
    QualityTrustError,
    canonical_model_bytes,
)


class _Payload(BaseModel):
    model_config = ConfigDict(strict=True)

    label: str
    count: int


def _trust() -> HmacQualityTrust:
    return HmacQualityTrust(secrets.token_bytes(32))


def test_sign_and_verify_runtime_payload() -> None:
    trust = _trust()
    payload = b"quality-certificate"
    signature = trust.sign(payload)

    trust.require_valid(
        payload=payload,
        signature=signature,
        key_id=trust.key_id,
    )


@pytest.mark.parametrize("change", ["payload", "signature", "key"])
def test_verification_rejects_changed_identity(change: str) -> None:
    trust = _trust()
    payload = b"quality-certificate"
    signature = trust.sign(payload)
    key_id = trust.key_id

    if change == "payload":
        payload = b"changed-certificate"
    elif change == "signature":
        signature = "0" * 64
    else:
        key_id = _trust().key_id

    with pytest.raises(QualityTrustError):
        trust.require_valid(payload=payload, signature=signature, key_id=key_id)


def test_environment_loader_uses_runtime_only_base64_material() -> None:
    encoded_key = base64.b64encode(secrets.token_bytes(32)).decode("ascii")

    trust = HmacQualityTrust.from_environment({QUALITY_HMAC_KEY_ENV: encoded_key})

    assert len(trust.key_id) == 64


@pytest.mark.parametrize(
    "environment",
    [
        {},
        {QUALITY_HMAC_KEY_ENV: "not canonical Base64"},
        {QUALITY_HMAC_KEY_ENV: base64.b64encode(b"short").decode("ascii")},
    ],
)
def test_environment_loader_rejects_invalid_trust_material(
    environment: dict[str, str],
) -> None:
    with pytest.raises(QualityTrustError):
        HmacQualityTrust.from_environment(environment)


def test_canonical_model_bytes_are_order_independent() -> None:
    first = _Payload(label="content", count=2)
    second = _Payload.model_validate({"count": 2, "label": "content"})

    assert canonical_model_bytes(first) == canonical_model_bytes(second)


def _write_dotenv_file(directory: Path, encoded_key: str) -> None:
    (directory / ".env").write_text(f"{QUALITY_HMAC_KEY_ENV}={encoded_key}\n")


def _expected_key_id(encoded_key: str) -> str:
    return HmacQualityTrust(base64.b64decode(encoded_key)).key_id


def test_dotenv_resolution_never_mutates_the_parent_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The working directory's .env may be untrusted: resolving the key
    from it must not inject that key, or any other variable it declares,
    into the parent process environment. Child-process stripping of the
    key is covered by tests/test_process_environment.py."""
    dotenv_key = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    parent_environment: dict[str, str] = {"PATH": "/usr/bin"}
    monkeypatch.setattr(trust.os, "environ", parent_environment)
    (tmp_path / ".env").write_text(
        f"{QUALITY_HMAC_KEY_ENV}={dotenv_key}\nUNRELATED_VARIABLE=injected\n"
    )
    monkeypatch.chdir(tmp_path)

    resolved = HmacQualityTrust.from_environment()

    assert resolved.key_id == _expected_key_id(dotenv_key)
    assert parent_environment == {"PATH": "/usr/bin"}


def test_exported_value_wins_over_a_dotenv_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exported_key = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    dotenv_key = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    monkeypatch.setattr(trust.os, "environ", {QUALITY_HMAC_KEY_ENV: exported_key})
    _write_dotenv_file(tmp_path, dotenv_key)
    monkeypatch.chdir(tmp_path)

    resolved = HmacQualityTrust.from_environment()

    assert resolved.key_id == _expected_key_id(exported_key)


def test_dotenv_file_supplies_the_key_when_absent_from_the_process_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dotenv_key = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    monkeypatch.setattr(trust.os, "environ", {})
    _write_dotenv_file(tmp_path, dotenv_key)
    monkeypatch.chdir(tmp_path)

    resolved = HmacQualityTrust.from_environment()

    assert len(resolved.key_id) == 64


def test_a_dotenv_file_above_the_working_directory_is_never_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the working directory's own .env is trusted: searching upward
    would let an unrelated parent directory's .env supply the signing key."""
    monkeypatch.setattr(trust.os, "environ", {})
    _write_dotenv_file(tmp_path, base64.b64encode(secrets.token_bytes(32)).decode())
    working_directory = tmp_path / "project"
    working_directory.mkdir()
    monkeypatch.chdir(working_directory)

    with pytest.raises(QualityTrustError):
        HmacQualityTrust.from_environment()


def test_absent_from_both_process_environment_and_dotenv_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(trust.os, "environ", {})
    monkeypatch.chdir(tmp_path)

    with pytest.raises(QualityTrustError, match=QUALITY_HMAC_KEY_ENV):
        HmacQualityTrust.from_environment()


@pytest.mark.parametrize(
    "dotenv_value",
    [
        "not canonical Base64",
        base64.b64encode(b"short").decode("ascii"),
    ],
)
def test_dotenv_sourced_invalid_trust_material_fails_closed(
    dotenv_value: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(trust.os, "environ", {})
    _write_dotenv_file(tmp_path, dotenv_value)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(QualityTrustError):
        HmacQualityTrust.from_environment()


def test_explicit_environ_argument_consults_no_dotenv_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    encoded_key = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    other_dotenv_key = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    _write_dotenv_file(tmp_path, other_dotenv_key)
    monkeypatch.chdir(tmp_path)

    resolved = HmacQualityTrust.from_environment({QUALITY_HMAC_KEY_ENV: encoded_key})

    assert resolved.key_id == _expected_key_id(encoded_key)
