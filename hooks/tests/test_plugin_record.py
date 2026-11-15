"""The template justfile reads the plugin a repository's gates run from and runs nothing in it."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import justfile_text
import plugin_pin
from ratchet_guards import events
from tests import repos
from tests.support import PLUGIN_ROOT

TEMPLATE = PLUGIN_ROOT / "skills/adopt/templates/justfile"
JUSTFILE = TEMPLATE.read_text(encoding="utf-8")
NONE_RECORDED = (
    "no plugin recorded for this repository: start an agent session here once, or set"
    " RATCHET_PLUGIN to the plugin's folder\n"
)
USES = {"ratchet_plugin", "parallel", "changed", "booted", "probed"}
HOSTILE = 'a"$(touch pwned)`touch pwned2`$HOME'


def needs_just() -> str:
    just = shutil.which("just")
    if just is None:
        pytest.skip("just is not installed")
    return just


def environment(plugin: str | None) -> dict[str, str]:
    env = {
        name: value
        for name, value in os.environ.items()
        if name not in ("RATCHET_PLUGIN", "RATCHET_PLUGIN_DIR") and not name.startswith("GIT_")
    }
    if plugin is not None:
        env["RATCHET_PLUGIN"] = plugin
    return env


def just(
    cwd: Path, *args: str, plugin: str | None = None, path: str | None = None
) -> subprocess.CompletedProcess[str]:
    env = environment(plugin)
    if path is not None:
        env["PATH"] = path + os.pathsep + env["PATH"]
    return subprocess.run(  # noqa: S603 # -- a fixed argv; the justfile is the template's own
        [needs_just(), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def pin_line(copy: Path | None = None) -> str:
    copy = copy or repos.installed_copy()
    version = plugin_pin.version_of(copy)
    commit = plugin_pin.commit_of(copy) or "1" * 40
    return f"- Ratchet plugin: {version} at {commit} tree {plugin_pin.copy_tree_id(copy)}\n"


def adopted(root: Path, record: str | None = None, pinned: Path | None = None) -> Path:
    repos.repository(
        root,
        {
            "justfile": JUSTFILE,
            "docs/process/binding.md": f"# B\n\n## Runner\n\n{pin_line(pinned)}",
        },
    )
    if record is not None:
        (root / ".git" / events.PLUGIN_RECORD).write_text(f"{record}\n", encoding="utf-8")
    return root


def stub_plugin(root: Path, scripts: tuple[str, ...]) -> Path:
    for name in scripts:
        repos.write(root, f"scripts/{name}", 'import sys\nprint("ran", len(sys.argv))\n')
    return root


def test_an_empty_plugin_variable_is_unset_and_the_record_is_read(tmp_path: Path) -> None:
    root = adopted(tmp_path / "repo", str(repos.installed_copy()))
    for plugin in ("", None):
        done = just(root, "plugin-pin", plugin=plugin)
        assert done.returncode == 0, done.stdout
        assert done.stdout.splitlines()[-1] == "plugin-pin: green"


def test_a_named_plugin_wins_over_the_record(tmp_path: Path) -> None:
    root = adopted(tmp_path / "repo", str(tmp_path / "gone"))
    assert just(root, "plugin-pin", plugin=str(repos.installed_copy())).returncode == 0


def test_the_record_is_read_from_a_subdirectory(tmp_path: Path) -> None:
    root = adopted(tmp_path / "repo", str(repos.installed_copy()))
    below = root / "a/b"
    below.mkdir(parents=True)
    done = just(below, "plugin-pin")
    assert done.returncode == 0, done.stdout
    assert done.stdout.splitlines()[-1] == "plugin-pin: green"


def test_the_record_is_read_from_a_linked_work_tree(tmp_path: Path) -> None:
    root = adopted(tmp_path / "repo", str(repos.installed_copy()))
    linked = tmp_path / "linked"
    repos.git(root, "worktree", "add", "-q", str(linked), "-b", "side")
    assert not (linked / ".git").is_dir()
    done = just(linked, "plugin-pin")
    assert done.returncode == 0, done.stdout
    assert done.stdout.splitlines()[-1] == "plugin-pin: green"


def test_a_record_naming_a_copy_that_is_gone_says_where_it_looked(tmp_path: Path) -> None:
    gone = tmp_path / "gone"
    root = adopted(tmp_path / "repo", str(gone))
    done = just(root, "plugin-pin")
    assert done.returncode == 1
    assert f"the plugin at {gone} is tree unreadable, not the " in done.stdout


def test_the_first_line_of_a_tier_names_the_copy_its_version_and_its_commit(
    tmp_path: Path,
) -> None:
    copy = tmp_path / "copy"
    shutil.copytree(
        PLUGIN_ROOT / "scripts", copy / "scripts", ignore=shutil.ignore_patterns("__pycache__")
    )
    shutil.copytree(PLUGIN_ROOT / ".claude-plugin", copy / ".claude-plugin")
    repos.write(copy, "scripts/parallel.py", "")
    repos.write(copy, ".claude-plugin/commit", f"{'c' * 40}\n")
    version = plugin_pin.version_of(copy)
    root = adopted(tmp_path / "repo", str(copy))
    held = plugin_pin.copy_tree_id(copy)
    pin = f"- Ratchet plugin: {version} at {'c' * 40} tree {held}"
    repos.write(root, "docs/process/binding.md", f"## Runner\n\n{pin}\n")
    done = just(root, "tier2")
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.splitlines()[0] == f"plugin-pin: using {version} at {'c' * 40}, in {copy}"


@pytest.mark.parametrize("via", ["variable", "record"])
def test_a_folder_name_with_shell_syntax_runs_nothing(tmp_path: Path, via: str) -> None:
    folder = stub_plugin(tmp_path / HOSTILE, ("plugin_pin.py", "commits.py", "settings_rules.py"))
    root = adopted(tmp_path / "repo", str(folder) if via == "record" else None, folder)
    for recipe in (("plugin-pin",), ("commit-msg", "message"), ("settings",)):
        done = just(root, *recipe, plugin=str(folder) if via == "variable" else None)
        assert done.returncode == 0, done.stdout + done.stderr
        assert "ran" in done.stdout
    for where in (tmp_path, root, folder, folder.parent):
        assert not (where / "pwned").exists()
        assert not (where / "pwned2").exists()


def guarded_recipes() -> list[tuple[str, list[str]]]:
    needs_just()
    dump = subprocess.run(  # noqa: S603 # -- a fixed argv; the justfile is the template's own
        [needs_just(), "--justfile", str(TEMPLATE), "--dump", "--dump-format", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    recipes = json.loads(dump.stdout)["recipes"]
    return [
        (
            name,
            ["x"]
            * len(
                [
                    p
                    for p in recipe["parameters"]
                    if p["default"] is None and p["kind"] == "singular"
                ]
            ),
        )
        for name, recipe in recipes.items()
        if name != "_plugin"
        and (
            "_plugin" in [d["recipe"] for d in recipe["dependencies"]]
            or calls_guard(recipe["body"])
        )
    ]


def calls_guard(node: object) -> bool:
    if isinstance(node, list):
        return any(calls_guard(item) for item in node)
    return isinstance(node, str) and "just _plugin;" in node


def mentions(node: object) -> bool:
    if isinstance(node, list):
        if len(node) == 2 and node[0] == "variable" and node[1] in USES:
            return True
        return any(mentions(item) for item in node)
    return isinstance(node, str) and "RATCHET_PLUGIN_DIR" in node


def test_every_recipe_that_runs_a_file_of_the_plugin_goes_through_the_guard() -> None:
    needs_just()
    dump = subprocess.run(  # noqa: S603 # -- a fixed argv; the justfile is the template's own
        [needs_just(), "--justfile", str(TEMPLATE), "--dump", "--dump-format", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    recipes = json.loads(dump.stdout)["recipes"]
    unguarded = [
        name
        for name, recipe in recipes.items()
        if name != "_plugin"
        and mentions(recipe["body"])
        and not {d["recipe"] for d in recipe["dependencies"]} & {"_plugin", "plugin-pin"}
        and not calls_guard(recipe["body"])
    ]
    assert unguarded == []
    assert justfile_text.first_dependents(JUSTFILE, "_plugin")


@pytest.mark.parametrize("plugin", [None, ""])
def test_a_recipe_that_needs_the_plugin_says_none_is_recorded_in_one_line(
    tmp_path: Path, plugin: str | None
) -> None:
    root = adopted(tmp_path / "repo")
    recipes = guarded_recipes()
    assert {"commit-msg", "settings", "conformance", "hooks-install", "plugin-pin"} <= {
        name for name, _ in recipes
    }
    for name, arguments in recipes:
        done = just(root, name, *arguments, plugin=plugin)
        assert (done.returncode, done.stdout, done.stderr) == (1, NONE_RECORDED, ""), name


def test_with_no_repository_the_justfile_reads_no_file_at_the_root(tmp_path: Path) -> None:
    stubs = tmp_path / "stubs"
    log = tmp_path / "cat.log"
    repos.write(stubs, "git", "#!/bin/sh\nexit 128\n")
    repos.write(stubs, "cat", f'#!/bin/sh\necho "$@" >> "{log}"\n')
    for name in ("git", "cat"):
        (stubs / name).chmod(0o755)
    loose = tmp_path / "loose"
    loose.mkdir()
    (loose / "justfile").write_text(JUSTFILE, encoding="utf-8")
    done = just(loose, "_plugin", path=str(stubs))
    assert (done.returncode, done.stdout) == (1, NONE_RECORDED)
    assert not log.exists()
