"""Site image (site hardening): the container serves the site as an
unprivileged user, so a compromise of the app is not root in the container."""

from pathlib import Path

_DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile.site"
_ROOT_USERS = {"root", "0", "0:0", "root:root"}


def _instructions() -> list[tuple[str, str]]:
    lines = _DOCKERFILE.read_text(encoding="utf-8").splitlines()
    statements = (line.strip() for line in lines)
    return [
        (keyword.upper(), argument.strip())
        for keyword, _, argument in (
            statement.partition(" ")
            for statement in statements
            if statement and not statement.startswith("#")
        )
    ]


def test_the_image_runs_as_a_non_root_user() -> None:
    users = [argument for keyword, argument in _instructions() if keyword == "USER"]

    assert users
    assert users[-1] not in _ROOT_USERS


def test_the_user_is_switched_before_the_entrypoint() -> None:
    keywords = [keyword for keyword, _ in _instructions()]

    assert keywords.index("USER") < keywords.index("ENTRYPOINT")
