"""The release: the tag on main's commit plus its commit file, and the check that holds the tag."""

import json
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
    code = release_check.main(["release_check.py", tag, "--main", main, "--repo", str(root)])
    return code, capsys.readouterr().out.splitlines()


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
    assert "git show failed" in capsys.readouterr().err


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
