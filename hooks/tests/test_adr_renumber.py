from pathlib import Path

import pytest

import adr_renumber
from tests.repos import branch, commit, git, repository, write

ADR_TEXT = "# Decision\n\n## Refutation criterion\n\nWrong if false.\n"


def test_renumbers_colliding_branch_adr_and_rewrites_only_branch_citations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = repository(
        tmp_path / "repo",
        {
            "docs/adr/0001-base.md": ADR_TEXT,
            "docs/adr/0002-theirs.md": ADR_TEXT,
            "docs/process/binding.md": (
                "- Default branch: `main`\n"
                "See ADR 0002 and docs/adr/0002-theirs.md for the baseline.\n"
            ),
            "src/rule.py": "LIMIT = 1  # ADR 0002\n",
        },
    )
    branch(root, "feat/mine")
    write(
        root,
        "docs/adr/0001-base.md",
        "---\nstatus: superseded\nsuperseded-by: 0002\n---\n" + ADR_TEXT,
    )
    write(
        root,
        "docs/adr/0002-mine.md",
        (
            "---\nstatus: superseded\nsuperseded-by: 0002\n---\n"
            "# Mine\n\nCites ADR 0002, ADR-0002, and docs/adr/0002-mine.md.\n"
            "## Refutation criterion\n\nWrong if false.\n"
        ),
    )
    write(
        root,
        "docs/process/binding.md",
        (
            "- Default branch: `main`\n"
            "See ADR 0002 and docs/adr/0002-theirs.md for the baseline.\n"
            "Branch rule: ADR 0002 via docs/adr/0002-mine.md and 0002-mine.md.\n"
        ),
    )
    write(root, "src/rule.py", "LIMIT = 1  # ADR 0002\nNEW = 2  # ADR 0002\n")
    commit(root, "feat(adr): add 0002-mine")

    code = adr_renumber.main(["adr_renumber.py", "--root", str(root), "2", "3"])
    assert code == 0

    out = capsys.readouterr().out.splitlines()
    assert out[0] == "renamed docs/adr/0002-mine.md -> docs/adr/0003-mine.md"
    assert "updated docs/adr/0001-base.md (1 line)" in out
    assert "updated docs/adr/0003-mine.md (2 lines)" in out
    assert "updated docs/process/binding.md (1 line)" in out
    assert "updated src/rule.py (1 line)" in out

    assert (root / "docs/adr/0002-theirs.md").is_file()
    assert not (root / "docs/adr/0002-mine.md").exists()
    moved = (root / "docs/adr/0003-mine.md").read_text(encoding="utf-8")
    assert "superseded-by: 0003" in moved
    assert "Cites ADR 0003, ADR-0003, and docs/adr/0003-mine.md." in moved
    assert "superseded-by: 0003" in (root / "docs/adr/0001-base.md").read_text(encoding="utf-8")

    binding_lines = (root / "docs/process/binding.md").read_text(encoding="utf-8").splitlines()
    assert binding_lines[1] == "See ADR 0002 and docs/adr/0002-theirs.md for the baseline."
    assert binding_lines[2] == "Branch rule: ADR 0003 via docs/adr/0003-mine.md and 0003-mine.md."

    rule_lines = (root / "src/rule.py").read_text(encoding="utf-8").splitlines()
    assert rule_lines == ["LIMIT = 1  # ADR 0002", "NEW = 2  # ADR 0003"]

    status = git(root, "status", "--porcelain")
    assert "0003-mine.md" in status


