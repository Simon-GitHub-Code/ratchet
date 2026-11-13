"""The transcript audit: a tester naming a file of the product, or the harness's log, is seen."""

import json
from pathlib import Path

import pytest

import acceptance
import acceptance_audit
from tests import products


def call(name: str, given: object, cwd: Path) -> str:
    block = {"type": "tool_use", "id": "t", "name": name, "input": given}
    return json.dumps({"type": "assistant", "cwd": str(cwd), "message": {"content": [block]}})


def audited(
    lines: list[str], root: Path, capsys: pytest.CaptureFixture[str]
) -> tuple[int, list[str]]:
    transcript = root.parent / "transcript.jsonl"
    transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")
    code = acceptance.main(
        ["acceptance.py", "audit", "--transcript", str(transcript), "--root", str(root)]
    )
    return code, capsys.readouterr().out.splitlines()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return products.make(tmp_path / "product")


def test_a_tester_that_only_used_the_product_is_green(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tester = acceptance.store(root) / "save-link" / "head" / "session" / "tester"
    tester.mkdir(parents=True)
    elsewhere = root.parent
    lines = [
        "not json",
        json.dumps({"type": "summary"}),
        json.dumps({"type": "user", "message": {"content": "Try it."}}),
        call("Bash", {"command": "curl -s http://127.0.0.1:8000/ -o page.html"}, elsewhere),
        call("Write", {"file_path": str(tester / "record.json"), "content": "{}"}, elsewhere),
        call("Bash", {"command": f"ls {tester}"}, elsewhere),
        call("Bash", {"command": "echo 'unbalanced"}, root),
        call("Bash", {"command": "ls --all ~"}, root),
        call("Grep", {"pattern": "x", "path": str(elsewhere)}, root),
        call("Grep", {"pattern": "x"}, elsewhere),
        call("Bash", {"command": "cat transcript.jsonl"}, elsewhere),
        call("Notes", {"paths": [str(elsewhere / "notes.md")], "limit": 5}, elsewhere),
    ]
    assert audited(lines, root, capsys) == (
        0,
        ["acceptance audit: green, 9 tool calls and none inside the product"],
    )


def test_every_way_of_naming_a_file_of_the_product_is_flagged(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    session = acceptance.store(root) / "save-link" / "head" / "session"
    (session / "tester").mkdir(parents=True)
    log = session / "requests.jsonl"
    (session / "seal.json").write_text("{}", encoding="utf-8")
    settings = root / "src" / "settings.py"
    lines = [
        call("Read", {"file_path": str(settings)}, root.parent),
        call("Bash", {"command": "cat src/settings.py"}, root),
        call("Bash", {"command": f"cd {root.name} && grep -r PORT src/*.py"}, root.parent),
        call("WebFetch", {"url": f"file://{settings}"}, root.parent),
        call("Grep", {"pattern": "PORT"}, root / "src"),
        call("Edit", {"file_path": str(log), "old_string": "303", "new_string": "200"}, root),
        call("Bash", {"command": "cat ../seal.json"}, session / "tester"),
    ]
    code, out = audited(lines, root, capsys)
    assert code == 1
    assert out == [
        f"line 1, Read, names {settings}, inside the product",
        f"line 2, Bash, names {settings}, inside the product",
        f"line 3, Bash, names {settings}, inside the product",
        f"line 4, WebFetch, names {settings}, inside the product",
        f"line 5, Grep, names {root / 'src'}, inside the product",
        f"line 6, Edit, names {log}, inside the product",
        f"line 7, Bash, names {session / 'seal.json'}, inside the product",
        "acceptance audit: red, the tester looked inside the product (7 tool calls)",
    ]


def test_the_main_checkout_of_a_work_tree_is_the_product_too(tmp_path: Path) -> None:
    main, tree = tmp_path / "main", tmp_path / "tree"
    evidence = main / ".git" / "ratchet" / "acceptance"
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(call("Read", {"file_path": str(main / "a.py")}, tmp_path) + "\n")
    assert acceptance_audit.main(transcript, tree, evidence) == 1
    elsewhere = tmp_path / "bare.git" / "ratchet" / "acceptance"
    assert acceptance_audit.main(transcript, tree, elsewhere) == 0


def test_a_file_that_is_not_a_transcript_is_never_a_clean_audit(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert audited(["{}", "[]", "nothing"], root, capsys) == (
        2,
        [f"acceptance audit: {root.parent / 'transcript.jsonl'} is not a session transcript"],
    )
    missing = root.parent / "missing.jsonl"
    code = acceptance.main(["x", "audit", "--transcript", str(missing), "--root", str(root)])
    assert code == 2
    assert capsys.readouterr().out.startswith(f"acceptance audit: cannot read {missing}:")
