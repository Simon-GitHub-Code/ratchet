"""The tool-neutral gate scripts: the ceiling direction in ``lowered`` and the ``budget`` gate."""

import json
import sys
import tomllib
from pathlib import Path

import pytest

import adapter
import budget
import lowered
from tests.repos import branch, commit, repository, write

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


def test_a_tier_ceiling_starts_no_tighter_than_the_tier_budget() -> None:
    # Why: a trial adoption measured an empty skeleton and set tier1 to 14 seconds, so the first
    # feature's tests would have needed an ADR to "loosen" a ceiling 60 times under the budget.
    assert budget.lowered_ceiling(None, 9.0, 1.5, floor=budget.TIER_BUDGETS["tier1"]) == 900.0
    assert budget.lowered_ceiling(None, 1000.0, 1.5, floor=900.0) == 1500.0
    assert budget.lowered_ceiling(14.0, 9.0, 1.5, floor=900.0) is None, "an existing ceiling stands"
    assert budget.lowered_ceiling(1500.0, 100.0, 1.5, floor=900.0) == 900.0


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
    without_tier0 = THRESHOLDS.replace("tier0 = 60\n", "")
    (tmp_path / "quality/thresholds.toml").write_text(without_tier0, encoding="utf-8")
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
    assert record["status"] == "unbounded", "the first adoption has no ceiling yet"
    assert budget.ceiling_of((tmp_path / "quality/thresholds.toml").read_text(), "tier0") == 60.0
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


def test_codex_hooks_treats_a_shapeless_entry_as_empty() -> None:
    root = Path("/plugins/ratchet")
    shapeless = {"hooks": {"PreToolUse": ["not a dict", {"hooks": "not a list"}]}}
    merged = adapter.codex_hooks(shapeless, root)
    hooks = merged["hooks"]
    assert isinstance(hooks, dict)
    assert len(hooks["PreToolUse"]) == 3
    assert hooks["PreToolUse"][-1]["matcher"] == adapter.MATCHER


def test_adapter_main_without_apply_only_prints_the_steps(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    assert adapter.main(["adapter", "codex", "--home", str(home)]) == 0
    assert "docs/harness/" in capsys.readouterr().out
    assert not home.exists()


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


def test_lowered_main_against_a_real_repository(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = repository(tmp_path)
    assert lowered.main(["x", "--root", str(root)]) == 1
    assert capsys.readouterr().out == "quality/thresholds.toml: missing\n"
    write(root, "quality/thresholds.toml", THRESHOLDS)
    assert lowered.main(["x", "--root", str(root)]) == 0
    assert "no base commit yet" in capsys.readouterr().out
    commit(root, "chore(quality): thresholds")
    branch(root)
    write(root, "quality/thresholds.toml", THRESHOLDS.replace("90.0", "80.0"))
    assert lowered.main(["x", "--root", str(root)]) == 1
    assert "py.coverage.core.line: lowered from 90.0 to 80.0" in capsys.readouterr().out
    write(root, "quality/thresholds.toml", THRESHOLDS.replace("90.0", "95.0"))
    assert lowered.main(["x", "--root", str(root)]) == 0
    fresh = repository(tmp_path / "fresh") if (tmp_path / "fresh").mkdir() is None else root
    commit(fresh, "feat(core): second")
    write(fresh, "quality/thresholds.toml", THRESHOLDS)
    assert lowered.main(["x", "--root", str(fresh)]) == 0
    assert "is new at" in capsys.readouterr().out


def test_a_true_flag_turned_off_or_dropped_is_a_loosening() -> None:
    before = lowered.flatten({"rs": {"coverage": {"domain": {"absolute": True, "line": 100}}}})
    assert lowered.loosened(before, {**before, "rs.coverage.domain.absolute": False}) == [
        "rs.coverage.domain.absolute: turned off"
    ]
    assert lowered.loosened(before, {"rs.coverage.domain.line": 100.0}) == [
        "rs.coverage.domain.absolute: removed (was true)"
    ]
    assert lowered.loosened({"x.on": False}, {"x.on": True}) == []


def loosenings_between(old: str, new: str) -> list[str]:
    return lowered.loosened(
        lowered.flatten(tomllib.loads(old)), lowered.flatten(tomllib.loads(new))
    )


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("min = 80", "min = false", "cov.min: changed from a number to a boolean"),
        ("min = 80", "min = true", "cov.min: changed from a number to a boolean"),
        ("min = true", "min = 80", "cov.min: changed from a boolean to a number"),
        ("min = false", "min = 1", "cov.min: changed from a boolean to a number"),
    ],
)
def test_a_change_between_a_number_and_a_boolean_is_a_loosening(
    old: str, new: str, message: str
) -> None:
    assert loosenings_between(f"[cov]\n{old}", f"[cov]\n{new}") == [message]


@pytest.mark.parametrize("new", ['"eighty"', "[80]"])
def test_a_number_turned_into_a_string_or_list_reads_as_removed(new: str) -> None:
    assert loosenings_between("[cov]\nmin = 80", f"[cov]\nmin = {new}") == [
        "cov.min: removed (was 80.0)"
    ]


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
@pytest.mark.parametrize(
    ("table", "before"), [("cov", "min = 80"), ("ceilings.x", "sec = 10")], ids=["floor", "ceiling"]
)
def test_a_value_that_is_not_a_finite_number_is_a_loosening(
    table: str, before: str, value: str
) -> None:
    key = before.partition(" ")[0]
    after = f"{key} = {value}"
    assert loosenings_between(f"[{table}]\n{before}", f"[{table}]\n{after}") == [
        f"{table}.{key}: {value} is not a finite number"
    ]


ADR = """# 0007: the change ceiling rises for the settlement feature

## Decision
Relaxes: ceilings.change.lines from 1500 to 2500
- Relaxes: rs.coverage.domain.absolute from true to false
Relaxes: rs.coverage.domain.branch from 100 to removed
Relaxes: rs.coverage.domain.line from soon to later
"""


def test_a_loosening_an_adr_names_exactly_passes_and_says_which(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = "[ceilings.change]\nlines = 1500\n[rs.coverage.domain]\nabsolute = true\n"
    base += "line = 100\nbranch = 100\n"
    root = repository(tmp_path, {"quality/thresholds.toml": base})
    commit(root, "feat(core): second")
    branch(root)
    write(root, "quality/thresholds.toml", base.replace("1500", "2500").replace("true", "false"))
    assert lowered.main(["lowered", "--root", str(root)]) == 1
    write(root, "docs/adr/0007-wider.md", ADR)
    assert lowered.main(["lowered", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    relaxed = "ceiling raised from 1500.0 to 2500.0, relaxed by docs/adr/0007-wider.md"
    assert f"ceilings.change.lines: {relaxed}" in out
    write(root, "quality/thresholds.toml", base.replace("1500", "3000"))
    assert lowered.main(["lowered", "--root", str(root)]) == 1, "the ADR names 2500, not 3000"
    write(root, "quality/thresholds.toml", base.replace("line = 100\n", ""))
    assert lowered.main(["lowered", "--root", str(root)]) == 1, "no ADR names line removed"
    write(root, "quality/thresholds.toml", base.replace("branch = 100\n", ""))
    assert lowered.main(["lowered", "--root", str(root)]) == 0, "branch removed is named"
