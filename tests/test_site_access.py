"""Site access-control tests (SITE-04): a person is authorized only by an
exact email-and-password match against the allowlist file, and an absent
or unreadable file denies everyone rather than admitting them."""

from pathlib import Path

import pytest

from lecture_study.site.access import (
    CredentialsError,
    hash_password,
    read_credentials,
    verify_credentials,
    verify_password,
)


def _write_credentials(path: Path, entries: dict[str, str]) -> None:
    lines = [
        f"{email}:{hash_password(password)}" for email, password in entries.items()
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestPasswordHashing:
    def test_the_correct_password_verifies(self):
        password_hash = hash_password("segredo-forte")

        assert verify_password("segredo-forte", password_hash) is True

    def test_a_wrong_password_does_not_verify(self):
        password_hash = hash_password("segredo-forte")

        assert verify_password("outra-coisa", password_hash) is False

    def test_two_hashes_of_the_same_password_differ(self):
        """A random salt per call, so the file never reveals that two
        people share a password."""
        assert hash_password("mesma-senha") != hash_password("mesma-senha")

    def test_a_malformed_hash_fails_closed(self):
        assert verify_password("qualquer", "isto-nao-e-um-hash") is False


class TestReadCredentials:
    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(CredentialsError):
            read_credentials(tmp_path / "nao-existe.txt")

    def test_reads_email_and_hash_pairs(self, tmp_path: Path):
        path = tmp_path / "credentials.txt"
        _write_credentials(path, {"aluna@example.com": "senha123"})

        credentials = read_credentials(path)

        assert len(credentials) == 1
        assert credentials[0].email == "aluna@example.com"

    def test_comments_and_blank_lines_are_ignored(self, tmp_path: Path):
        path = tmp_path / "credentials.txt"
        path.write_text("# comentário\n\nfer@example.com:" + hash_password("x") + "\n")

        assert len(read_credentials(path)) == 1

    def test_email_is_normalized_to_lowercase(self, tmp_path: Path):
        path = tmp_path / "credentials.txt"
        path.write_text("Aluna@Example.com:" + hash_password("x") + "\n")

        assert read_credentials(path)[0].email == "aluna@example.com"


class TestAuthorize:
    def test_correct_email_and_password_authorizes(self, tmp_path: Path):
        path = tmp_path / "credentials.txt"
        _write_credentials(path, {"aluna@example.com": "senha123"})

        authorized = verify_credentials(
            "aluna@example.com", "senha123", read_credentials(path)
        )

        assert authorized is not None
        assert authorized.email == "aluna@example.com"

    def test_correct_password_wrong_email_denies(self, tmp_path: Path):
        path = tmp_path / "credentials.txt"
        _write_credentials(path, {"aluna@example.com": "senha123"})

        assert (
            verify_credentials("outra@example.com", "senha123", read_credentials(path))
            is None
        )

    def test_correct_email_wrong_password_denies(self, tmp_path: Path):
        path = tmp_path / "credentials.txt"
        _write_credentials(path, {"aluna@example.com": "senha123"})

        assert (
            verify_credentials("aluna@example.com", "errada", read_credentials(path))
            is None
        )

    def test_email_match_is_case_insensitive(self, tmp_path: Path):
        path = tmp_path / "credentials.txt"
        _write_credentials(path, {"aluna@example.com": "senha123"})

        authorized = verify_credentials(
            "Aluna@Example.com", "senha123", read_credentials(path)
        )

        assert authorized is not None

    def test_removing_a_line_revokes_access_on_the_next_check(self, tmp_path: Path):
        """No redeploy, no restart: the file is read on every check."""
        path = tmp_path / "credentials.txt"
        _write_credentials(path, {"aluna@example.com": "senha123"})
        assert (
            verify_credentials("aluna@example.com", "senha123", read_credentials(path))
            is not None
        )

        path.write_text("")

        assert (
            verify_credentials("aluna@example.com", "senha123", read_credentials(path))
            is None
        )

    def test_missing_file_raises_rather_than_denying_silently(self, tmp_path: Path):
        with pytest.raises(CredentialsError):
            read_credentials(tmp_path / "nao-existe.txt")
