"""The tool-neutral gate scripts: the ceiling direction in ``lowered`` and the ``budget`` gate."""

import json
import sys
from pathlib import Path

import pytest

import adapter
import budget
import lowered

THRESHOLDS = """# thresholds
[py.coverage.core]
line = 90.0

[ceilings.seconds]
tier0 = 60
tier1 = 600
"""


def test_thresholds_only_rise_and_ceilings_only_fall() -> None:
    before = lowered.flatten(
        {"py": {"coverage": {"line": 90.0}}, "ceilings": {"seconds": {"tier1": 600}}}
    )
    same = lowered.loosened(before, before)
    assert same == []
    tighter = {"py.coverage.line": 95.0, "ceilings.seconds.tier1": 500.0}
    assert lowered.loosened(before, tighter) == []
    looser = {"py.coverage.line": 80.0, "ceilings.seconds.tier1": 700.0}
    assert lowered.loosened(before, looser) == [
        "ceilings.seconds.tier1: ceiling raised from 600.0 to 700.0",
        "py.coverage.line: lowered from 90.0 to 80.0",
    ]
    assert lowered.loosened(before, {}) == [
        "ceilings.seconds.tier1: removed (was 600.0)",
        "py.coverage.line: removed (was 90.0)",
    ]
    assert lowered.lowered(before, looser) == lowered.loosened(before, looser)


def test_ceiling_is_read_from_the_ceilings_table() -> None:
    assert budget.ceiling_of(THRESHOLDS, "tier1") == 600.0
    assert budget.ceiling_of(THRESHOLDS, "tier2") is None
    assert budget.ceiling_of("", "tier0") is None
    assert budget.ceiling_of("[ceilings.seconds]\ntier0 = true\n", "tier0") is None
    assert budget.ceiling_of("[ceilings]\nseconds = 3\n", "tier0") is None


def test_with_ceiling_adds_replaces_and_keeps_other_tables() -> None:
    added = budget.with_ceiling("[py.coverage.core]\nline = 90.0\n", "tier0", 90.0)
    assert added == "[py.coverage.core]\nline = 90.0\n\n[ceilings.seconds]\ntier0 = 90\n"
    assert budget.with_ceiling("", "tier0", 90.0) == "[ceilings.seconds]\ntier0 = 90\n"
    replaced = budget.with_ceiling(THRESHOLDS, "tier1", 450.0)
    assert budget.ceiling_of(replaced, "tier1") == 450.0
    assert budget.ceiling_of(replaced, "tier0") == 60.0
    extended = budget.with_ceiling(THRESHOLDS + "\n[py.mutation]\nscore = 1.0\n", "tier2", 3000.0)
    assert budget.ceiling_of(extended, "tier2") == 3000.0
    assert budget.ceiling_of(extended, "tier0") == 60.0
    assert lowered.flatten(__import__("tomllib").loads(extended))["py.mutation.score"] == 1.0


def test_a_ceiling_is_only_ever_lowered() -> None:
    assert budget.lowered_ceiling(None, 100.0, 1.5) == 150.0
    assert budget.lowered_ceiling(200.0, 100.0, 1.5) == 150.0
    assert budget.lowered_ceiling(150.0, 100.0, 1.5) is None
    assert budget.lowered_ceiling(140.0, 100.0, 1.5) is None
    assert budget.lowered_ceiling(None, 0.4, 1.5) == 1.0


@pytest.mark.parametrize(
    ("outcome", "status", "verdict"),
    [
        (budget.Outcome("tier0", 10.0, 60.0, 0), "green", 0),
        (budget.Outcome("tier0", 61.0, 60.0, 0), "red", 1),
        (budget.Outcome("tier0", 61.0, None, 0), "unbounded", 0),
        (budget.Outcome("tier0", 10.0, 60.0, 3), "failed", 3),
        (budget.Outcome("tier0", 61.0, 60.0, 3), "failed", 3),
    ],
)
def test_outcome_status_and_verdict(outcome: budget.Outcome, status: str, verdict: int) -> None:
    assert outcome.status == status
    assert outcome.verdict == verdict
    assert outcome.line().endswith(status)


