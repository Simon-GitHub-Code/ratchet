import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from hypothesis import settings

import acceptance

settings.register_profile(
    "default", derandomize=True, print_blob=True, max_examples=200, deadline=None
)
settings.register_profile("ci", derandomize=True, print_blob=True, max_examples=2000, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))


@pytest.fixture(autouse=True)
def isolated_git_config(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path_factory.mktemp("gitconfig") / "config"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(empty))
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
    monkeypatch.delenv("RATCHET_HOOK_PROBE", raising=False)


@pytest.fixture
def sessions_ended(tmp_path: Path) -> Iterator[None]:
    yield
    for found in sorted(tmp_path.glob("**/ratchet/acceptance/*/*/*/session.json")):
        if acceptance.state(found.parent) in (acceptance.State.LIVE, acceptance.State.STARTING):
            acceptance.end(found.parent)
