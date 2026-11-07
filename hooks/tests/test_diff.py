"""``just guard-diff``: changed paths, both manifests, the summary and annotations."""

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from ratchet_guards import diff
from ratchet_guards.manifest import MANIFEST_PATH, Group, parse
from tests.support import MANIFEST_TEXT, PLUGIN_ROOT

BASE_MANIFEST = '[[group]]\nname = "old"\nreason = "r"\npaths = ["dropped/**", "CLAUDE.md"]\n'


def fake_git(
    worktree: Path,
    tracked: str,
    *,
    untracked: str = "",
    base_manifest: str | None = BASE_MANIFEST,
    base: str = "origin/main",
) -> diff.Git:
    """A git that answers only the exact command lines ``ratchet_guards.diff`` should run."""
    answers: dict[tuple[str, ...], str | None] = {
        ("rev-parse", "--show-toplevel"): f"{worktree}\n",
        ("merge-base", base, "HEAD"): "abc123\n",
        ("diff", "--name-only", "--no-renames", "-z", "abc123"): tracked,
        ("ls-files", "--others", "--exclude-standard", "--full-name", "-z", ":/"): untracked,
        ("show", f"abc123:{MANIFEST_PATH}"): base_manifest,
        ("rev-parse", "--verify", "--quiet", "origin/main^{commit}"): "abc\n",
    }

    def git(args: Sequence[str]) -> str | None:
        return answers[tuple(args)]

    return git


def worktree_with_manifest(tmp_path: Path, text: str | None = None) -> Path:
    (tmp_path / "guards").mkdir()
    manifest = text if text is not None else MANIFEST_TEXT
    (tmp_path / MANIFEST_PATH).write_text(manifest, encoding="utf-8")
    return tmp_path


def test_changed_paths_merge_tracked_and_untracked() -> None:
    git = fake_git(Path("/w"), "b\0a\0", untracked="c\0a\0")
    assert diff.changed_paths(git, "origin/main") == ["a", "b", "c"]


def test_a_failing_git_is_an_error() -> None:
    with pytest.raises(RuntimeError, match="git merge-base origin/main HEAD failed"):
        diff.changed_paths(lambda _: None, "origin/main")


def test_both_manifests_count(tmp_path: Path) -> None:
    worktree = worktree_with_manifest(
        tmp_path, '[[group]]\nname = "new"\nreason = "r"\npaths = ["x"]\n'
    )
    found = diff.manifests(fake_git(worktree, ""), "origin/main", worktree)
    assert [group.name for manifest in found for group in manifest.groups] == ["old", "new"]
    matches = diff.guarded(["dropped/a.toml", "x", "CLAUDE.md", "free.py"], found)
    assert [(path, group.name) for path, group in matches] == [
        ("dropped/a.toml", "old"),
        ("x", "new"),
        ("CLAUDE.md", "old"),
    ]


def test_missing_or_broken_manifests_are_skipped(tmp_path: Path) -> None:
    git = fake_git(tmp_path, "", base_manifest=None, base="b")
    assert diff.manifests(git, "b", tmp_path) == []
    worktree = worktree_with_manifest(tmp_path, "broken [")
    found = diff.manifests(fake_git(worktree, "", base="b"), "b", worktree)
    assert [group.name for manifest in found for group in manifest.groups] == ["old"]


def test_a_missing_or_broken_base_manifest_still_reads_the_work_tree(tmp_path: Path) -> None:
    worktree = worktree_with_manifest(
        tmp_path, '[[group]]\nname = "new"\nreason = "r"\npaths = ["x"]\n'
    )
    for base_manifest in (None, "broken ["):
        git = fake_git(worktree, "", base_manifest=base_manifest)
        found = diff.manifests(git, "origin/main", worktree)
        assert [group.name for manifest in found for group in manifest.groups] == ["new"]


def test_markdown_and_annotations() -> None:
    group = Group("ci", "r", ("x",))
    assert diff.markdown([("a", group), ("b", group)], "main").endswith(
        "| `a` | ci |\n| `b` | ci |\n"
    )
    assert diff.markdown([], "main") == (
        "## Guard paths\n\nNo guard path changed against the merge base with `main`.\n"
    )
    assert diff.markdown([("a`b|c", group)], "main") == (
        "## :warning: 1 guard path(s) changed\n\nAgainst the merge base with `main`. Guard paths "
        "define the quality gates (guards/manifest.toml). Read each change, and its "
        "gate dispute in docs/disputes/ if an agent raised one, before merging.\n\n"
        "| Path | Group |\n| --- | --- |\n| `a'b\\|c` | ci |\n"
    )
    assert diff.annotations([("a%b\nc\r", group)]) == [
        "::error title=Guard path changed (ci)::a%25b%0Ac%0D"
    ]


def test_main_reports_and_fails_only_when_asked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    worktree = worktree_with_manifest(tmp_path)
    git = fake_git(worktree, "CLAUDE.md\0app/src/a.py\0", base="x")
    summary = tmp_path / "summary.md"
    summary.write_text("earlier\n", encoding="utf-8")
    argv = ["--base", "x", "--summary", str(summary), "--annotate", "--fail-if-present"]
    assert diff.main(argv, git) == 1
    assert capsys.readouterr().out == (
        "old\tCLAUDE.md\n::error title=Guard path changed (old)::CLAUDE.md\n"
    )
    written = summary.read_text(encoding="utf-8")
    assert written.startswith("earlier\n## :warning: 1 guard path")
    assert "Against the merge base with `x`." in written
    assert diff.main(["--base", "x"], git) == 0


def test_main_with_nothing_guarded(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    worktree = worktree_with_manifest(tmp_path)
    git = fake_git(worktree, "app/src/a.py\0", base_manifest=None)
    assert diff.main(["--fail-if-present", "--annotate"], git) == 0
    assert (
        capsys.readouterr().out == "no guard path changed against the merge base with origin/main\n"
    )


def test_run_git_returns_stdout_or_none(tmp_path: Path) -> None:
    version = diff.run_git(["--version"])
    assert version is not None
    assert version.startswith("git version")
    assert diff.run_git(["-C", str(tmp_path), "rev-parse", "HEAD"]) is None
    assert diff.git_in(tmp_path)(["rev-parse", "HEAD"]) is None


def test_the_default_base_is_the_first_branch_that_exists(tmp_path: Path) -> None:
    # Why: guard-diff and the lowered gate must mean the same "before"; both take the
    # candidates from gatelib and this one picks the first the repository actually has.
    answers = {("rev-parse", "--verify", "--quiet", "main^{commit}"): "abc\n"}
    assert diff.default_base(lambda args: answers.get(tuple(args)), tmp_path) == "main"
    assert diff.default_base(lambda _: None, tmp_path) == "origin/main"


def test_help_describes_the_command(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        diff.main(["--help"], lambda _: None)
    assert "\n\nList changed guard paths.\n" in capsys.readouterr().out


def test_the_module_runs_as_a_script() -> None:
    done = subprocess.run(
        [sys.executable, "-m", "ratchet_guards.diff", "--help"],
        capture_output=True,
        text=True,
        check=False,
        cwd=PLUGIN_ROOT / "hooks",
    )
    assert done.returncode == 0
    assert "--fail-if-present" in done.stdout
    assert "List changed guard paths." in done.stdout


def test_the_fixture_manifest_parses() -> None:
    assert parse(MANIFEST_TEXT).groups
