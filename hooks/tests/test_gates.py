"""The shipped gates: markers, suppressions, pins, prose, snapshots, commits and change size."""

import json
from pathlib import Path

import pytest

import change_size
import commits
import markers
import pins
import prose
import snapshots
import suppressions
from tests.repos import branch, commit, git, repository, write

Out = pytest.CaptureFixture[str]


def run(gate: object, root: Path, *flags: str) -> int:
    main = getattr(gate, "main")  # noqa: B009 # -- the gate modules share one shape
    result = main(["gate", "--root", str(root), *flags])
    assert isinstance(result, int)
    return result


# --- markers --------------------------------------------------------------------------------


def test_markers_rise_is_red_and_paydown_is_green(tmp_path: Path, capsys: Out) -> None:
    root = repository(tmp_path, {"a.py": "x = 1  # TODO one\ny = 2  # FIXME two\n"})
    assert run(markers, root) == 0
    assert "no base commit yet; 2 marker(s)" in capsys.readouterr().out
    commit(root, "feat(core): second")
    branch(root)
    write(root, "a.py", "x = 1  # TODO one\n")
    write(root, "b.py", "z = 3  # HACK untracked counts too\n")
    assert run(markers, root) == 1
    assert "HACK: rose from 0 to 1" in capsys.readouterr().out
    (root / "b.py").unlink()
    assert run(markers, root) == 0
    assert markers.counts(root, None) == {"TODO": 1, "FIXME": 0, "HACK": 0, "XXX": 0}
    assert markers.grep_count(root, "TODOS", None) == 0, "whole words only"


# --- suppressions ---------------------------------------------------------------------------


def test_suppressions_count_per_kind_and_a_focus_marker_fails(tmp_path: Path, capsys: Out) -> None:
    root = repository(tmp_path, {"a.py": "x = 1  # noqa: E501\n"})
    commit(root, "feat(core): second")
    branch(root)
    assert run(suppressions, root) == 0
    write(root, "a.py", "x = 1  # noqa: E501\ny = 2  # type: ignore[misc]\n")
    assert run(suppressions, root) == 1
    assert "type-ignore: rose from 0 to 1" in capsys.readouterr().out
    write(root, "a.py", "x = 1  # noqa: E501\n")
    write(root, "a.test.ts", "describe.only('x', () => {})\n")
    assert run(suppressions, root) == 1
    assert "focus marker at a.test.ts:1:" in capsys.readouterr().out
    assert suppressions.focused(tmp_path / "nowhere") == []


def test_suppressions_without_a_base_only_look_for_focus(tmp_path: Path) -> None:
    root = repository(tmp_path, {"a.py": "x = 1  # noqa\n"})
    assert run(suppressions, root) == 0


# --- pins -----------------------------------------------------------------------------------

PINNED = "actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09 # v5"


def test_pins_check_actions_images_and_permissions(tmp_path: Path) -> None:
    root = repository(
        tmp_path,
        {
            ".github/workflows/ci.yml": (
                "name: ci\npermissions:\n  contents: read\njobs:\n  a:\n    steps:\n"
                f"      - uses: {PINNED}\n      - uses: ./local\n"
                "      - uses: docker://alpine@sha256:0000\n"
                "    container:\n      image: alpine@sha256:0000\n"
            ),
            "Dockerfile": (
                "FROM python:3.13@sha256:abc AS build\nRUN make\nFROM build\nFROM scratch\n"
                "FROM $BASE\n"
            ),
            "compose.yml": "services:\n  db:\n    image: postgres@sha256:abc\n",
            "notes.md": "uses: actions/checkout@v4 is prose, not a workflow\n",
        },
    )
    assert pins.problems(root) == []
    write(
        root,
        ".github/workflows/deep.yml",
        "name: deep\njobs:\n  a:\n    steps:\n      - uses: actions/checkout@v4\n"
        "      - uses: actions/cache@0057852bfaa89a56745cba8c7296529d2fc39830\n"
        "      - uses: docker://alpine:3\n    container:\n      image: alpine:3\n",
    )
    write(root, "Dockerfile.dev", "FROM python:3.13\n")
    write(root, "docker-compose.yaml", "services:\n  db:\n    image: postgres:16\n")
    assert pins.problems(root) == [
        ".github/workflows/deep.yml: no top-level permissions block",
        ".github/workflows/deep.yml:5: action actions/checkout@v4 is not pinned to a commit",
        ".github/workflows/deep.yml:6: pinned action lacks a version comment",
        ".github/workflows/deep.yml:7: image docker://alpine:3 is not pinned by digest",
        ".github/workflows/deep.yml:9: image alpine:3 is not pinned by digest",
        "Dockerfile.dev:1: FROM python:3.13 is not pinned by digest",
        "docker-compose.yaml:3: image postgres:16 is not pinned by digest",
    ]
    assert run(pins, root) == 1
    (root / "bin.yml").write_bytes(b"\x00")
    write(root, ".github/workflows/bin.yml", "\x00")  # unreadable, skipped
    assert len(pins.problems(root)) == 7


