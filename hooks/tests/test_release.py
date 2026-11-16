"""The release: the tag on main's commit plus its commit file, and the check that holds the tag."""

import json
import os
import subprocess
from pathlib import Path

import pytest

import release
import release_check
from tests import repos
from tests.support import PLUGIN_ROOT

Out = pytest.CaptureFixture[str]
MANIFEST = ".claude-plugin/plugin.json"
COMMIT_FILE = ".claude-plugin/commit"
CHANGELOG = "# Changelog\n\n## 1.2.3\n\nThe new thing.\n\n### Detail\n\n- a\n\n## 1.2.2\n\nOlder.\n"


def versioned(version: str = "1.2.3") -> dict[str, str]:
    return {MANIFEST: json.dumps({"version": version}), "CHANGELOG.md": CHANGELOG}


def released(tmp_path: Path) -> tuple[Path, str, str]:
    root = repos.repository(tmp_path / "work", versioned())
    base = repos.git(root, "rev-parse", "HEAD")
    assert release.main(["release.py", "--repo", str(root)]) == 0
    return root, base, repos.git(root, "rev-parse", "refs/tags/v1.2.3")


def checked(
    root: Path, capsys: Out, *, tag: str = "v1.2.3", main: str = "main"
) -> tuple[int, list[str]]:
    capsys.readouterr()
    code = release_check.main(
        ["release_check.py", tag, "--local", "--main", main, "--repo", str(root)]
    )
    return code, capsys.readouterr().out.splitlines()[1:]


def forged_on(root: Path, parent: str, scratch: Path, *, mode: str = "100644") -> str:
    def run(*args: str, stdin: str | None = None) -> str:
        return subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
            ["git", "-C", str(root), *args],  # noqa: S607 # -- git from PATH on purpose
            input=stdin,
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "GIT_INDEX_FILE": str(scratch / "forged-index")},
        ).stdout.strip()

    run("read-tree", parent)
    blob = run("hash-object", "-w", "--stdin", stdin=f"{parent}\n")
    run("update-index", "--add", "--cacheinfo", f"{mode},{blob},{COMMIT_FILE}")
    return repos.git(root, "commit-tree", run("write-tree"), "-p", parent, "-m", "forged")


def test_a_release_is_the_main_commit_plus_its_commit_file_and_a_lightweight_tag(
    tmp_path: Path, capsys: Out
) -> None:
    root, base, tagged = released(tmp_path)
    assert capsys.readouterr().out == f"created v1.2.3 {tagged}\n"
    assert repos.git(root, "cat-file", "-t", "refs/tags/v1.2.3") == "commit"
    assert repos.git(root, "rev-list", "--parents", "-n1", tagged) == f"{tagged} {base}"
    assert repos.git(root, "show", f"{tagged}:{COMMIT_FILE}") == base
    assert repos.git(root, "show", f"{tagged}:{COMMIT_FILE}") + "\n" == (
        repos.git(root, "cat-file", "-p", f"{tagged}:{COMMIT_FILE}") + "\n"
    )
    assert repos.git(root, "rev-parse", "HEAD") == base
    assert repos.git(root, "diff", "--name-only", base, tagged) == COMMIT_FILE
    assert repos.git(root, "status", "--porcelain") == ""


def test_a_second_run_with_the_tag_present_does_nothing(tmp_path: Path, capsys: Out) -> None:
    root, _, tagged = released(tmp_path)
    capsys.readouterr()
    assert release.main(["release.py", "--repo", str(root)]) == 0
    assert capsys.readouterr().out == "exists v1.2.3\n"
    assert repos.git(root, "rev-parse", "refs/tags/v1.2.3") == tagged


@pytest.mark.parametrize("manifest", ["not json", "[]", '{"name": "x"}'])
def test_a_manifest_with_no_version_stops_the_release(
    tmp_path: Path, capsys: Out, manifest: str
) -> None:
    root = repos.repository(tmp_path / "work", {MANIFEST: manifest})
    assert release.main(["release.py", "--repo", str(root)]) == 1
    assert capsys.readouterr().err.startswith("release: ")
    assert repos.git(root, "tag") == ""


