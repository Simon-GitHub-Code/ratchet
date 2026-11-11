"""Hypothesis profiles: ``default`` is deterministic; ``ci`` runs more examples.

Select with ``HYPOTHESIS_PROFILE=ci uv run pytest``. Neither has a per-example deadline: a
wall-clock limit fails a deterministic test for the host's load, not for a fault in the code.
"""

import os

import pytest
from hypothesis import settings

settings.register_profile(
    "default", derandomize=True, print_blob=True, max_examples=200, deadline=None
)
settings.register_profile("ci", derandomize=True, print_blob=True, max_examples=2000, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))


@pytest.fixture(autouse=True)
def isolated_git_config(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No test reads the machine's git config, whose ``core.hooksPath`` changes which hooks run."""
    empty = tmp_path_factory.mktemp("gitconfig") / "config"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(empty))
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
    monkeypatch.delenv("RATCHET_HOOK_PROBE", raising=False)
