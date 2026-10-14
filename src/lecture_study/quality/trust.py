"""Runtime trust root for quality-certificate authentication."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel

QUALITY_HMAC_KEY_ENV = "LECTURE_STUDY_QUALITY_HMAC_KEY_B64"
_MINIMUM_KEY_BYTES = 32
_SHA256_HEX_LENGTH = 64


class QualityTrustError(RuntimeError):
    """Raised when certificate trust material is absent or invalid."""


@dataclass(frozen=True, slots=True)
class HmacQualityTrust:
    """Authenticates quality artifacts against a runtime-only trust root."""

    _key: bytes

    def __post_init__(self) -> None:
        if len(self._key) < _MINIMUM_KEY_BYTES:
            raise QualityTrustError(
                f"Quality trust material must contain at least {_MINIMUM_KEY_BYTES} bytes."
            )

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> HmacQualityTrust:
        """Resolve the trust key from the process environment, falling
        back to the project's dotenv file.

        Only the `environ is None` branch, which already reads
        `os.environ`, consults dotenv; an explicit `environ` mapping stays
        pure and consults no file. Only the working directory's own `.env`
        is read -- never one found by searching parent directories, which
        could belong to an unrelated project. The file is untrusted, so
        only the trust key is read from it and `os.environ` is never
        mutated. An exported value always wins over the dotenv file. A
        missing file yields no value, leaving the fail-closed error below
        as the outcome.
        """
        if environ is None:
            encoded_key = _process_or_dotenv_key()
        else:
            encoded_key = environ.get(QUALITY_HMAC_KEY_ENV)
        if not encoded_key:
            raise QualityTrustError(
                f"Required quality trust variable {QUALITY_HMAC_KEY_ENV} is not configured."
            )

        try:
            key = base64.b64decode(encoded_key, validate=True)
        except (binascii.Error, ValueError) as error:
            raise QualityTrustError(
                f"{QUALITY_HMAC_KEY_ENV} must contain canonical Base64."
            ) from error
        return cls(key)

    @property
    def key_id(self) -> str:
        return hashlib.sha256(self._key).hexdigest()

    def sign(self, payload: bytes) -> str:
        return hmac.new(self._key, payload, hashlib.sha256).hexdigest()

    def require_valid(
        self,
        *,
        payload: bytes,
        signature: str,
        key_id: str,
    ) -> None:
        if len(signature) != _SHA256_HEX_LENGTH:
            raise QualityTrustError("Quality artifact signature has an invalid length.")
        if not hmac.compare_digest(self.key_id, key_id):
            raise QualityTrustError(
                "Quality artifact was signed by a different trust root."
            )
        if not hmac.compare_digest(self.sign(payload), signature):
            raise QualityTrustError("Quality artifact signature is invalid.")


def _process_or_dotenv_key() -> str | None:
    if QUALITY_HMAC_KEY_ENV in os.environ:
        return os.environ[QUALITY_HMAC_KEY_ENV]
    return dotenv_values(Path.cwd() / ".env").get(QUALITY_HMAC_KEY_ENV)


def canonical_json_bytes(payload: object) -> bytes:
    """Return deterministic JSON bytes for a JSON-compatible value."""

    serialized = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return serialized.encode("utf-8")


def canonical_model_bytes(model: BaseModel) -> bytes:
    """Return deterministic bytes suitable for hashing and authentication."""

    return canonical_json_bytes(model.model_dump(mode="json"))
