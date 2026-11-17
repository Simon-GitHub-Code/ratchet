import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

import plugin_pin
import release
from ratchet_guards import events
from tests import repos

JUSTFILE = (Path(__file__).resolve().parents[2] / "skills/adopt/templates/justfile").read_text(
    encoding="utf-8"
)
HOSTILE = 'a"$(touch pwned)`touch pwned2`$HOME'
EXCLUDED = {".git", ".in_use", "__pycache__"}


def raw_tree_id(copy: Path, scratch: Path) -> str:
    work = scratch / "oracle"
    shutil.copytree(repos.skeleton(), work / ".git", symlinks=True)
    for directory, names, files in os.walk(copy):
        linked = [name for name in names if (Path(directory) / name).is_symlink()]
        names[:] = [name for name in names if name not in EXCLUDED]
        for name in [*files, *(name for name in linked if name in names)]:
            path = Path(directory) / name
            relative = path.relative_to(copy).as_posix()
            if relative in (".claude-plugin/commit", ".orphaned_at"):
                continue
            if path.is_symlink():
                mode, sha = "120000", hash_object(work, "--stdin", feed=str(path.readlink()))
            else:
                executable = path.stat().st_mode & stat.S_IXUSR
                mode = "100755" if executable else "100644"
                sha = hash_object(work, "--no-filters", str(path))
            repos.git(work, "update-index", "--add", "--cacheinfo", f"{mode},{sha},{relative}")
    return repos.git(work, "write-tree")


def hash_object(work: Path, *args: str, feed: str | None = None) -> str:
    done = subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
        ["git", "-C", str(work), "hash-object", "-w", *args],  # noqa: S607 # -- git from PATH
        input=feed,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def release_copy(destination: Path) -> Path:
    shutil.copytree(repos.installed_copy(), destination, symlinks=True)
    return destination


def project(
    root: Path, copy: Path, tree: str, *, record: Path | None = None, extra: str = ""
) -> Path:
    version = plugin_pin.version_of(copy)
    pin = f"- Ratchet plugin: {version} at {'1' * 40} tree {tree}"
    repos.repository(
        root,
        {"justfile": JUSTFILE + extra, "docs/process/binding.md": f"# B\n\n## Runner\n\n{pin}\n"},
    )
    if record is not None:
        (root / ".git" / events.PLUGIN_RECORD).write_text(f"{record}\n", encoding="utf-8")
    return root


