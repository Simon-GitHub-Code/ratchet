"""The two hook shims in this process: what each does when Python or the guard cannot run."""

import io
import json
import sys
from pathlib import Path

import pytest

import guard
import session

OLD_PYTHON = (3, 10, 0)


@pytest.fixture(autouse=True)
def path_is_restored(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shims put the hooks directory first on ``sys.path``; a test must not keep that."""
    monkeypatch.setattr(sys, "path", list(sys.path))


def stdin(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


def reason(out: str) -> tuple[str, str]:
    answer = json.loads(out)["hookSpecificOutput"]
    return answer["permissionDecision"], answer["permissionDecisionReason"]


def test_the_guard_asks_when_python_is_too_old(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stdin(monkeypatch, "{}")
    monkeypatch.setattr(sys, "version_info", OLD_PYTHON)
    assert guard.main() == 0
    decision, why = reason(capsys.readouterr().out)
    assert decision == "ask"
    assert why.endswith(", 3.11+ needed.")
    assert why.startswith("Ratchet guard could not run: python3 is ")


def test_the_guard_asks_when_the_hook_module_cannot_be_imported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stdin(monkeypatch, "{}")
    monkeypatch.setitem(sys.modules, "ratchet_guards.hook", None)
    assert guard.main() == 0
    decision, why = reason(capsys.readouterr().out)
    assert decision == "ask"
    assert why.startswith("Ratchet guard could not run: ModuleNotFoundError: ")


def test_the_guard_root_is_the_payload_cwd_then_the_project_directory_then_the_default() -> None:
    default = Path("/default")
    assert guard.root_of('{"cwd": "/a"}', {"CLAUDE_PROJECT_DIR": "/p"}, default) == Path("/a")
    assert guard.root_of('{"cwd": ""}', {"CLAUDE_PROJECT_DIR": "/p"}, default) == Path("/p")
    assert guard.root_of("[1]", {"CLAUDE_PROJECT_DIR": "/p"}, default) == Path("/p")
    assert guard.root_of("not json", {}, default) == default


def test_the_session_hook_reads_the_event_name_and_nothing_else() -> None:
    assert session.event_of('{"hook_event_name": "SubagentStart"}') == "SubagentStart"
    assert session.event_of('{"hook_event_name": 3}') == ""
    assert session.event_of("[]") == ""
    assert session.event_of("{not json") == ""


def test_the_session_hook_says_nothing_when_python_is_too_old(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stdin(monkeypatch, "{}")
    monkeypatch.setattr(sys, "version_info", OLD_PYTHON)
    assert session.main() == 0
    assert capsys.readouterr().out == ""


def test_the_session_hook_says_nothing_when_the_session_module_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    stdin(monkeypatch, json.dumps({"cwd": str(tmp_path)}))
    monkeypatch.setitem(sys.modules, "ratchet_guards.session", None)
    assert session.main() == 0
    assert capsys.readouterr().out == ""