def test_handles_untracked_files_deletions_binary_and_no_citations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = repository(
        tmp_path / "repo",
        {
            "docs/adr/0001-base.md": ADR_TEXT,
            "docs/adr/0002-theirs.md": ADR_TEXT,
            "tracked.txt": "keep ADR 0002\ndrop me\n",
            "no_eof.txt": "old",
        },
    )
    branch(root, "feat/untracked")
    write(root, "docs/adr/0002-untracked.md", "# Untracked\n\nNo number here.\n")
    write(root, "tracked.txt", "keep ADR 0002\n")
    write(root, "no_eof.txt", "new")
    write(root, "notes.md", "First ADR 0002\nSecond 0002-untracked.md\n")
    (root / "blob.bin").write_bytes(b"\x00\x01ADR 0002\n")
    (root / "bad_utf8.dat").write_bytes(b"\xff\xfeADR 0002\n")
    (root / "link.md").symlink_to("notes.md")

    assert adr_renumber.main(["adr_renumber.py", "--root", str(root), "0002", "0003"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == [
        "renamed docs/adr/0002-untracked.md -> docs/adr/0003-untracked.md",
        "updated notes.md (2 lines)",
    ]
    assert (root / "tracked.txt").read_text(encoding="utf-8") == "keep ADR 0002\n"
    assert (root / "notes.md").read_text(encoding="utf-8") == (
        "First ADR 0003\nSecond 0003-untracked.md\n"
    )
    assert (root / "blob.bin").read_bytes() == b"\x00\x01ADR 0002\n"
    assert (root / "bad_utf8.dat").read_bytes() == b"\xff\xfeADR 0002\n"

    write(root, "docs/adr/0005-silent.md", "# Silent\n\nNothing cited.\n")
    assert adr_renumber.main(["adr_renumber.py", "--root", str(root), "0005", "0006"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "renamed docs/adr/0005-silent.md -> docs/adr/0006-silent.md",
        "no citations updated",
    ]


def test_disambiguates_with_file_and_rejects_ambiguous_or_invalid_inputs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = repository(tmp_path / "repo", {"docs/adr/0001-base.md": ADR_TEXT})
    branch(root, "feat/two")
    write(root, "docs/adr/0002-one.md", ADR_TEXT)
    write(root, "docs/adr/0002-two.md", "# Two\n\nADR 0002\n")
    commit(root, "feat(adr): add two 0002s")

    assert adr_renumber.main(["adr_renumber.py", "--root", str(root), "2", "3"]) == 1
    err = capsys.readouterr().err
    assert "0002-one.md" in err
    assert "0002-two.md" in err
    assert "--file" in err

    assert (
        adr_renumber.main(
            [
                "adr_renumber.py",
                "--root",
                str(root),
                "--file",
                "docs/adr/0002-two.md",
                "2",
                "3",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (root / "docs/adr/0002-one.md").is_file()
    assert (root / "docs/adr/0003-two.md").is_file()
    assert "ADR 0003" in (root / "docs/adr/0003-two.md").read_text(encoding="utf-8")

    assert adr_renumber.main(["adr_renumber.py", "--root", str(root), "2", "1"]) == 1
    assert "already exists" in capsys.readouterr().err

    assert adr_renumber.main(["adr_renumber.py", "--root", str(root), "99", "100"]) == 1
    assert "0099" in capsys.readouterr().err

    write(root, "outside.md", ADR_TEXT)
    for bad_file in (
        "outside.md",
        "docs/adr/0003-two.md",
        "docs/adr/0002-missing.md",
        "docs/adr/0002-bad.txt",
    ):
        assert (
            adr_renumber.main(
                ["adr_renumber.py", "--root", str(root), "--file", bad_file, "2", "4"]
            )
            == 1
        )
        capsys.readouterr()

    for bad_args in (["0", "2"], ["-1", "2"], ["abc", "2"], ["2", "0002"]):
        with pytest.raises(SystemExit) as exc:
            adr_renumber.main(["adr_renumber.py", "--root", str(root), *bad_args])
        assert exc.value.code == 2


def test_both_colliding_on_base_fail_without_file_and_rewrites_without_base(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = repository(
        tmp_path / "repo",
        {
            "docs/adr/0001-a.md": ADR_TEXT,
            "docs/adr/0001-b.md": ADR_TEXT,
        },
    )
    branch(root, "feat/check")
    assert adr_renumber.main(["adr_renumber.py", "--root", str(root), "1", "2"]) == 1
    assert "--file" in capsys.readouterr().err

    plain = tmp_path / "plain"
    write(plain, "docs/adr/0002-only.md", "# Only\r\n\r\nSee ADR 0002.\r\n")
    write(plain, "docs/adr/0004-a.md", ADR_TEXT)
    write(plain, "docs/adr/0004-b.md", ADR_TEXT)
    write(plain, "notes.txt", "Also ADR 0002.\r\n")
    write(plain, ".git/note.txt", "Keep ADR 0002.\n")
    assert adr_renumber.main(["adr_renumber.py", "--root", str(plain), "4", "5"]) == 1
    assert "--file" in capsys.readouterr().err
    assert adr_renumber.main(["adr_renumber.py", "--root", str(plain), "2", "3"]) == 0
    capsys.readouterr()
    assert (plain / "docs/adr/0003-only.md").read_bytes() == b"# Only\r\n\r\nSee ADR 0003.\r\n"
    assert (plain / "notes.txt").read_bytes() == b"Also ADR 0003.\r\n"
    assert (plain / ".git/note.txt").read_text(encoding="utf-8") == "Keep ADR 0002.\n"


def test_missing_adr_dir_wide_numbers_and_base_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert adr_renumber.main(["adr_renumber.py", "--root", str(empty), "1", "2"]) == 1
    assert "0001" in capsys.readouterr().err

    root = repository(tmp_path / "wide", {"docs/adr/10000-big.md": "# Big\n\nADR 10000\n"})
    base_sha = git(root, "rev-parse", "HEAD")
    branch(root, "feat/wide")
    write(root, "ref.md", "ADR 10000\n")
    assert (
        adr_renumber.main(
            ["adr_renumber.py", "--root", str(root), "--base", base_sha, "10000", "10001"]
        )
        == 0
    )
    capsys.readouterr()
    assert (root / "docs/adr/10001-big.md").is_file()
    assert (root / "ref.md").read_text(encoding="utf-8") == "ADR 10001\n"
