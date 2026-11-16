"""The justfile check holds against a forged marker, planted bytecode and a real cache layout."""

import os
import stat
import subprocess
from pathlib import Path

import pytest

import plugin_pin
from tests import repos
from tests.test_plugin_tree import just, project, raw_tree_id, red_line, release_copy


def odd_tree(copy: Path) -> None:
    repos.write(copy, "tools/deep/er/file.txt", "nested\n")
    repos.write(copy, "tools/run.sh", "#!/bin/sh\n")
    (copy / "tools/run.sh").chmod(0o755)
    repos.write(copy, "a.b", "dot sorts before the slash\n")
    repos.write(copy, "a/inner.txt", "a folder named a\n")
    repos.write(copy, "café.txt", "unicode\n")
    repos.write(copy, "crlf.txt", "one\r\ntwo\r\n")
    (copy / "empty").mkdir()
    (copy / "link-to-file").symlink_to("a.b")
    (copy / "link-to-folder").symlink_to("tools")
    (copy / "dangling").symlink_to("nowhere/at/all")
    repos.write(copy, ".orphaned_at", "1763251200000\n")


def test_the_tree_id_is_git_write_trees_for_executables_links_and_nested_folders(
    tmp_path: Path,
) -> None:
    copy = release_copy(tmp_path / "copy")
    odd_tree(copy)
    pinned = raw_tree_id(copy, tmp_path)
    assert plugin_pin.copy_tree_id(copy) == pinned
    done = just(project(tmp_path / "repo", copy, pinned), "plugin-pin", plugin=copy)
    assert done.returncode == 0, done.stdout + done.stderr


