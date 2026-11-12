"""Would a commit run tier 0? Each case is a real repository, none on the machine's git config."""

import os
import shutil
import subprocess
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
TEMPLATE_JUSTFILE = hooks_installed.TEMPLATE
INSTALL = "pre-commit install"
SCRIPT = hooks_installed.PLUGIN_ROOT / "scripts/hooks_install.py"
ANSWERING_JUSTFILE = "pre-commit:\n    @echo $RATCHET_HOOK_PROBE\n"


def project(tmp_path: Path) -> Path:
    (tmp_path / "project").mkdir()
    return repository(tmp_path / "project")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return project(tmp_path)


def config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    text: str,
    *,
    variable: str = "GIT_CONFIG_GLOBAL",
) -> None:
    path = tmp_path / f"{variable}.config"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setenv(variable, str(path))


def hooks_path(directory: Path) -> str:
    return f"[core]\n\thooksPath = {directory}\n"


def problem(root: Path) -> hooks_installed.Problem:
    found = hooks_installed.check(root)
    assert found is not None
    return found


def on_the_path(monkeypatch: pytest.MonkeyPatch, *, found: bool) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/pre-commit" if found else None)


def test_hooks_that_answer_are_green(root: Path, capsys: Out) -> None:
    install_hooks(root)
    assert hooks_installed.check(root) is None
    assert hooks_installed.main(["hooks_installed", "--root", str(root)]) == 0
    assert capsys.readouterr().out == "hooks: green\n"


def test_no_hooks_is_red_and_names_the_entry_point_an_agent_runs(root: Path, capsys: Out) -> None:
    found = problem(root)
    assert found.fix == f'run `python3 "{SCRIPT}"`'
    assert not found.owner
    assert found.produce == found.fix
    assert "not installed" in found.detail
    assert hooks_installed.main(["hooks_installed", "--root", str(root)]) == 1
    out = capsys.readouterr().out
    assert f"  fix: {found.fix}\n" in out
    assert "owner" not in out
    assert out.endswith("hooks: red\n")


def test_the_binding_names_the_entry_point_when_it_has_one(root: Path) -> None:
    write(root, "docs/process/binding.md", "| hooks-install | `just hooks-install` |\n")
    assert problem(root).fix == "run `just hooks-install`"


def test_another_hook_mechanism_is_probed_alike_and_a_red_names_its_install(root: Path) -> None:
    assert hooks_installed.mechanism(root) == (".pre-commit-config.yaml", None)
    binding = "docs/process/binding.md"
    write(root, binding, "- Commit hooks: `lefthook.yml`, installed by `lefthook install`\n")
    assert hooks_installed.mechanism(root) == ("lefthook.yml", "lefthook install")
    assert problem(root).fix == "run `lefthook install`"
    assert not problem(root).owner
    write(root, binding, "- Commit hooks: `hooks` (plain scripts)\n")
    assert problem(root).fix == "run `<the command that installs the hooks of hooks>`"
    assert problem(root).owner
    install_hooks(root)
    assert hooks_installed.check(root) is None


def test_without_pre_commit_on_the_path_the_install_runs_through_uvx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    on_the_path(monkeypatch, found=False)
    assert hooks_installed.installer() == ["uvx", "pre-commit", "install"]
    assert (
        hooks_installed.install_command(["global"]) == f"GIT_CONFIG_GLOBAL=/dev/null uvx {INSTALL}"
    )
    on_the_path(monkeypatch, found=True)
    assert hooks_installed.install_command([]) == INSTALL


def test_one_hook_alone_is_red(root: Path) -> None:
    hook_script(root / ".git/hooks", "pre-commit", answering("pre-commit"))
    assert hooks_installed.check(root) is not None


def test_a_hook_that_answers_as_the_other_one_is_red(root: Path) -> None:
    hook_script(root / ".git/hooks", "pre-commit", answering("commit-msg"))
    hook_script(root / ".git/hooks", "commit-msg", answering("commit-msg"))
    assert hooks_installed.check(root) is not None