def just(
    root: Path, *args: str, plugin: Path | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    just_binary = shutil.which("just")
    if just_binary is None:
        pytest.skip("just is not installed")
    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in ("RATCHET_PLUGIN", "RATCHET_PLUGIN_DIR") and not name.startswith("GIT_")
    }
    environment.update(env or {})
    if plugin is not None:
        environment["RATCHET_PLUGIN"] = str(plugin)
    return subprocess.run(  # noqa: S603 # -- a fixed argv; the justfile is the template's own
        [just_binary, *args],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def red_line(copy: Path, held: str, pinned: str) -> str:
    return (
        f"the plugin at {copy} is tree {held}, not the {pinned} that docs/process/binding.md"
        " pins, and no copy on disk matches: upgrade this project with"
        f" `python3 {copy}/scripts/upgrade.py --apply`, or install the plugin release the pin"
        " names\n"
    )


def test_a_matching_copy_runs_and_the_first_line_names_it(tmp_path: Path) -> None:
    copy = release_copy(tmp_path / "copy")
    pinned = raw_tree_id(copy, tmp_path)
    assert plugin_pin.copy_tree_id(copy) == pinned
    done = just(project(tmp_path / "repo", copy, pinned), "plugin-pin", plugin=copy)
    assert done.returncode == 0, done.stdout + done.stderr
    version = plugin_pin.version_of(copy)
    assert done.stdout.splitlines()[0] == f"plugin-pin: using {version}, in {copy}"


def test_what_a_harness_adds_to_a_copy_does_not_change_its_tree_id(tmp_path: Path) -> None:
    copy = release_copy(tmp_path / "copy")
    pinned = raw_tree_id(copy, tmp_path)
    repos.write(copy, ".in_use/4242", "1\n")
    repos.write(copy, "scripts/__pycache__/x.cpython-312.pyc", "bytes")
    repos.write(copy, ".claude-plugin/commit", f"{'1' * 40}\n")
    done = just(project(tmp_path / "repo", copy, pinned), "plugin-pin", plugin=copy)
    assert done.returncode == 0, done.stdout + done.stderr


def test_a_copy_with_one_byte_changed_is_red_in_one_line(tmp_path: Path) -> None:
    copy = release_copy(tmp_path / "copy")
    pinned = raw_tree_id(copy, tmp_path)
    with (copy / "README.md").open("a", encoding="utf-8") as readme:
        readme.write("x")
    held = raw_tree_id(copy, tmp_path / "again")
    assert held != pinned
    done = just(project(tmp_path / "repo", copy, pinned), "plugin-pin", plugin=copy)
    assert (done.returncode, done.stdout, done.stderr) == (1, red_line(copy, held, pinned), "")


def test_a_copy_with_an_extra_file_is_red(tmp_path: Path) -> None:
    copy = release_copy(tmp_path / "copy")
    pinned = raw_tree_id(copy, tmp_path)
    repos.write(copy, "scripts/extra.py", "print('extra')\n")
    done = just(project(tmp_path / "repo", copy, pinned), "plugin-pin", plugin=copy)
    assert (done.returncode, done.stderr) == (1, "")
    assert done.stdout == red_line(copy, raw_tree_id(copy, tmp_path / "again"), pinned)


def test_a_copy_with_a_mode_changed_is_red(tmp_path: Path) -> None:
    copy = release_copy(tmp_path / "copy")
    pinned = raw_tree_id(copy, tmp_path)
    script = copy / "hooks/guard.py"
    script.chmod(0o644)
    done = just(project(tmp_path / "repo", copy, pinned), "plugin-pin", plugin=copy)
    assert done.returncode == 1
    assert done.stdout.startswith(f"the plugin at {copy} is tree ")


def sibling_world(tmp_path: Path, *, matching: bool) -> tuple[Path, Path, Path, str]:
    folder = tmp_path / "cache/ratchet"
    good = release_copy(folder / "earlier")
    pinned = raw_tree_id(good, tmp_path)
    recorded = release_copy(folder / "later")
    repos.write(recorded, "scripts/extra.py", "print('extra')\n")
    if not matching:
        repos.write(good, "scripts/extra.py", "print('another')\n")
    root = project(tmp_path / "repo", good, pinned, record=recorded)
    return root, good, recorded, pinned


def test_a_sibling_that_matches_runs_when_the_recorded_copy_does_not(tmp_path: Path) -> None:
    root, good, _, _ = sibling_world(tmp_path, matching=True)
    done = just(root, "plugin-pin")
    assert done.returncode == 0, done.stdout + done.stderr
    version = plugin_pin.version_of(good)
    assert done.stdout.splitlines()[0] == f"plugin-pin: using {version}, in {good}"


def test_with_no_matching_copy_on_disk_the_tier_is_red_in_one_line(tmp_path: Path) -> None:
    root, _, recorded, pinned = sibling_world(tmp_path, matching=False)
    done = just(root, "plugin-pin")
    held = raw_tree_id(recorded, tmp_path / "again")
    assert (done.returncode, done.stdout, done.stderr) == (1, red_line(recorded, held, pinned), "")


def test_the_id_is_the_same_under_a_hostile_global_git_configuration(tmp_path: Path) -> None:
    copy = release_copy(tmp_path / "copy")
    repos.write(copy, "docs/crlf.txt", "one\r\ntwo\r\n")
    repos.write(copy, "docs/lf.txt", "one\ntwo\n")
    repos.write(copy, ".gitattributes", "* text eol=crlf filter=mangle\n")
    repos.write(copy, ".gitignore", "kept.txt\n")
    repos.write(copy, "kept.txt", "an ignored file the release holds\n")
    pinned = raw_tree_id(copy, tmp_path)
    attributes = tmp_path / "attributes"
    attributes.write_text("* text eol=crlf filter=mangle\n", encoding="utf-8")
    config = tmp_path / "gitconfig"
    config.write_text(
        "[core]\n\tautocrlf = true\n\tfilemode = false\n\tsafecrlf = true\n"
        f"\tattributesFile = {attributes}\n"
        '[filter "mangle"]\n\tclean = tr a-z A-Z\n\tsmudge = tr A-Z a-z\n',
        encoding="utf-8",
    )
    hostile = {
        "GIT_CONFIG_GLOBAL": str(config),
        "GIT_CONFIG_SYSTEM": str(config),
        "XDG_CONFIG_HOME": str(tmp_path),
        "HOME": str(tmp_path),
    }
    done = just(project(tmp_path / "repo", copy, pinned), "plugin-pin", plugin=copy, env=hostile)
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.parametrize("extra", [False, True], ids=["as pinned", "with an extra file"])
def test_a_folder_name_with_shell_syntax_runs_nothing(tmp_path: Path, *, extra: bool) -> None:
    changed = extra
    copy = release_copy(tmp_path / HOSTILE)
    pinned = raw_tree_id(copy, tmp_path)
    if changed:
        repos.write(copy, "scripts/extra.py", "print('extra')\n")
    root = project(tmp_path / "repo", copy, pinned, record=copy)
    for plugin in (None, copy):
        done = just(root, "plugin-pin", plugin=plugin)
        assert done.returncode == (1 if changed else 0), done.stdout + done.stderr
        assert (done.stdout.startswith("the plugin at ")) == changed
    for where in (tmp_path, root, copy, copy.parent):
        assert not (where / "pwned").exists()
        assert not (where / "pwned2").exists()


def test_the_check_writes_nothing_into_the_projects_git_directory(tmp_path: Path) -> None:
    copy = release_copy(tmp_path / "copy")
    pinned = raw_tree_id(copy, tmp_path)
    root = project(tmp_path / "repo", copy, pinned, record=copy)

    def listing() -> list[tuple[str, int, int]]:
        found = []
        for path in sorted((root / ".git").rglob("*")):
            info = path.lstat()
            found.append((str(path.relative_to(root)), info.st_size, info.st_mtime_ns))
        return found

    before = listing()
    assert just(root, "plugin-pin").returncode == 0
    assert just(root, "plugin-pin", plugin=copy).returncode == 0
    changed = release_copy(tmp_path / "changed")
    repos.write(changed, "scripts/extra.py", "x\n")
    assert just(root, "plugin-pin", plugin=changed).returncode == 1
    assert listing() == before


def test_ci_holds_the_pins_tree_id_to_the_pinned_commits(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = {".claude-plugin/plugin.json": '{"version": "1.2.3"}'}
    used = repos.repository(tmp_path / "plugin", manifest)
    commit = repos.git(used, "rev-parse", "HEAD")
    held = repos.git(used, "rev-parse", "HEAD^{tree}")
    root = tmp_path / "repo"
    for tree, expected in ((held, 0), ("f" * 40, 1)):
        repos.write(
            root,
            "docs/process/binding.md",
            f"## Runner\n\n- Ratchet plugin: 1.2.3 at {commit} tree {tree}\n",
        )
        code = plugin_pin.main(["plugin_pin.py", "--root", str(root), "--plugin", str(used)])
        out = capsys.readouterr().out
        assert code == expected, out
    assert f"commit whose tree is {held}, not the {'f' * 40} that" in out


CHILD = """
probe: _plugin
    printf '%s' "$RATCHET_PLUGIN_DIR" > outer.txt
    printf '%s\\n' "$OTHER" > "$(git rev-parse --git-common-dir)/ratchet-plugin"
    just child

child: _plugin
    printf '%s' "$RATCHET_PLUGIN_DIR" > inner.txt
"""


def test_a_child_just_runs_the_folder_its_parent_checked_whatever_the_record_says_later(
    tmp_path: Path,
) -> None:
    good = release_copy(tmp_path / "cache/earlier")
    pinned = raw_tree_id(good, tmp_path)
    other = release_copy(tmp_path / "elsewhere/other")
    repos.write(other, "scripts/extra.py", "print('extra')\n")
    root = project(tmp_path / "repo", good, pinned, record=good, extra=CHILD)
    done = just(root, "probe", env={"OTHER": str(other)})
    assert done.returncode == 0, done.stdout + done.stderr
    assert (root / "outer.txt").read_text(encoding="utf-8") == str(good)
    assert (root / "inner.txt").read_text(encoding="utf-8") == str(good)
    record = root / ".git" / events.PLUGIN_RECORD
    assert record.read_text(encoding="utf-8").strip() == str(other)


def release_clone(tmp_path: Path) -> tuple[Path, str, str]:
    work, merged = plugin_clone_source(tmp_path / "work")
    clone = tmp_path / "clone"
    repos.git(tmp_path, "clone", "-q", "--branch", "v1.2.3", str(work), str(clone))
    return clone, merged, repos.git(clone, "rev-parse", "HEAD")


def plugin_clone_source(work: Path) -> tuple[Path, str]:
    repos.repository(work, {".claude-plugin/plugin.json": '{"version": "1.2.3"}'})
    merged = repos.git(work, "rev-parse", "HEAD")
    repos.write(work, "CHANGELOG.md", "# Changelog\n\n## 1.2.3\n\nThe release.\n")
    repos.commit(work, "docs(core): the changelog")
    assert release.main(["release.py", "--repo", str(work)]) == 0
    return work, merged


def test_a_clone_is_the_release_only_while_it_is_the_release_commit_and_clean(
    tmp_path: Path,
) -> None:
    clone, merged, tagged = release_clone(tmp_path)
    assert plugin_pin.commit_of(clone) == merged
    repos.write(clone, ".claude-plugin/plugin.json", "dirty\n")
    assert plugin_pin.commit_of(clone) == tagged
    repos.git(clone, "checkout", "-q", "--", ".claude-plugin/plugin.json")
    assert plugin_pin.commit_of(clone) == merged
    repos.write(clone, "untracked.txt", "x\n")
    assert plugin_pin.commit_of(clone) == tagged
    (clone / "untracked.txt").unlink()
    repos.git(clone, "config", "user.email", "t@example.invalid")
    repos.git(clone, "config", "user.name", "t")
    repos.git(clone, "config", "commit.gpgsign", "false")
    repos.git(clone, "switch", "-q", "-c", "side")
    repos.write(clone, "added.txt", "x\n")
    added = repos.commit(clone, "feat(core): another file")
    assert plugin_pin.commit_of(clone) == added


def test_a_clone_that_names_a_release_but_holds_other_files_is_red(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone, merged, _ = release_clone(tmp_path)
    released = repos.git(clone, "rev-parse", f"{merged}^{{tree}}")
    root = tmp_path / "repo"
    repos.write(
        root,
        "docs/process/binding.md",
        f"## Runner\n\n- Ratchet plugin: 1.2.3 at {merged} tree {released}\n",
    )
    args = ["plugin_pin.py", "--root", str(root), "--plugin", str(clone)]
    assert plugin_pin.main(args) == 0
    capsys.readouterr()
    repos.write(clone, "scripts/extra.py", "print('extra')\n")
    assert plugin_pin.main(args) == 1
    assert "is 1.2.3 at" in capsys.readouterr().out


@pytest.mark.parametrize("recipe", [("conformance",), ("commit-msg", "message")])
def test_a_record_naming_a_deleted_copy_is_red_in_one_line_not_a_traceback(
    tmp_path: Path, recipe: tuple[str, ...]
) -> None:
    copy = release_copy(tmp_path / "copy")
    pinned = raw_tree_id(copy, tmp_path)
    gone = tmp_path / "cache" / "gone"
    root = project(tmp_path / "repo", copy, pinned, record=gone)
    done = just(root, *recipe)
    assert done.returncode == 1
    assert done.stdout == (
        f"the plugin at {gone} is gone, and no copy on disk has the tree {pinned} that"
        " docs/process/binding.md pins: install the plugin release the pin names, then start an"
        " agent session here once, or set RATCHET_PLUGIN to its folder\n"
    )
    assert "Traceback" not in done.stderr
    assert done.stderr == ""
