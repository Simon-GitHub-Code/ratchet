import atexit
import functools
import json
import shutil
import tempfile
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

import conformance
import owed
import plugin_pin
import upgrade
from tests import adopted
from tests.repos import branch, commit, git, repository
from tests.test_owed import (
    COMMENT,
    DIFF_COVERAGE,
    LEDGER,
    OWN_RULE,
    VERSION,
    add,
    drop_row,
    entry,
    fill_lister,
    gate,
    merged_upgrade,
)

FIRST, SECOND, LATER = "998.0.0", "999.0.0", "1000.0.0"

Out = pytest.CaptureFixture[str]


@functools.cache
def plugins() -> dict[str, Path]:
    scratch = Path(tempfile.mkdtemp(prefix="ratchet-rerun-plugins-"))
    atexit.register(shutil.rmtree, scratch, ignore_errors=True)
    return {
        version: repository(
            scratch / version, {".claude-plugin/plugin.json": json.dumps({"version": version})}
        )
        for version in (FIRST, SECOND, LATER)
    }


def upgraded(root: Path, version: str) -> upgrade.Plan:
    result = upgrade.plan(root, plugins()[version])
    assert isinstance(result, upgrade.Plan), result
    upgrade.apply(root, result.changes)
    commit(root, f"chore(method): upgrade the Ratchet plugin to {version}")
    return result


def scratch(name: str) -> Path:
    place = Path(tempfile.mkdtemp(prefix=f"ratchet-rerun-{name}-"))
    atexit.register(shutil.rmtree, place, ignore_errors=True)
    return place / "repo"


@functools.cache
def upgrades(source: str, *versions: str) -> Path:
    base = adopted.adopted("0.9.2") if source == "0.9.2" else held_base()
    root = adopted.copy(base, scratch(f"{source}-{'-'.join(versions)}"))
    branch(root, "rerun")
    for version in versions:
        upgraded(root, version)
    return root


@functools.cache
def held_base() -> Path:
    root = adopted.copy(merged_upgrade(), scratch("held"))
    (root / LEDGER).write_text(owed.render(owed.read(root).entries[1:]), encoding="utf-8")
    commit(root, "chore(method): drop an owed entry by hand")
    return root


