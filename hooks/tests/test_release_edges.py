import json
from pathlib import Path

import pytest

import release
import release_check
from tests import repos
from tests.test_release import Out, checked, red, released, versioned

MANIFEST = ".claude-plugin/plugin.json"


def test_the_bump_commit_is_where_the_version_became_it_whatever_came_after(
    tmp_path: Path,
) -> None:
    root = repos.repository(tmp_path / "work", versioned("1.2.2"))
    repos.write(root, MANIFEST, json.dumps({"version": "1.2.3"}))
    bump = repos.commit(root, "feat(core): release 1.2.3")
    repos.write(root, MANIFEST, json.dumps({"version": "1.2.3", "name": "x"}))
    repos.commit(root, "chore(core): touch the manifest")
    repos.write(root, MANIFEST, json.dumps({"version": "1.2.4"}))
    repos.commit(root, "feat(core): release 1.2.4")
    assert release.bump_commit(root, "HEAD", "1.2.3") == bump
    assert release.bump_commit(root, "HEAD", "9.9.9") is None


def test_a_head_with_no_bump_commit_stops_the_release(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repos.repository(tmp_path / "work", versioned())
    monkeypatch.setattr(release, "bump_commit", lambda *_: None)
    assert release.main(["release.py", "--repo", str(root)]) == 1
    assert "where the version became 1.2.3" in capsys.readouterr().err
    assert repos.git(root, "tag") == ""


def test_a_failed_git_call_and_a_missing_changelog_are_stops(tmp_path: Path) -> None:
    root = repos.repository(tmp_path / "work", versioned())
    with pytest.raises(release.StopError, match="git rev-parse failed"):
        release.git(root, "rev-parse", "--verify", "no-such-ref")
    (root / "CHANGELOG.md").unlink()
    with pytest.raises(release.StopError, match="cannot be read"):
        release.notes(root, "1.2.3")


def test_a_commit_not_held_here_is_red_with_the_fetch_to_run(tmp_path: Path) -> None:
    root, base, tagged = released(tmp_path)
    absent = "0" * 40
    first = release_check.findings(root, "v1.2.3", absent, main=base, label="m")
    second = release_check.findings(root, "v1.2.3", tagged, main=absent, label="m")
    assert "run git fetch" in first[0]
    assert "run git fetch" in second[0]


def test_trees_that_cannot_be_read_are_red(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, base, tagged = released(tmp_path)
    monkeypatch.setattr(release_check, "tree_without_file", lambda *_: None)
    found = release_check.findings(root, "v1.2.3", tagged, main=base, label="main")
    assert any("cannot be read" in line for line in found)


def test_a_ref_that_is_not_there_is_red_in_local_mode(tmp_path: Path, capsys: Out) -> None:
    root, _, _ = released(tmp_path)
    code, lines = checked(root, capsys, main="no-such-ref")
    assert code == 1
    red(lines, says="no-such-ref: no such ref")


def test_a_remote_with_the_tag_and_no_default_branch_is_red(tmp_path: Path, capsys: Out) -> None:
    root, _, _ = released(tmp_path)
    origin = tmp_path / "origin.git"
    repos.git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    repos.git(root, "remote", "add", "origin", str(origin))
    repos.git(root, "push", "-q", "origin", "refs/tags/v1.2.3")
    capsys.readouterr()
    assert release_check.main(["release_check.py", "v1.2.3", "--repo", str(root)]) == 1
    assert "origin names no default branch" in capsys.readouterr().out


def test_a_version_that_goes_back_is_refused_and_the_old_tag_stays_green(
    tmp_path: Path, capsys: Out
) -> None:
    notes = "# C\n\n## 1.1.0\n\nOld.\n"
    root = repos.repository(
        tmp_path / "work", {MANIFEST: json.dumps({"version": "1.1.0"}), "CHANGELOG.md": notes}
    )
    assert release.main(["release.py", "--repo", str(root)]) == 0
    repos.write(root, MANIFEST, json.dumps({"version": "1.2.0"}))
    repos.write(root, "CHANGELOG.md", "# C\n\n## 1.2.0\n\nNew.\n\n## 1.1.0\n\nOld.\n")
    repos.commit(root, "feat(core): release 1.2.0")
    assert release.main(["release.py", "--repo", str(root)]) == 0
    repos.write(root, MANIFEST, json.dumps({"version": "1.1.0"}))
    repos.commit(root, "revert(core): back to 1.1.0")
    capsys.readouterr()
    assert release.main(["release.py", "--repo", str(root)]) == 1
    error = capsys.readouterr().err
    assert "1.1.0" in error
    assert "v1.2.0" in error
    code, lines = checked(root, capsys, tag="v1.1.0")
    assert (code, lines) == (0, ["release-check: v1.1.0 green"])


def test_a_version_that_is_not_dotted_numbers_is_refused(tmp_path: Path, capsys: Out) -> None:
    manifest = json.dumps({"version": "next"})
    root = repos.repository(
        tmp_path / "work", {MANIFEST: manifest, "CHANGELOG.md": "## next\n\nx\n"}
    )
    assert release.main(["release.py", "--repo", str(root)]) == 1
    assert "is not dotted numbers" in capsys.readouterr().err
