"""The hooks-install entry point: the framework's install, run where an agent may, then the probe.

``pre-commit`` here is a stand-in on the path that behaves as the framework's install does: it
refuses while git reports a ``core.hooksPath``, and otherwise writes its marked hooks into the
repository's own hooks directory. The real one is run in ``test_hooks_installed.py``.
"""

import os
from pathlib import Path

import pytest

import hooks_install
import hooks_installed
from tests.repos import (
    answering,
    chaining_hooks,
    git,
    hook_script,
    install_hooks,
    repository,
    write,
)

Out = pytest.CaptureFixture[str]
MARK = f"# {hooks_install.FRAMEWORK_MARK}: https://pre-commit.com"
REFUSE = (
    'if git config core.hooksPath >/dev/null; then echo "Cowardly refusing to install hooks with'
    ' core.hooksPath set." >&2; exit 1; fi'
)
INSTALLS = """
echo "$*" >> "{log}"
{refuse}
hooks="$(git rev-parse --git-common-dir)/hooks"
for name in pre-commit commit-msg; do
  printf '#!/bin/sh\\n{mark}\\n{body}\\n' "$name" > "$hooks/$name"
  chmod +x "$hooks/$name"
done
"""
ANSWERS = '[ -n "$RATCHET_HOOK_PROBE" ] && echo %s > "$RATCHET_HOOK_PROBE"; exit 0'


def framework(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    """Put a ``pre-commit`` with ``body`` first on the path."""
    hook_script(tmp_path / "bin", "pre-commit", body)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")


def global_hooks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, chain: bool) -> Path:
    directory = tmp_path / "global-hooks"
    if chain:
        chaining_hooks(directory)
    else:
        for name in hooks_installed.HOOKS:
            hook_script(directory, name, "exit 0")
    config = tmp_path / "global.config"
    config.write_text(f"[core]\n\thooksPath = {directory}\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    return directory


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "project").mkdir()
    return repository(tmp_path / "project")


@pytest.fixture
def log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A ``pre-commit`` on the path that installs answering hooks, and the log of its calls."""
    calls = tmp_path / "calls"
    body = INSTALLS.format(log=calls, refuse=REFUSE, mark=MARK, body=ANSWERS)
    framework(tmp_path, monkeypatch, body)
    return calls


@pytest.fixture
def chained(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A global hooks path whose hooks run the repository's own."""
    return global_hooks(tmp_path, monkeypatch, chain=True)


@pytest.fixture
def unchained(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A global hooks path whose hooks do not run the repository's own."""
    return global_hooks(tmp_path, monkeypatch, chain=False)


@pytest.fixture
def failing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``pre-commit`` whose install fails."""
    framework(tmp_path, monkeypatch, "echo 'no such config' >&2\nexit 3")


@pytest.fixture
def silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``pre-commit`` that installs hooks which never answer the probe."""
    body = INSTALLS.format(log=tmp_path / "calls", refuse="", mark=MARK, body="exit 0 # %s")
    framework(tmp_path, monkeypatch, body)


def run(root: Path) -> int:
    return hooks_install.main(["hooks_install", "--root", str(root)])


def files(directory: Path) -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8") for path in directory.iterdir()}


def test_hooks_that_answer_already_are_left_alone(root: Path, log: Path, capsys: Out) -> None:
    install_hooks(root)
    assert run(root) == 0
    assert capsys.readouterr().out == "hooks: green\n"
    assert not log.exists()


def test_missing_hooks_are_installed_and_the_probe_is_the_proof(
    root: Path, log: Path, capsys: Out
) -> None:
    assert run(root) == 0
    assert capsys.readouterr().out == "hooks: green\n"
    assert log.read_text(encoding="utf-8") == "install\n"
    assert hooks_installed.check(root) is None


@pytest.mark.usefixtures("log")
def test_under_a_global_hooks_path_that_chains_the_install_touches_the_repository_only(
    root: Path, chained: Path
) -> None:
    before = files(chained)
    config = git(root, "config", "--list", "--show-origin")
    assert run(root) == 0
    assert files(chained) == before
    assert git(root, "config", "--list", "--show-origin") == config
    assert MARK in (root / ".git/hooks/pre-commit").read_text(encoding="utf-8")


@pytest.mark.usefixtures("unchained")
def test_a_global_hook_that_does_not_chain_stays_the_owners(
    root: Path, log: Path, capsys: Out
) -> None:
    assert run(root) == 1
    out = capsys.readouterr().out
    assert "do not run the repository's own" in out
    assert f"  owner: run `git config core.hooksPath {root / '.git/hooks'}`" in out
    assert log.read_text(encoding="utf-8") == "install\n"


def test_a_local_hooks_path_is_the_owners_and_nothing_is_installed(
    root: Path, log: Path, capsys: Out
) -> None:
    git(root, "config", "core.hooksPath", str(root / "elsewhere"))
    assert run(root) == 1
    assert "  owner: run `git config --unset core.hooksPath && pre-commit install`" in (
        capsys.readouterr().out
    )
    assert not log.exists()


@pytest.mark.usefixtures("chained")
def test_a_hook_somebody_else_wrote_is_never_moved(root: Path, log: Path, capsys: Out) -> None:
    theirs = hook_script(root / ".git/hooks", "pre-commit", "exit 0")
    assert run(root) == 1
    out = capsys.readouterr().out
    assert f"{theirs}: a hook the pre-commit framework did not write" in out
    assert "  owner: run `GIT_CONFIG_GLOBAL=/dev/null pre-commit install`" in out
    assert theirs.read_text(encoding="utf-8") == "#!/bin/sh\nexit 0\n"
    assert not log.exists()
    assert not (root / ".git/hooks/commit-msg").exists()


def test_another_mechanism_is_installed_by_the_bindings_command_not_by_this(
    root: Path, log: Path, capsys: Out
) -> None:
    named = "- Commit hooks: `lefthook.yml`, installed by `lefthook install`\n"
    write(root, "docs/process/binding.md", named)
    assert run(root) == 1
    assert "  fix: run `lefthook install`" in capsys.readouterr().out
    assert not log.exists()


@pytest.mark.usefixtures("failing")
def test_an_install_that_fails_is_quoted_and_handed_to_the_owner(root: Path, capsys: Out) -> None:
    assert run(root) == 1
    out = capsys.readouterr().out
    assert "hooks: `pre-commit install` exited 3 (no such config)" in out
    assert "  owner: run `pre-commit install`" in out


@pytest.mark.usefixtures("silent")
def test_installed_hooks_that_stay_silent_do_not_send_an_agent_round_again(
    root: Path, capsys: Out
) -> None:
    assert run(root) == 1
    out = capsys.readouterr().out
    assert "the install ran and the hooks still do not answer" in out
    assert "  fix: see that .pre-commit-config.yaml runs" in out
    assert "hooks_install.py" not in out


@pytest.mark.usefixtures("log")
def test_a_framework_hook_that_no_longer_answers_is_the_frameworks_to_rewrite(root: Path) -> None:
    hook_script(root / ".git/hooks", "pre-commit", f"{MARK}\nexit 0")
    hook_script(root / ".git/hooks", "commit-msg", f"{MARK}\n{answering('commit-msg')}")
    assert hooks_install.foreign(root) == []
    assert run(root) == 0
