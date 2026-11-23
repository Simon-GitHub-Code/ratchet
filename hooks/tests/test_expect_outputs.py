import json
import re
from pathlib import Path

import pytest

import expect_outputs
from tests.repos import branch, commit, git, repository, write

Out = pytest.CaptureFixture[str]

TABLE = """\
# Binding

- Application types: cli
- Life events: depended-on

## Recorded outputs

| Paths | Pattern |
| --- | --- |
| `tests/__snapshots__/**` | |
| `tests/**/test_*.py` | `assert_inline\\(\\|snapshot\\(` |
| <`globs` of recorded outputs, or `none: <reason>`> | <optional regex> |
| none: no snapshots here | |
| | `ignored` |
"""


def project(tmp_path: Path, files: dict[str, str] | None = None, binding: str = TABLE) -> Path:
    root = repository(
        tmp_path,
        {"docs/process/binding.md": binding, "README.md": "# A\n", **(files or {})},
    )
    branch(root, "feature/1-expect")
    return root


def test_parse_reads_paths_and_patterns_and_skips_placeholders_none_and_bad_regex() -> None:
    text = (
        TABLE + "| `tests/golden/*.txt` | `none` |\n"
        "| `tests/golden/*.md` | `—` |\n"
        "| `tests/golden/*.csv` | `-` |\n"
        "| `tests/bad/*.py` | `(` |\n"
    )
    rules = expect_outputs.parse(text)
    assert [(r.paths, r.pattern.pattern if r.pattern else None) for r in rules] == [
        (("tests/__snapshots__/**",), None),
        (("tests/**/test_*.py",), r"assert_inline\(|snapshot\("),
        (("tests/golden/*.txt",), None),
        (("tests/golden/*.md",), None),
        (("tests/golden/*.csv",), None),
    ]
    assert expect_outputs.parse("# Binding\n") == ()


def test_declaration_reads_the_merge_base_not_the_working_tree(tmp_path: Path) -> None:
    root = project(tmp_path, {"tests/__snapshots__/out.txt": "before\n"})
    base = git(root, "rev-parse", "main").strip()
    write(root, "docs/process/binding.md", "# Binding\n")
    write(root, "tests/__snapshots__/out.txt", "after\n")
    found = expect_outputs.listing(root, base)
    assert found.altered == ("tests/__snapshots__/out.txt",)
    assert expect_outputs.declaration(tmp_path / "missing", base) == ()
    assert expect_outputs.declaration(root, "0" * 40) == ()

    no_table = repository(
        tmp_path / "no_table",
        {"docs/process/binding.md": "# Binding\n", "tests/__snapshots__/a.txt": "1\n"},
    )
    branch(no_table, "feature/2-no-table")
    no_table_base = git(no_table, "rev-parse", "main").strip()
    write(no_table, "tests/__snapshots__/a.txt", "2\n")
    assert expect_outputs.listing(no_table, no_table_base) == expect_outputs.Listing((), ())


def test_path_rules_classify_new_files_as_added_and_modified_deleted_or_renamed_as_altered(
    tmp_path: Path,
) -> None:
    root = project(
        tmp_path,
        {
            "tests/__snapshots__/keep.txt": "one\n",
            "tests/__snapshots__/drop.txt": "two\n",
            "tests/__snapshots__/move.txt": "three\n",
            "tests/__snapshots__/bin.png": "oldbin",
        },
    )
    base = git(root, "rev-parse", "main").strip()
    write(root, "tests/__snapshots__/new.txt", "fresh\n")
    write(root, "tests/__snapshots__/keep.txt", "changed\n")
    (root / "tests/__snapshots__/drop.txt").unlink()
    (root / "tests/__snapshots__/move.txt").rename(root / "tests/__snapshots__/moved.txt")
    (root / "tests/__snapshots__/bin.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x01")
    write(root, "src/app.py", "x = 1\n")

    found = expect_outputs.listing(root, base)
    assert found.added == ("tests/__snapshots__/moved.txt", "tests/__snapshots__/new.txt")
    assert found.altered == (
        "tests/__snapshots__/bin.png",
        "tests/__snapshots__/drop.txt",
        "tests/__snapshots__/keep.txt",
        "tests/__snapshots__/move.txt",
    )
    assert expect_outputs.altered(root, base) == list(found.altered)