def test_a_local_hooks_path_elsewhere_is_the_owners_to_unset(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    git(root, "config", "core.hooksPath", str(elsewhere))
    on_the_path(monkeypatch, found=True)
    found = problem(root)
    assert found.fix == f"run `git config --unset core.hooksPath && {INSTALL}`"
    assert found.owner
    assert found.produce == f"the owner: {found.fix}"
    assert "set in the local git config" in found.detail


def test_a_local_hooks_path_beside_installed_hooks_needs_only_the_unset(
    root: Path, tmp_path: Path
) -> None:
    install_hooks(root)
    git(root, "config", "core.hooksPath", str(tmp_path))
    assert problem(root).fix == "run `git config --unset core.hooksPath`"
    assert problem(root).owner


def test_a_worktree_hooks_path_is_unset_in_its_own_scope(root: Path, tmp_path: Path) -> None:
    git(root, "config", "extensions.worktreeConfig", "true")
    git(root, "config", "--worktree", "core.hooksPath", str(tmp_path))
    assert "`git config --worktree --unset core.hooksPath && " in problem(root).fix


def test_a_global_hooks_path_chaining_to_installed_hooks_is_green(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chaining_hooks(tmp_path / "global-hooks")
    config(monkeypatch, tmp_path, hooks_path(tmp_path / "global-hooks"))
    install_hooks(root)
    assert hooks_installed.check(root) is None


@pytest.mark.parametrize("variable", ["GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"])
def test_a_hooks_path_outside_with_the_repository_hooks_missing_is_an_agents_install(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, variable: str
) -> None:
    chaining_hooks(tmp_path / "global-hooks")
    config(monkeypatch, tmp_path, hooks_path(tmp_path / "global-hooks"), variable=variable)
    found = problem(root)
    assert found.fix == f'run `python3 "{SCRIPT}"`'
    assert not found.owner
    assert "not installed" in found.detail


def test_a_global_hooks_path_that_does_not_chain_is_red(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("pre-commit", "commit-msg"):
        hook_script(tmp_path / "global-hooks", name, "exit 0")
    config(monkeypatch, tmp_path, hooks_path(tmp_path / "global-hooks"))
    install_hooks(root)
    found = problem(root)
    assert "do not run the repository's own" in found.detail
    assert f"git config core.hooksPath {root / '.git/hooks'}" in found.fix
    assert found.owner


def test_a_hooks_path_from_the_environment_is_named_by_its_scope(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_hooks(root)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(tmp_path))
    assert "set in the command git config" in problem(root).detail


def test_a_hook_that_never_answers_is_killed_at_the_timeout(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Why: pgrep searches the whole machine, so the duration carries this process's id; a
    # parallel worker or another checkout's run sleeping 31 seconds must not look like a leftover.
    nap = f"31.{os.getpid()}"
    hook_script(root / ".git/hooks", "pre-commit", f"exec sleep {nap}")
    hook_script(root / ".git/hooks", "commit-msg", f"exec sleep {nap}")
    monkeypatch.setattr(hooks_installed, "TIMEOUT", 0.5)
    found = problem(root)
    assert "had not finished after 0.5 seconds" in found.detail
    assert "A busy machine is the usual cause" in found.detail
    assert found.fix.startswith("run this again when the machine is quieter")
    assert not found.owner
    left = subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
        ["pgrep", "-f", f"sleep {nap}"],  # noqa: S607 # -- pgrep is resolved from PATH
        capture_output=True,
        check=False,
    )
    assert left.returncode == 1


def test_a_hook_that_fails_before_it_answers_is_not_read_as_not_installed(root: Path) -> None:
    # Why: a pre-commit framework error, such as a cache database locked for over five seconds
    # on a loaded machine, exits non-zero without reaching the recipe. The hooks are installed.
    hook_script(
        root / ".git/hooks",
        "pre-commit",
        "echo 'An unexpected error has occurred: OperationalError: database is locked' >&2\nexit 3",
    )
    hook_script(root / ".git/hooks", "commit-msg", answering("commit-msg"))
    found = problem(root)
    assert "the pre-commit hook exited 3 before it answered" in found.detail
    assert "OperationalError: database is locked" in found.detail
    assert "not installed" not in found.detail
    assert "pre-commit install" not in found.fix
    assert "git hook run pre-commit" in found.fix


def test_a_hook_that_fails_with_no_output_says_so(root: Path) -> None:
    hook_script(root / ".git/hooks", "pre-commit", answering("pre-commit"))
    hook_script(root / ".git/hooks", "commit-msg", "exit 1")
    assert "the commit-msg hook exited 1 before it answered (no output)" in problem(root).detail


def test_a_hook_that_answers_and_then_fails_is_still_an_answer(root: Path) -> None:
    hook_script(
        root / ".git/hooks", "pre-commit", answering("pre-commit").replace("exit 0", "exit 5")
    )
    hook_script(root / ".git/hooks", "commit-msg", answering("commit-msg"))
    assert hooks_installed.check(root) is None


def test_the_hooks_run_one_after_the_other_as_git_runs_them(root: Path, tmp_path: Path) -> None:
    # Why: two pre-commit processes at once contend for the cache database; a commit never runs
    # them together.
    log = tmp_path / "order"
    for name in hooks_installed.HOOKS:
        body = f"echo 'start {name}' >> {log}\nsleep 0.2\necho 'end {name}' >> {log}\n"
        hook_script(root / ".git/hooks", name, body + answering(name))
    assert hooks_installed.check(root) is None
    assert log.read_text(encoding="utf-8").splitlines() == [
        "start pre-commit",
        "end pre-commit",
        "start commit-msg",
        "end commit-msg",
    ]


def test_a_justfile_that_cannot_answer_is_not_probed(root: Path, tmp_path: Path) -> None:
    write(root, "justfile", "pre-commit:\n    just tier0\n")
    marker = tmp_path / "ran"
    hook_script(root / ".git/hooks", "pre-commit", f"echo ran > {marker}")
    found = problem(root)
    assert not marker.exists()
    assert str(TEMPLATE_JUSTFILE) in found.fix
    assert "without running tier 0" in found.detail


@pytest.mark.parametrize("name", ["justfile", "Justfile", ".justfile"])
def test_a_justfile_that_can_answer_is_probed(root: Path, name: str) -> None:
    write(root, name, ANSWERING_JUSTFILE)
    install_hooks(root)
    assert hooks_installed.check(root) is None


def test_a_git_without_hook_run_cannot_be_probed(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hooks_installed, "git_version", lambda: (2, 30, 0))
    found = problem(root)
    assert "no `git hook run`" in found.detail
    assert found.fix == "upgrade git to 2.36 or later"


def test_the_installed_git_reports_a_version_that_can_probe() -> None:
    assert hooks_installed.git_version() >= hooks_installed.MIN_GIT


def test_outside_a_repository_the_fix_is_git_init(tmp_path: Path) -> None:
    assert problem(tmp_path).fix == "run `git init` there"


def recipe(directory: Path, *args: str, probe: Path | None) -> subprocess.CompletedProcess[str]:
    just = shutil.which("just") or "just"
    env = {"PATH": f"{Path(just).parent}:/usr/bin:/bin", "HOME": str(directory)}
    if probe is not None:
        env["RATCHET_HOOK_PROBE"] = str(probe)
    return subprocess.run(  # noqa: S603
        [just, "--justfile", str(directory / "justfile"), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_the_template_recipes_answer_the_probe_and_nothing_else_does(tmp_path: Path) -> None:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    shutil.copy(TEMPLATE_JUSTFILE, tmp_path / "justfile")
    probe = tmp_path / "probe"
    assert recipe(tmp_path, "pre-commit", probe=probe).returncode == 0
    assert probe.read_text(encoding="utf-8").strip() == "pre-commit"
    assert recipe(tmp_path, "commit-msg", "any-file", probe=probe).returncode == 0
    assert probe.read_text(encoding="utf-8").strip() == "commit-msg"
    probe.unlink()
    # Why: without the variable the recipe runs the gates, which the template leaves unbound.
    assert recipe(tmp_path, "pre-commit", probe=None).returncode != 0
    # Why: tier0 reaches the gates directly, so a stray variable never skips one there.
    assert recipe(tmp_path, "tier0", probe=probe).returncode != 0
    assert not probe.exists()


def test_the_entry_point_installs_the_real_hooks_that_a_global_path_still_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    pre_commit, just = shutil.which("pre-commit"), shutil.which("just")
    if pre_commit is None or just is None:
        pytest.skip("pre-commit and just are not installed")
    # Why: this case is about the command the gate prints, not about how long a hook may take; the
    # limit is tested on its own, and a real pre-commit on a heavily loaded machine can exceed it.
    monkeypatch.setattr(hooks_installed, "TIMEOUT", 600.0)
    monkeypatch.setenv("PRE_COMMIT_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("PATH", f"{Path(just).parent}:{Path(pre_commit).parent}:/usr/bin:/bin")
    chaining_hooks(tmp_path / "global-hooks")
    config(monkeypatch, tmp_path, hooks_path(tmp_path / "global-hooks"))
    templates = hooks_installed.PLUGIN_ROOT / "skills/adopt/templates"
    adopted = project(tmp_path)
    shutil.copy(templates / ".pre-commit-config.yaml", adopted / ".pre-commit-config.yaml")
    shutil.copy(templates / "justfile", adopted / "justfile")
    git(adopted, "add", "-A")
    git(adopted, "commit", "-q", "-m", "chore: hooks")
    refused = subprocess.run(  # noqa: S603
        [pre_commit, "install"], cwd=adopted, capture_output=True, check=False
    )
    assert refused.returncode != 0
    assert problem(adopted).fix == f'run `python3 "{SCRIPT}"`'
    before = git(adopted, "config", "--list", "--show-origin")
    assert hooks_install.main(["hooks_install", "--root", str(adopted)]) == 0
    assert capsys.readouterr().out == "hooks: green\n"
    assert git(adopted, "config", "--list", "--show-origin") == before
    assert sorted(path.name for path in (tmp_path / "global-hooks").iterdir()) == [
        "commit-msg",
        "pre-commit",
    ]
    assert hooks_installed.check(adopted) is None
    (adopted / ".git/hooks/commit-msg").unlink()
    assert hooks_installed.check(adopted) is not None