def test_a_checkout_with_no_manifest_stops_the_release(tmp_path: Path, capsys: Out) -> None:
    root = repos.repository(tmp_path / "work")
    assert release.main(["release.py", "--repo", str(root)]) == 1
    assert "is missing, not JSON or has no version" in capsys.readouterr().err


def test_the_notes_are_the_versions_section_without_its_neighbours(
    tmp_path: Path, capsys: Out
) -> None:
    root = repos.repository(tmp_path / "work", versioned())
    assert release.main(["release.py", "--notes", "1.2.3", "--repo", str(root)]) == 0
    assert capsys.readouterr().out == "The new thing.\n\n### Detail\n\n- a\n"
    assert release.main(["release.py", "--notes", "1.2.2", "--repo", str(root)]) == 0
    assert capsys.readouterr().out == "Older.\n"


def test_a_version_with_no_section_has_no_notes(tmp_path: Path, capsys: Out) -> None:
    root = repos.repository(tmp_path / "work", versioned())
    assert release.main(["release.py", "--notes", "9.9.9", "--repo", str(root)]) == 1
    assert "has no section ## 9.9.9" in capsys.readouterr().err


def test_a_good_release_passes_the_check(tmp_path: Path, capsys: Out) -> None:
    root, _, _ = released(tmp_path)
    assert checked(root, capsys) == (0, ["release-check: v1.2.3 green"])


def red(lines: list[str], *, says: str) -> None:
    assert any(says in line for line in lines[:-1])
    assert "never move or delete this tag" in lines[-1]
    assert "next patch version" in lines[-1]


def test_a_tag_that_does_not_exist_is_red(tmp_path: Path, capsys: Out) -> None:
    root, _, _ = released(tmp_path)
    code, lines = checked(root, capsys, tag="v9.9.9")
    assert code == 1
    red(lines, says="no such tag")


def test_a_release_commit_without_the_commit_file_is_red(tmp_path: Path, capsys: Out) -> None:
    root, base, _ = released(tmp_path)
    repos.git(root, "tag", "-f", "v1.2.3", base)
    code, lines = checked(root, capsys)
    assert code == 1
    red(lines, says=f"holds no {COMMIT_FILE}")


@pytest.mark.parametrize("text", ["abc\n", "F" * 40 + "\n", "a" * 40 + "\n\n", ""])
def test_a_commit_file_that_is_not_one_sha_is_red(tmp_path: Path, capsys: Out, text: str) -> None:
    root, _, _ = released(tmp_path)
    repos.write(root, COMMIT_FILE, text)
    repos.snapshot(root, "chore: a hand made file")
    repos.git(root, "tag", "-f", "v1.2.3", "HEAD")
    code, lines = checked(root, capsys)
    assert code == 1
    red(lines, says="is not one full lowercase commit sha")


def test_a_commit_file_naming_no_commit_here_is_red(tmp_path: Path, capsys: Out) -> None:
    root, _, _ = released(tmp_path)
    repos.write(root, COMMIT_FILE, "a" * 40 + "\n")
    repos.snapshot(root, "chore: a hand made file")
    repos.git(root, "tag", "-f", "v1.2.3", "HEAD")
    code, lines = checked(root, capsys)
    assert code == 1
    red(lines, says="which is not a commit here")


def test_a_release_commit_with_another_parent_is_red(tmp_path: Path, capsys: Out) -> None:
    root, _, tagged = released(tmp_path)
    repos.write(root, "extra.md", "later\n")
    later = repos.commit(root, "feat(core): later")
    tree = repos.git(root, "rev-parse", f"{tagged}^{{tree}}")
    forged = repos.git(root, "commit-tree", tree, "-p", later, "-m", "forged")
    repos.git(root, "tag", "-f", "v1.2.3", forged)
    code, lines = checked(root, capsys)
    assert code == 1
    red(lines, says="only parent is not")