# --- prose ----------------------------------------------------------------------------------

GLOSSARY = (
    "# Glossary\n\n## Language\n\n**Customer**:\nWho buys.\n_Avoid_: client, `buyer`, <synonym>\n\n"
    "**Order**:\nWhat they buy.\n_Avoid_: purchase order\n"
)


def test_prose_finds_avoided_words_in_documents_and_comments(tmp_path: Path) -> None:
    root = repository(
        tmp_path,
        {
            "GLOSSARY.md": GLOSSARY,
            "docs/a.md": "The client and the Buyer.\nA purchase order too.\nclients are fine.\n",
            "src/a.py": "client = 1  # the client wins\nx = 2  # glossary: allow, the client API\n",
            "src/b.ts": "// Buyer here\nconst buyer = 1\n",
            "src/c.bin": "client\n",
        },
    )
    assert prose.avoided(GLOSSARY) == {
        "client": "Customer",
        "buyer": "Customer",
        "purchase order": "Order",
    }
    assert prose.problems(root) == [
        "docs/a.md:1: 'client' is on the Avoid list of 'Customer'",
        "docs/a.md:1: 'Buyer' is on the Avoid list of 'Customer'",
        "docs/a.md:2: 'purchase order' is on the Avoid list of 'Order'",
        "src/a.py:1: 'client' is on the Avoid list of 'Customer'",
        "src/b.ts:1: 'Buyer' is on the Avoid list of 'Customer'",
    ]
    assert run(prose, root) == 1


def test_prose_without_a_glossary_or_without_avoid_lists(tmp_path: Path) -> None:
    root = repository(tmp_path, {"docs/a.md": "client\n"})
    assert prose.problems(root) == ["GLOSSARY.md: missing"]
    write(root, "GLOSSARY.md", "# Glossary\n\n**Customer**:\nWho buys.\n")
    assert prose.problems(root) == []
    (root / "src").mkdir()
    (root / "src/a.py").write_bytes(b"\x00 client")
    write(root, "GLOSSARY.md", GLOSSARY)
    assert prose.problems(root) == ["docs/a.md:1: 'client' is on the Avoid list of 'Customer'"]
    assert run(prose, root) == 1


# --- snapshots ------------------------------------------------------------------------------

MANIFEST = (
    '[[group]]\nname = "tool-config"\nreason = "Rule sets."\n'
    'paths = ["lint.toml", "cfg/*.json", "~/.tool.toml"]\n'
)


def test_snapshots_write_then_check_and_report_every_drift(tmp_path: Path, capsys: Out) -> None:
    root = repository(
        tmp_path,
        {"guards/manifest.toml": MANIFEST, "lint.toml": "a = 1\n", "cfg/x.json": "{}\n"},
    )
    assert run(snapshots, root, "--write") == 0
    assert sorted(p.name for p in (root / "quality/snapshots").iterdir()) == [
        "cfg__x.json",
        "lint.toml",
    ]
    assert run(snapshots, root, "--check") == 0
    write(root, "lint.toml", "a = 2\n")
    write(root, "cfg/y.json", "[]\n")
    (root / "quality/snapshots/stale.toml").write_text("old\n")
    capsys.readouterr()
    assert run(snapshots, root, "--check") == 1
    differs = "differs from its config; run `just snapshots` and review the diff"
    assert capsys.readouterr().out.splitlines()[:3] == [
        "quality/snapshots/cfg__y.json: missing; run `just snapshots`",
        f"quality/snapshots/lint.toml: {differs}",
        "quality/snapshots/stale.toml: stale; its config is gone",
    ]
    assert run(snapshots, root, "--write") == 0
    assert not (root / "quality/snapshots/stale.toml").exists()
    assert run(snapshots, root, "--check") == 0