def test_inline_pattern_distinguishes_unrelated_edits_and_additions_from_alterations(
    tmp_path: Path,
) -> None:
    root = project(
        tmp_path,
        {
            "tests/test_shifted.py": (
                "def test_a():\n    assert snapshot('keep')\n\ndef test_b():\n    assert 1 == 1\n"
            ),
            "tests/test_added.py": "def test_a():\n    assert snapshot('one')\n",
            "tests/test_edited.py": "def test_a():\n    assert snapshot('old')\n",
            "tests/test_removed.py": "def test_a():\n    assert snapshot('gone')\n",
            "tests/test_deleted_match.py": "def test_a():\n    assert snapshot('bye')\n",
            "tests/test_deleted_plain.py": "def test_a():\n    assert 1 == 1\n",
            "tests/test_binary.py": "def test_a():\n    assert snapshot('bin')\n",
        },
    )
    base = git(root, "rev-parse", "main").strip()
    write(
        root,
        "tests/test_shifted.py",
        "# shifted\n\ndef test_a():\n    assert snapshot('keep')\n\ndef test_b():\n"
        "    assert 2 == 2\n",
    )
    write(
        root,
        "tests/test_added.py",
        "def test_a():\n    assert snapshot('one')\n\ndef test_b():\n"
        "    assert assert_inline('two')\n",
    )
    write(root, "tests/test_edited.py", "def test_a():\n    assert snapshot('new')\n")
    write(root, "tests/test_removed.py", "def test_a():\n    assert 1 == 1\n")
    (root / "tests/test_deleted_match.py").unlink()
    (root / "tests/test_deleted_plain.py").unlink()
    (root / "tests/test_binary.py").write_bytes(b"\x00\xff\xfe")
    write(root, "tests/test_brand_new_with.py", "def test_n():\n    assert snapshot('hi')\n")
    write(root, "tests/test_brand_new_without.py", "def test_n():\n    assert True\n")

    found = expect_outputs.listing(root, base)
    assert found.added == ("tests/test_added.py", "tests/test_brand_new_with.py")
    assert found.altered == (
        "tests/test_binary.py",
        "tests/test_deleted_match.py",
        "tests/test_edited.py",
        "tests/test_removed.py",
    )


def test_listing_also_compares_two_commits_without_touching_working_tree(tmp_path: Path) -> None:
    root = project(tmp_path, {"tests/__snapshots__/a.txt": "v1\n"})
    base = git(root, "rev-parse", "main").strip()
    write(root, "tests/__snapshots__/a.txt", "v2\n")
    write(root, "tests/__snapshots__/b.txt", "new\n")
    commit(root, "test(expect): update and add snapshots")
    head = git(root, "rev-parse", "HEAD").strip()
    git(root, "switch", "-q", "main")
    found = expect_outputs.listing(root, base, head=head)
    assert found.added == ("tests/__snapshots__/b.txt",)
    assert found.altered == ("tests/__snapshots__/a.txt",)


def test_cli_prints_json_and_plain_summaries_and_handles_no_base(
    tmp_path: Path, capsys: Out
) -> None:
    bare = tmp_path / "bare"
    bare.mkdir()
    assert expect_outputs.main(["expect_outputs.py", "--root", str(bare), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"added": [], "altered": []}
    assert expect_outputs.main(["expect_outputs.py", "--root", str(bare)]) == 0
    assert "no commit to compare" in capsys.readouterr().out

    root = project(tmp_path / "repo", {"tests/__snapshots__/a.txt": "v1\n"})
    assert expect_outputs.main(["expect_outputs.py", "--root", str(root)]) == 0
    assert capsys.readouterr().out.strip() == "expect-outputs: none"

    write(root, "tests/__snapshots__/a.txt", "v2\n")
    write(root, "tests/__snapshots__/b.txt", "new\n")
    assert expect_outputs.main(["expect_outputs.py", "--root", str(root), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "added": ["tests/__snapshots__/b.txt"],
        "altered": ["tests/__snapshots__/a.txt"],
    }
    assert expect_outputs.main(["expect_outputs.py", "--root", str(root)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == [
        "added\ttests/__snapshots__/b.txt",
        "altered\ttests/__snapshots__/a.txt",
    ]


def test_parse_skips_empty_patterns_placeholders_and_keeps_valid_rules() -> None:
    text = (
        "## Recorded outputs\n\n"
        "| Paths | Pattern |\n"
        "| --- | --- |\n"
        "| `tests/a/**` | `<optional regex>` |\n"
        "| `tests/b/**` | |\n"
    )
    rules = expect_outputs.parse(text)
    assert len(rules) == 2
    assert all(isinstance(r, expect_outputs.Rule) for r in rules)
    assert all(r.pattern is None for r in rules)
    assert re.compile("x") is not None