def test_a_release_commit_with_a_second_parent_is_red(tmp_path: Path, capsys: Out) -> None:
    root, base, tagged = released(tmp_path)
    repos.write(root, "extra.md", "later\n")
    later = repos.commit(root, "feat(core): later")
    tree = repos.git(root, "rev-parse", f"{tagged}^{{tree}}")
    forged = repos.git(root, "commit-tree", tree, "-p", base, "-p", later, "-m", "forged")
    repos.git(root, "tag", "-f", "v1.2.3", forged)
    code, lines = checked(root, capsys)
    assert code == 1
    red(lines, says="only parent is not")


def test_a_release_commit_that_changes_another_file_is_red(tmp_path: Path, capsys: Out) -> None:
    root, base, tagged = released(tmp_path)
    repos.git(root, "checkout", "-q", "--detach", tagged)
    repos.write(root, "README.md", "# changed\n")
    repos.snapshot(root, "chore: an edit")
    forged = repos.git(root, "commit-tree", "HEAD^{tree}", "-p", base, "-m", "forged")
    repos.git(root, "tag", "-f", "v1.2.3", forged)
    repos.git(root, "checkout", "-q", "main")
    code, lines = checked(root, capsys)
    assert code == 1
    red(lines, says=f"by more than {COMMIT_FILE}")