def test_an_empty_folder_hashes_as_the_empty_tree(tmp_path: Path) -> None:
    (tmp_path / "nothing").mkdir()
    assert (
        plugin_pin.copy_tree_id(tmp_path / "nothing") == "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    )
    assert plugin_pin.copy_tree_id(tmp_path / "absent") is None


def test_an_unreadable_file_leaves_no_id(tmp_path: Path) -> None:
    copy = release_copy(tmp_path / "copy")
    secret = copy / "secret.txt"
    secret.write_text("x\n", encoding="utf-8")
    secret.chmod(0)
    try:
        if os.access(secret, os.R_OK):
            pytest.skip("permissions are not enforced here")
        assert plugin_pin.copy_tree_id(copy) is None
    finally:
        secret.chmod(stat.S_IRUSR | stat.S_IWUSR)


def test_the_hash_needs_no_git_and_stores_nothing(tmp_path: Path) -> None:
    copy = release_copy(tmp_path / "copy")
    pinned = raw_tree_id(copy, tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "git-calls"
    fake = bin_dir / "git"
    fake.write_text(f'#!/bin/sh\necho "$@" >> {log}\nexit 1\n', encoding="utf-8")
    fake.chmod(0o755)
    root = project(tmp_path / "repo", copy, pinned)
    path = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
    done = just(root, "plugin-pin", plugin=copy, env={"PATH": path})
    assert done.returncode == 0, done.stdout + done.stderr
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    assert set(calls) <= {"config --get ratchet.jobs"}


def forged_world(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    good = release_copy(tmp_path / "cache/good")
    pinned = raw_tree_id(good, tmp_path)
    evil = release_copy(tmp_path / "evil")
    repos.write(evil, "scripts/plugin_pin.py", "print('plugin-pin: EVIL, green')\n")
    repos.write(evil, "scripts/conformance.py", "import sys\nsys.exit(0)\n")
    return project(tmp_path / "repo", good, pinned, record=good), good, evil, pinned


def test_a_forged_marker_names_a_folder_that_is_hashed_and_refused(tmp_path: Path) -> None:
    root, good, evil, pinned = forged_world(tmp_path)
    forged = {"RATCHET_PLUGIN_CHECKED": f"D{pinned} {evil}"}
    for recipe in (("plugin-pin",), ("conformance",)):
        done = just(root, *recipe, env=forged)
        assert done.returncode == 1, done.stdout + done.stderr
        assert "EVIL" not in done.stdout
        held = plugin_pin.copy_tree_id(evil)
        assert done.stdout == red_line(evil, held or "unreadable", pinned)
    assert good.is_dir()


def test_a_marker_never_sends_the_child_to_a_sibling_or_the_record(tmp_path: Path) -> None:
    root, good, evil, pinned = forged_world(tmp_path)
    repos.write(good, "scripts/extra.py", "print('now another tree')\n")
    done = just(root, "plugin-pin", env={"RATCHET_PLUGIN_CHECKED": f"D{pinned} {evil}"})
    assert done.returncode == 1
    assert done.stdout.startswith(f"the plugin at {evil} is tree ")


def test_a_marker_naming_the_pinned_tree_and_folder_runs_it(tmp_path: Path) -> None:
    root, good, _, pinned = forged_world(tmp_path)
    done = just(root, "plugin-pin", env={"RATCHET_PLUGIN_CHECKED": f"D{pinned} {good}"})
    assert done.returncode == 0, done.stdout + done.stderr


def plant(tmp_path: Path, destination: Path) -> Path:
    flag = tmp_path / "ran-evil"
    source = tmp_path / "evil_gatelib.py"
    source.write_text(
        f"import os\nopen({str(flag)!r}, 'w').close()\nos._exit(0)\n", encoding="utf-8"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    compile_it = (
        "import py_compile, sys\n"
        "py_compile.compile(sys.argv[1], cfile=sys.argv[2], doraise=True,"
        " invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)\n"
    )
    subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
        ["python3", "-c", compile_it, str(source), str(destination)],  # noqa: S607 # -- PATH
        check=True,
    )
    return flag


def cache_tag() -> str:
    return subprocess.run(  # -- a fixed argv, no shell
        ["python3", "-c", "import sys; print(sys.implementation.cache_tag)"],  # noqa: S607 # -- PATH
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_a_planted_compiled_file_in_a_copy_never_runs(tmp_path: Path) -> None:
    copy = release_copy(tmp_path / "copy")
    pinned = raw_tree_id(copy, tmp_path)
    flag = plant(tmp_path, copy / "scripts/__pycache__" / f"gatelib.{cache_tag()}.pyc")
    root = project(tmp_path / "repo", copy, pinned)
    done = just(root, "plugin-pin", plugin=copy, env={"TMPDIR": str(tmp_path)})
    assert done.returncode == 0, done.stdout + done.stderr
    assert not flag.exists()


@pytest.mark.parametrize("how", ["open to others", "a link"])
def test_a_compiled_file_in_a_folder_this_user_does_not_hold_privately_never_runs(
    tmp_path: Path, how: str
) -> None:
    copy = release_copy(tmp_path / "copy")
    pinned = raw_tree_id(copy, tmp_path)
    temporary = tmp_path / "shared"
    temporary.mkdir()
    folder = temporary / f"ratchet-pyc-{os.getuid()}"
    held = folder if how == "open to others" else tmp_path / "elsewhere"
    keyed = held / pinned / str(copy / "scripts").lstrip("/") / f"gatelib.{cache_tag()}.pyc"
    flag = plant(tmp_path, keyed)
    if how == "open to others":
        folder.chmod(0o777)
    else:
        folder.symlink_to(held)
    root = project(tmp_path / "repo", copy, pinned)
    done = just(root, "plugin-pin", plugin=copy, env={"TMPDIR": str(temporary)})
    assert done.returncode == 0, done.stdout + done.stderr
    assert not flag.exists()
    assert sorted(path.name for path in held.rglob("*.pyc")) == [keyed.name]


def test_compiled_files_go_to_a_cache_outside_every_copy(tmp_path: Path) -> None:
    copy = release_copy(tmp_path / "copy")
    pinned = raw_tree_id(copy, tmp_path)
    root = project(tmp_path / "repo", copy, pinned)
    done = just(root, "plugin-pin", plugin=copy, env={"TMPDIR": str(tmp_path)})
    assert done.returncode == 0, done.stdout + done.stderr
    assert not list(copy.rglob("__pycache__"))
    assert list(tmp_path.glob(f"ratchet-pyc-*/{pinned}/**/*.pyc"))


def test_a_sibling_marked_orphaned_by_the_harness_still_matches(tmp_path: Path) -> None:
    layout = tmp_path / "plugins/cache/ratchet/ratchet"
    earlier = release_copy(layout / "0.12.3")
    pinned = raw_tree_id(earlier, tmp_path)
    repos.write(earlier, ".orphaned_at", "1763251200000\n")
    assert raw_tree_id(earlier, tmp_path / "again") == pinned
    later = release_copy(layout / "0.13.0")
    repos.write(later, "scripts/extra.py", "print('later')\n")
    root = project(tmp_path / "repo", earlier, pinned, record=later)
    done = just(root, "plugin-pin")
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.splitlines()[0].endswith(f"in {earlier}")


def test_a_folder_beside_the_record_with_no_plugin_manifest_is_never_tried(
    tmp_path: Path,
) -> None:
    layout = tmp_path / "cache/ratchet"
    bare = release_copy(layout / "bare")
    (bare / ".claude-plugin/plugin.json").unlink()
    pinned = raw_tree_id(bare, tmp_path)
    recorded = release_copy(layout / "later")
    root = project(tmp_path / "repo", recorded, pinned, record=recorded)
    done = just(root, "plugin-pin")
    assert done.returncode == 1
    assert done.stdout.startswith(f"the plugin at {recorded} is tree ")


def test_with_nothing_recorded_the_guard_says_so_and_not_an_unbound_variable(
    tmp_path: Path,
) -> None:
    copy = release_copy(tmp_path / "copy")
    root = project(tmp_path / "repo", copy, raw_tree_id(copy, tmp_path))
    done = just(root, "_plugin", env={"RATCHET_PLUGIN": ""})
    assert done.returncode == 1
    assert "unbound" not in done.stdout + done.stderr
    assert done.stdout.startswith("no plugin recorded for this repository")


def test_a_named_pipe_in_a_copy_is_left_out_and_never_read(tmp_path: Path) -> None:
    copy = release_copy(tmp_path / "copy")
    before = plugin_pin.copy_tree_id(copy)
    os.mkfifo(copy / "pipe")
    assert plugin_pin.copy_tree_id(copy) == before


def test_a_git_directory_planted_in_a_matching_copy_runs_no_command(tmp_path: Path) -> None:
    copy = release_copy(tmp_path / "copy")
    repos.write(copy, ".claude-plugin/commit", f"{'1' * 40}\n")
    pinned = raw_tree_id(copy, tmp_path)
    flag = tmp_path / "ran-fsmonitor"
    repos.git(copy, "init", "-q")
    repos.git(copy, "config", "user.email", "t@example.invalid")
    repos.git(copy, "config", "user.name", "t")
    repos.git(copy, "config", "commit.gpgsign", "false")
    repos.git(copy, "add", "-A", "-f")
    repos.git(copy, "commit", "-q", "-m", "planted")
    repos.git(copy, "config", "core.fsmonitor", f"touch {flag}; true")
    root = project(tmp_path / "repo", copy, pinned)
    for recipe in ("plugin-pin", "conformance"):
        just(root, recipe, plugin=copy, env={"TMPDIR": str(tmp_path)})
        assert not flag.exists(), recipe
    assert just(root, "plugin-pin", plugin=copy).returncode == 0


def test_a_folder_a_tier_hashed_is_read_without_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copy = release_copy(tmp_path / "copy")
    (copy / ".git").mkdir()
    repos.write(copy, ".claude-plugin/commit", f"{'2' * 40}\n")
    monkeypatch.delenv("RATCHET_PLUGIN_CHECKED", raising=False)
    assert not plugin_pin.checked_folder(copy)
    assert plugin_pin.commit_of(copy) == plugin_pin.UNREADABLE
    monkeypatch.setenv("RATCHET_PLUGIN_CHECKED", f"D{'3' * 40} {copy}")
    assert plugin_pin.checked_folder(copy)
    assert plugin_pin.commit_of(copy) == "2" * 40
    assert plugin_pin.release_tree(copy) == plugin_pin.copy_tree_id(copy)


def marker_module(tmp_path: Path, name: str) -> tuple[Path, Path]:
    flag = tmp_path / "ran-evil"
    folder = tmp_path / "hijack"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(f"open({str(flag)!r}, 'w').close()\n", encoding="utf-8")
    return folder, flag


def gate_with(tmp_path: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    copy = release_copy(tmp_path / "copy")
    pinned = raw_tree_id(copy, tmp_path)
    root = project(tmp_path / "repo", copy, pinned)
    return just(root, "plugin-pin", plugin=copy, env={"TMPDIR": str(tmp_path), **env})


def test_a_folder_on_pythonpath_holding_json_never_replaces_the_real_module(
    tmp_path: Path,
) -> None:
    folder, flag = marker_module(tmp_path, "json.py")
    done = gate_with(tmp_path, {"PYTHONPATH": str(folder)})
    assert done.returncode == 0, done.stdout + done.stderr
    assert not flag.exists()


@pytest.mark.parametrize(
    "variable", [("PYTHONSAFEPATH", "1"), ("PYTHONHOME", "/nonexistent/ratchet-home")]
)
def test_an_interpreter_variable_that_would_break_a_script_run_leaves_the_gate_green(
    tmp_path: Path, variable: tuple[str, str]
) -> None:
    done = gate_with(tmp_path, dict([variable]))
    assert done.returncode == 0, done.stdout + done.stderr


def test_a_startup_file_named_by_pythonstartup_never_runs(tmp_path: Path) -> None:
    flag = tmp_path / "ran-evil"
    startup = tmp_path / "startup.py"
    startup.write_text(f"open({str(flag)!r}, 'w').close()\n", encoding="utf-8")
    done = gate_with(tmp_path, {"PYTHONSTARTUP": str(startup), "PYTHONINSPECT": "1"})
    assert done.returncode == 0, done.stdout + done.stderr
    assert not flag.exists()


def test_on_the_fallback_no_compiled_file_is_written_anywhere(tmp_path: Path) -> None:
    copy = release_copy(tmp_path / "copy")
    pinned = raw_tree_id(copy, tmp_path)
    temporary = tmp_path / "shared"
    temporary.mkdir()
    folder = temporary / f"ratchet-pyc-{os.getuid()}"
    folder.mkdir()
    folder.chmod(0o777)
    root = project(tmp_path / "repo", copy, pinned)
    done = just(root, "plugin-pin", plugin=copy, env={"TMPDIR": str(temporary)})
    assert done.returncode == 0, done.stdout + done.stderr
    assert not list(copy.rglob("*.pyc"))
    assert not list(temporary.rglob("*.pyc"))


def test_on_the_fallback_a_planted_compiled_file_in_a_copy_never_runs(tmp_path: Path) -> None:
    copy = release_copy(tmp_path / "copy")
    pinned = raw_tree_id(copy, tmp_path)
    flag = plant(tmp_path, copy / "scripts/__pycache__" / f"gatelib.{cache_tag()}.pyc")
    temporary = tmp_path / "shared"
    temporary.mkdir()
    folder = temporary / f"ratchet-pyc-{os.getuid()}"
    folder.mkdir()
    folder.chmod(0o777)
    root = project(tmp_path / "repo", copy, pinned)
    done = just(root, "plugin-pin", plugin=copy, env={"TMPDIR": str(temporary)})
    assert done.returncode == 0, done.stdout + done.stderr
    assert not flag.exists()


def test_a_group_writable_cache_folder_is_refused_with_one_line_saying_why_and_the_way_out(
    tmp_path: Path,
) -> None:
    copy = release_copy(tmp_path / "copy")
    pinned = raw_tree_id(copy, tmp_path)
    temporary = tmp_path / "shared"
    temporary.mkdir()
    folder = temporary / f"ratchet-pyc-{os.getuid()}"
    keyed = folder / pinned / str(copy / "scripts").lstrip("/") / f"gatelib.{cache_tag()}.pyc"
    flag = plant(tmp_path, keyed)
    folder.chmod(0o775)
    root = project(tmp_path / "repo", copy, pinned)
    done = just(root, "plugin-pin", plugin=copy, env={"TMPDIR": str(temporary)})
    assert done.returncode == 0, done.stdout + done.stderr
    assert not flag.exists()
    said = [line for line in done.stderr.splitlines() if "compiled files are not cached" in line]
    assert len(said) == 1
    assert f"{folder} is mode 775" in said[0]
    assert f"remove {folder}" in said[0]
    assert "compiles from source on each start" in said[0]
    assert sorted(path.name for path in folder.rglob("*.pyc")) == [keyed.name]


def test_a_cache_folder_that_is_a_link_is_refused_with_the_line(tmp_path: Path) -> None:
    copy = release_copy(tmp_path / "copy")
    pinned = raw_tree_id(copy, tmp_path)
    temporary = tmp_path / "shared"
    temporary.mkdir()
    (temporary / f"ratchet-pyc-{os.getuid()}").symlink_to(tmp_path / "elsewhere")
    root = project(tmp_path / "repo", copy, pinned)
    done = just(root, "plugin-pin", plugin=copy, env={"TMPDIR": str(temporary)})
    assert done.returncode == 0, done.stdout + done.stderr
    assert "is a link" in done.stderr


def test_a_private_cache_folder_prints_nothing(tmp_path: Path) -> None:
    done = gate_with(tmp_path, {})
    assert done.returncode == 0, done.stdout + done.stderr
    assert "compiled files are not cached" not in done.stderr


def test_a_json_module_at_the_project_root_never_runs_in_an_inline_gate_call(
    tmp_path: Path,
) -> None:
    copy = release_copy(tmp_path / "copy")
    pinned = raw_tree_id(copy, tmp_path)
    root = project(tmp_path / "repo", copy, pinned)
    flag = tmp_path / "ran-evil"
    (root / "json.py").write_text(f"open({str(flag)!r}, 'w').close()\n", encoding="utf-8")
    just(root, "guard-diff", plugin=copy, env={"TMPDIR": str(tmp_path)})
    assert not flag.exists()