@pytest.fixture
def rerun(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(plugin_pin, "PLUGIN", plugins()[SECOND])
    return adopted.copy(upgrades("0.9.2", FIRST, SECOND), tmp_path / "repo")


def lines(out: str, part: str) -> list[str]:
    return [line for line in out.splitlines() if part in line]


@pytest.mark.parametrize("source", ["0.9.2", "held"])
def test_an_upgrade_run_again_on_its_open_branch_owes_what_one_upgrade_from_the_base_owes(
    source: str, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    twice, once = upgrades(source, FIRST, SECOND), upgrades(source, SECOND)
    assert (twice / LEDGER).read_text(encoding="utf-8") == (once / LEDGER).read_text(
        encoding="utf-8"
    ), "two upgrades on one open branch owe what one upgrade from the base owes"
    assert owed.read(twice).entries, "the upgrade owes something to compare"
    monkeypatch.setattr(plugin_pin, "PLUGIN", plugins()[SECOND])
    for root in (twice, once):
        code, out = gate(root, capsys)
        assert code == 0, out
    again = upgrade.plan(twice, plugins()[SECOND])
    assert isinstance(again, upgrade.Plan)
    assert again.changes == {}, "a third run changes nothing"


def test_an_entry_the_merge_base_holds_keeps_its_since_through_a_rerun() -> None:
    base = owed.read(held_base()).entries
    entries = owed.read(upgrades("held", FIRST, SECOND)).entries
    assert set(base) < set(entries)
    assert {e.since for e in base} == {VERSION}
    assert [e.since for e in entries if e not in base] == [SECOND]


def test_a_ledger_that_does_not_read_is_still_red_after_a_rerun(rerun: Path, capsys: Out) -> None:
    assert gate(rerun, capsys)[0] == 0
    (rerun / LEDGER).write_text("[[owed\n", encoding="utf-8")
    code, out = gate(rerun, capsys)
    assert code == 1
    assert f"{LEDGER}: does not parse" in out


def test_a_comment_entry_the_change_may_not_record_is_still_red_after_a_rerun(
    rerun: Path, capsys: Out
) -> None:
    assert gate(rerun, capsys)[0] == 0
    add(rerun, replace(COMMENT, since=SECOND))
    code, out = gate(rerun, capsys)
    assert code == 1
    assert "only the adoption and the change that first fills the lister record what" in out


def test_a_comment_entry_dated_before_the_lister_s_pin_is_still_red_after_a_rerun(
    rerun: Path, capsys: Out
) -> None:
    fill_lister(rerun)
    add(rerun, replace(COMMENT, since=SECOND))
    assert gate(rerun, capsys)[0] == 0
    add(rerun, replace(COMMENT, problem="backdated", since=FIRST))
    code, out = gate(rerun, capsys)
    assert code == 1
    assert (
        f"adds 'backdated' for src/a.txt (comments) since '{FIRST}'; the change first fills the"
        f" lister under the pin {SECOND}"
    ) in out


def test_an_entry_dated_before_the_pin_the_change_moves_to_is_still_red_after_a_rerun(
    rerun: Path, capsys: Out
) -> None:
    assert gate(rerun, capsys)[0] == 0
    add(rerun, entry(since=FIRST, problem="backdated"))
    code, out = gate(rerun, capsys)
    assert code == 1
    assert (
        f"since '{FIRST}'; the change moves the plugin pin to {SECOND}, so a new entry is owed"
        f" since {SECOND}"
    ) in out


def test_a_pin_older_than_the_plugin_in_use_is_still_red_after_a_rerun(
    rerun: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    monkeypatch.setattr(plugin_pin, "PLUGIN", plugins()[LATER])
    code, out = gate(rerun, capsys)
    assert code == 1
    assert f"{SECOND} is older than the plugin in use, {LATER}" in out


def test_growth_without_a_pin_move_is_still_red_after_a_rerun(rerun: Path, capsys: Out) -> None:
    git(rerun, "checkout", "-q", "main")
    git(rerun, "merge", "-q", "--ff-only", "rerun")
    branch(rerun, "change")
    written = own_entry(rerun, drop_row(rerun, DIFF_COVERAGE), SECOND)
    upgraded(rerun, SECOND)
    assert written in owed.read(rerun).entries
    code, out = gate(rerun, capsys)
    assert code == 1
    assert "without moving the plugin pin to a newer version" in out


def own_entry(root: Path, part: str, since: str) -> owed.Entry:
    [finding] = [f for f in conformance.findings(root) if part in f.problem]
    written = owed.Entry(finding.path, finding.rule, finding.key()[2], since)
    add(root, written)
    return written


def own_settings_rule(root: Path) -> None:
    path = root / ".claude/settings.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("permissions", {}).setdefault("ask", []).append(OWN_RULE)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def test_an_entry_the_base_does_not_owe_is_still_red_after_a_rerun_and_a_rerun_drops_it(
    rerun: Path, capsys: Out
) -> None:
    dropped = drop_row(rerun, DIFF_COVERAGE)
    written = own_entry(rerun, dropped, SECOND)
    code, out = gate(rerun, capsys)
    assert code == 1
    [line] = lines(out, "which the upgrade's own plan does not leave red on the merge base")
    assert f"adds '{written.problem}' for {written.path}" in line
    add(rerun, replace(written, since=FIRST))
    upgraded(rerun, SECOND)
    assert written.key not in {e.key for e in owed.read(rerun).entries}


def test_an_entry_another_gate_judges_is_still_red_after_a_rerun_and_a_rerun_drops_it(
    rerun: Path, capsys: Out
) -> None:
    own_settings_rule(rerun)
    written = own_entry(rerun, OWN_RULE, SECOND)
    assert owed.never(written.key)
    code, out = gate(rerun, capsys)
    assert code == 1
    [line] = lines(out, "which the upgrade's own plan does not leave red on the merge base")
    assert f"for {written.path} (settings)" in line
    upgraded(rerun, SECOND)
    assert written.key not in {e.key for e in owed.read(rerun).entries}


def test_the_entries_a_change_brings_are_those_the_gate_judges() -> None:
    kept = entry(path="a.toml")
    moved = entry(path="old.toml", problem="moved")
    redated = entry(path="b.toml", since=VERSION)
    new = entry(path="c.toml")
    before = [kept, moved, entry(path="b.toml")]
    after = [kept, replace(moved, path="new.toml"), redated, new]
    renamed = {"old.toml": "new.toml"}
    brought = owed.brought(before, after, renamed)
    assert brought == Counter([redated, new])
    judged = owed.grown(
        owed.Ledger(tuple(before), ()),
        owed.Ledger(tuple(after), ()),
        binding=(None, None),
        floor=None,
        exempt=(None, renamed),
    )
    assert sorted(line.split(" (")[0] for line in judged) == sorted(
        f"{LEDGER}: adds or changes '{e.problem}' for {e.path}" for e in brought.elements()
    )