def test_main_times_a_command_reports_and_sets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "quality").mkdir()
    (tmp_path / "quality/thresholds.toml").write_text(THRESHOLDS, encoding="utf-8")
    argv = [
        "budget",
        "tier0",
        "--root",
        str(tmp_path),
        "--report",
        "timing/tier0.jsonl",
        "--set",
        "--",
        sys.executable,
        "-c",
        "pass",
    ]
    assert budget.main(argv) == 0
    out = capsys.readouterr().out
    assert "ratchet-budget: tier0 took" in out
    assert "ceilings.seconds.tier0 set to" in out
    record = json.loads((tmp_path / "timing/tier0.jsonl").read_text().splitlines()[0])
    assert record["key"] == "tier0"
    assert record["status"] == "green"
    assert budget.ceiling_of((tmp_path / "quality/thresholds.toml").read_text(), "tier0") < 60.0
    failing = [
        "budget",
        "tier0",
        "--root",
        str(tmp_path),
        "--",
        sys.executable,
        "-c",
        "raise SystemExit(4)",
    ]
    assert budget.main(failing) == 4
    over = budget.with_ceiling("", "tiny", 0.0)
    (tmp_path / "quality/thresholds.toml").write_text(over, encoding="utf-8")
    assert (
        budget.main(["budget", "tiny", "--root", str(tmp_path), "--", sys.executable, "-c", "pass"])
        == 1
    )


def test_parse_requires_the_separator_and_a_command() -> None:
    with pytest.raises(SystemExit):
        budget.parse(["budget", "tier0"])
    assert budget.main(["budget", "tier0", "--"]) == 2


def test_codex_hooks_merge_once_and_keep_what_is_there() -> None:
    root = Path("/plugins/ratchet")
    merged = adapter.codex_hooks({"hooks": {"SessionStart": [{"hooks": [{"command": "x"}]}]}}, root)
    hooks = merged["hooks"]
    assert isinstance(hooks, dict)
    assert len(hooks["SessionStart"]) == 2
    assert hooks["PreToolUse"][0]["matcher"] == adapter.MATCHER
    assert "/plugins/ratchet/hooks/guard.py" in hooks["PreToolUse"][0]["hooks"][0]["command"]
    again = adapter.codex_hooks(merged, root)
    assert again == merged
    assert adapter.codex_hooks({"other": 1}, root)["other"] == 1


def test_opencode_plugin_fills_the_root_and_adapter_writes_files(tmp_path: Path) -> None:
    template = (adapter.PLUGIN_ROOT / adapter.OPENCODE_TEMPLATE).read_text(encoding="utf-8")
    assert adapter.PLACEHOLDER in template
    filled = adapter.opencode_plugin(template, Path("/p/ratchet"))
    assert adapter.PLACEHOLDER not in filled
    assert '"/p/ratchet"' in filled
    home = tmp_path / "home"
    assert adapter.main(["adapter", "opencode", "--home", str(home), "--apply"]) == 0
    written = (home / adapter.OPENCODE_PLUGIN).read_text(encoding="utf-8")
    assert adapter.PLUGIN_ROOT.as_posix() in written
    assert adapter.main(["adapter", "codex", "--home", str(home), "--apply"]) == 0
    hooks = json.loads((home / adapter.CODEX_HOOKS).read_text(encoding="utf-8"))["hooks"]
    assert set(hooks) == {"PreToolUse", "SessionStart"}
    for harness in ("claude", "grok", "pi"):
        assert adapter.main(["adapter", harness, "--home", str(home), "--apply"]) == 0
        assert adapter.steps(harness, Path("/p"), home)[-1].startswith("see docs/harness/")