def test_snapshots_need_the_group(tmp_path: Path) -> None:
    root = repository(tmp_path)
    assert snapshots.write(root, "tool-config") == [
        "guards/manifest.toml: no group named 'tool-config'"
    ]
    write(root, "guards/manifest.toml", '[[group]]\nname = "other"\nreason = "r"\npaths = ["x"]\n')
    assert snapshots.check(root, "tool-config") == [
        "guards/manifest.toml: no group named 'tool-config'"
    ]
    write(root, "guards/manifest.toml", "group = 3\n")
    assert snapshots.patterns(root, "tool-config") is None
    write(
        root, "guards/manifest.toml", '[[group]]\nname = "tool-config"\nreason = "r"\npaths = 1\n'
    )
    assert snapshots.patterns(root, "tool-config") == []


# --- commits --------------------------------------------------------------------------------


def test_commit_subjects_follow_the_convention(tmp_path: Path, capsys: Out) -> None:
    assert (
        commits.problems(["feat(core): add", "fix!: break", "chore(method): adopt the method"])
        == []
    )
    long = "feat: " + "x" * 70
    assert commits.problems(["Fixed stuff.", long]) == [
        "'Fixed stuff.': not `type(scope): summary` with a type in " + ", ".join(commits.TYPES),
        "'Fixed stuff.': ends with a full stop",
        f"'{long[:30]}...': 76 characters, at most 72",
    ]
    root = repository(tmp_path)
    assert run(commits, root) == 0, "the single clean commit"
    branch(root)
    commit(root, "wip")
    assert run(commits, root) == 1
    assert "'wip': not" in capsys.readouterr().out
    (tmp_path / "bad").mkdir()
    bad = repository(tmp_path / "bad")
    git(bad, "commit", "-q", "--amend", "-m", "nope")
    assert run(commits, bad) == 1


# --- change size ----------------------------------------------------------------------------


def test_change_size_measures_against_the_ceiling(tmp_path: Path, capsys: Out) -> None:
    root = repository(tmp_path, {"a.txt": "1\n2\n", "bin": "x"})
    assert run(change_size, root) == 0
    assert "no base commit yet" in capsys.readouterr().out
    commit(root, "feat(core): second")
    branch(root)
    write(root, "a.txt", "1\n3\n4\n")
    write(root, "new.txt", "n\n")
    (root / "bin").write_bytes(b"\x00\x01")
    (root / "blob").write_bytes(b"\x00\x01")
    assert change_size.measure(root, "main") == (4, 4), "binaries count as files, not lines"
    assert run(change_size, root) == 0
    assert "no ceiling recorded" in capsys.readouterr().out
    assert run(change_size, root, "--set", "--files", "2", "--lines", "10") == 0
    assert run(change_size, root, "--set", "--files", "3", "--lines", "10") == 0
    assert change_size.ceilings(root) == (3, 10)
    assert run(change_size, root) == 1
    assert "files: 5 changed, ceiling 3" in capsys.readouterr().out, "the new file counts"
    commit(root, "chore(method): adopt the ratchet method")
    assert run(change_size, root) == 0
    assert "exempt" in capsys.readouterr().out
    commit(root, "feat(core): more")
    assert run(change_size, root) == 1
    with pytest.raises(SystemExit):
        run(change_size, root, "--set")


def test_change_size_ceilings_are_rewritten_in_place() -> None:
    before = "[ceilings.change]\nfiles = 1\nlines = 2\n"
    after = "[ceilings.change]\nfiles = 3\nlines = 4\n"
    text = f"[python.coverage.core]\nline = 90.0\n\n{before}\n[x]\ny = 1\n"
    assert change_size.with_ceilings(text, 3, 4) == (
        f"[python.coverage.core]\nline = 90.0\n\n{after}\n[x]\ny = 1\n"
    )
    assert change_size.with_ceilings("", 3, 4) == "[ceilings.change]\nfiles = 3\nlines = 4\n"
    assert change_size.with_ceilings("[x]\ny = 1\n", 3, 4) == (
        "[x]\ny = 1\n\n[ceilings.change]\nfiles = 3\nlines = 4\n"
    )
    assert change_size.over((2, 2), (None, 1)) == ["lines: 2 changed, ceiling 1"]


def test_change_size_ignores_non_integer_ceilings(tmp_path: Path) -> None:
    write(tmp_path, "quality/thresholds.toml", "[ceilings.change]\nfiles = true\nlines = 2.5\n")
    assert change_size.ceilings(tmp_path) == (None, None)
    assert json.dumps(change_size.ceilings(tmp_path)) == "[null, null]"