def test_the_check_says_which_it_read(tmp_path: Path, capsys: Out) -> None:
    root, _, _ = released(tmp_path)
    capsys.readouterr()
    release_check.main(
        ["release_check.py", "v1.2.3", "--local", "--main", "main", "--repo", str(root)]
    )
    assert capsys.readouterr().out.splitlines()[0] == (
        "release-check: read v1.2.3 and main from the local clone (local)"
    )
    with_origin(tmp_path, root)
    capsys.readouterr()
    assert release_check.main(["release_check.py", "v1.2.3", "--repo", str(root)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "release-check: read v1.2.3 and main from origin (remote)",
        "release-check: v1.2.3 green",
    ]


def with_origin(tmp_path: Path, root: Path) -> Path:
    origin = tmp_path / "origin.git"
    repos.git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    repos.git(root, "remote", "add", "origin", str(origin))
    repos.git(root, "push", "-q", "origin", "main", "refs/tags/v1.2.3")
    return origin


def test_a_tag_moved_on_the_remote_is_red_though_the_local_tag_is_not(
    tmp_path: Path, capsys: Out
) -> None:
    root, _, _ = released(tmp_path)
    origin = with_origin(tmp_path, root)
    repos.write(root, "extra.md", "later\n")
    later = repos.commit(root, "feat(core): later")
    repos.git(root, "push", "-q", "origin", "main")
    forged = forged_on(root, later, tmp_path)
    repos.git(root, "push", "-q", "origin", f"{forged}:refs/heads/forged")
    repos.git(origin, "update-ref", "refs/tags/v1.2.3", forged)
    assert checked(root, capsys)[0] == 0
    capsys.readouterr()
    code = release_check.main(["release_check.py", "v1.2.3", "--repo", str(root)])
    lines = capsys.readouterr().out.splitlines()
    assert code == 1
    assert lines[0] == "release-check: read v1.2.3 and main from origin (remote)"
    red(lines[1:], says="first-parent line where the version became 1.2.3")


def test_a_remote_that_cannot_be_read_or_holds_no_tag_is_red(tmp_path: Path, capsys: Out) -> None:
    root, _, _ = released(tmp_path)
    capsys.readouterr()
    assert release_check.main(["release_check.py", "v1.2.3", "--repo", str(root)]) == 1
    assert "`git ls-remote origin` failed" in capsys.readouterr().out
    with_origin(tmp_path, root)
    assert release_check.main(["release_check.py", "v9.9.9", "--repo", str(root)]) == 1
    assert "v9.9.9: no such tag on origin" in capsys.readouterr().out


def test_a_tag_moved_to_a_later_main_commit_of_the_same_version_is_red(
    tmp_path: Path, capsys: Out
) -> None:
    root, _, _ = released(tmp_path)
    repos.write(root, "extra.md", "later\n")
    later = repos.commit(root, "feat(core): later")
    repos.git(root, "tag", "-f", "v1.2.3", forged_on(root, later, tmp_path))
    code, lines = checked(root, capsys)
    assert code == 1
    red(lines, says="first-parent line where the version became 1.2.3")


def test_a_late_run_tags_the_commit_where_the_version_became_the_tags(
    tmp_path: Path, capsys: Out
) -> None:
    root = repos.repository(tmp_path / "work", versioned("1.2.2"))
    repos.write(root, MANIFEST, json.dumps({"version": "1.2.3"}))
    bump = repos.commit(root, "feat(core): release 1.2.3")
    for name in ("one", "two"):
        repos.write(root, f"{name}.md", "x\n")
        repos.commit(root, f"feat(core): {name}")
    assert release.main(["release.py", "--repo", str(root)]) == 0
    tagged = repos.git(root, "rev-parse", "refs/tags/v1.2.3")
    assert repos.git(root, "rev-parse", f"{tagged}^") == bump
    assert checked(root, capsys) == (0, ["release-check: v1.2.3 green"])


def test_a_release_with_no_changelog_section_makes_no_tag(tmp_path: Path, capsys: Out) -> None:
    root = repos.repository(
        tmp_path / "work", {MANIFEST: json.dumps({"version": "1.2.3"}), "CHANGELOG.md": "# C\n"}
    )
    assert release.main(["release.py", "--repo", str(root)]) == 1
    assert "has no section ## 1.2.3" in capsys.readouterr().err
    assert repos.git(root, "tag") == ""
    repos.write(root, "CHANGELOG.md", "# C\n\n## 1.2.3\n\n\n## 1.2.2\n\nx\n")
    repos.snapshot(root, "chore: empty")
    assert release.main(["release.py", "--repo", str(root)]) == 1
    assert "is empty" in capsys.readouterr().err
    assert repos.git(root, "tag") == ""


def test_a_manual_run_never_makes_a_tag(tmp_path: Path, capsys: Out) -> None:
    root = repos.repository(tmp_path / "work", versioned())
    capsys.readouterr()
    assert release.main(["release.py", "--existing", "--repo", str(root)]) == 1
    assert "never makes one" in capsys.readouterr().err
    assert repos.git(root, "tag") == ""
    assert release.main(["release.py", "--repo", str(root)]) == 0
    capsys.readouterr()
    assert release.main(["release.py", "--existing", "--repo", str(root)]) == 0
    assert capsys.readouterr().out == "exists v1.2.3\n"


def test_the_version_in_the_manifest_has_the_changelog_section_the_release_reads() -> None:
    version = json.loads((PLUGIN_ROOT / MANIFEST).read_text("utf-8"))["version"]
    assert release.notes(PLUGIN_ROOT, version)


def test_the_workflow_validates_the_notes_before_it_pushes_and_a_manual_run_makes_no_tag() -> None:
    text = (PLUGIN_ROOT / ".github/workflows/release.yml").read_text("utf-8")
    assert "\n  workflow_dispatch:\n" in text
    assert text.index("release.py --notes") < text.index("git push origin")
    assert text.index('release_check.py "$tag" --local --main HEAD') < text.index("git push origin")
    assert 'then made="$(python3 scripts/release.py --existing)"' in text


def test_the_check_the_workflow_runs_before_the_push_passes_against_head(
    tmp_path: Path, capsys: Out
) -> None:
    root, _, _ = released(tmp_path)
    code, lines = checked(root, capsys, main="HEAD")
    assert code == 0, lines


def test_a_tree_that_cannot_be_read_is_an_error_not_an_empty_tree(tmp_path: Path) -> None:
    root, _, _ = released(tmp_path)
    assert release_check.tree_without_file(root, "0" * 40) is None
    assert release_check.tree_without_file(root, "HEAD") is not None


@pytest.mark.parametrize("mode", ["100755", "120000"])
def test_a_commit_file_that_is_not_a_regular_file_is_red(
    tmp_path: Path, capsys: Out, mode: str
) -> None:
    root, base, _ = released(tmp_path)
    repos.git(root, "tag", "-f", "v1.2.3", forged_on(root, base, tmp_path, mode=mode))
    code, lines = checked(root, capsys)
    assert code == 1
    red(lines, says=f"is not a regular file (mode {mode})")


def test_a_main_commit_off_the_main_ref_is_red(tmp_path: Path, capsys: Out) -> None:
    root, _, _ = released(tmp_path)
    repos.git(root, "checkout", "-q", "--orphan", "other")
    repos.write(root, "o.md", "o\n")
    repos.commit(root, "feat(core): other")
    code, lines = checked(root, capsys, main="other")
    assert code == 1
    red(lines, says="is not on other")


def test_a_version_that_is_not_the_tags_is_red(tmp_path: Path, capsys: Out) -> None:
    root, _, tagged = released(tmp_path)
    repos.git(root, "tag", "v1.2.4", tagged)
    code, lines = checked(root, capsys, tag="v1.2.4")
    assert code == 1
    red(lines, says="is 1.2.3, not v1.2.4")


def test_a_manifest_that_names_no_version_is_red(tmp_path: Path, capsys: Out) -> None:
    root = repos.repository(tmp_path / "work", {MANIFEST: "[1]"})
    base = repos.git(root, "rev-parse", "HEAD")
    repos.write(root, COMMIT_FILE, f"{base}\n")
    repos.git(root, "add", "-A")
    forged = repos.git(root, "commit-tree", repos.git(root, "write-tree"), "-p", base, "-m", "x")
    repos.git(root, "tag", "v1.2.3", forged)
    code, lines = checked(root, capsys)
    assert code == 1
    red(lines, says="is None, not v1.2.3")
    repos.write(root, MANIFEST, "{")
    repos.snapshot(root, "chore: break")
    base = repos.git(root, "rev-parse", "HEAD")
    repos.write(root, COMMIT_FILE, f"{base}\n")
    repos.git(root, "add", "-A")
    forged = repos.git(root, "commit-tree", repos.git(root, "write-tree"), "-p", base, "-m", "x")
    repos.git(root, "tag", "-f", "v1.2.3", forged)
    code, lines = checked(root, capsys)
    assert code == 1
    red(lines, says="is None, not v1.2.3")


def test_main_never_holds_the_release_commit_file() -> None:
    assert not (PLUGIN_ROOT / COMMIT_FILE).exists(), (
        f"{COMMIT_FILE} lives only on release commits (ADR 0068); a copy on main is stale "
        "from the next commit"
    )


@pytest.mark.xfail(
    strict=True, reason="the version PR switches the marketplace entry to the vV tag (step b)"
)
def test_the_marketplace_installs_the_tag_of_the_version() -> None:
    version = json.loads((PLUGIN_ROOT / MANIFEST).read_text("utf-8"))["version"]
    entry = next(
        plugin
        for plugin in json.loads(
            (PLUGIN_ROOT / ".claude-plugin/marketplace.json").read_text("utf-8")
        )["plugins"]
        if plugin["name"] == "ratchet"
    )
    assert entry["source"] == {
        "source": "github",
        "repo": "Simon-GitHub-Code/ratchet",
        "ref": f"v{version}",
    }, "the version PR switches the marketplace entry to the vV tag and removes this xfail (step b)"
