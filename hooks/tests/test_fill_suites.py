"""The suites table is filled first: no feature and no drain starts until /ratchet:fill wrote it."""

import tempfile
from pathlib import Path

import pytest

import conformance
import drain
import hooks_installed
import justfile_text
import owed
import upgrade
from tests import repos
from tests.repos import install_hooks, repository, snapshot, write
from tests.test_conformance import BINDING
from tests.test_drain import ALPHA, project
from tests.test_drain import run as drain_run
from tests.test_feature_preflight import checks, gh_returning, issue
from tests.test_feature_preflight import run as preflight_run
from tests.test_upgrade import fixture

LEDGER = "quality/owed.toml"
BITE_OWED = (
    "quality/bites.toml: gate 'tests-changed' (changed tests run) is bound to a tool and no"
    " [[bite]] plants a fault for it"
)
BITE = (
    '\n[[bite]]\ngate = "tests-changed"\nname = "a failing test"\n'
    'write = { path = "tests/_bite.txt", content = "x" }\nexpect = "red"\n'
)
SUITES = (
    "| Suite | Gate | Files | Run files |\n| --- | --- | --- | --- |\n"
    f"{conformance.SELF_TEST_SUITES}\n\n"
)


def upgraded(tmp_path: Path) -> Path:
    root = fixture("0.12.3", tmp_path)
    repos.branch(root, "upgrade")
    assert upgrade.main(["upgrade.py", "--root", str(root), "--apply"]) == 0
    return root


def owing(root: Path) -> list[owed.Entry]:
    return [entry for entry in owed.read(root).entries if entry.rule == "changed_tests"]


def test_the_owed_entry_for_the_suites_table_names_all_three_edits(tmp_path: Path) -> None:
    [entry] = owing(upgraded(tmp_path))
    assert entry.problem == (
        "'## Tests' lacks its suites table with the columns Suite | Gate | Files | Run files;"
        " one fill writes three edits together: the template's table, one row per suite with the"
        " gate that runs it whole, the globs of its test files and the recipe that runs given"
        " files; the Gates row 'changed tests run' bound to `just tests-changed`; and"
        " `tests-changed` in the justfile's `tier1_gates`. Write none alone: a Gates row bound"
        " before its table exists turns tier 1 red (ADR 0073)"
    )
    assert entry.rule in owed.FILL_FIRST


def test_a_feature_is_refused_while_the_suites_table_is_owed_and_goes_ahead_once_filled(
    tmp_path: Path,
) -> None:
    entries = owing(upgraded(tmp_path / "up"))
    root = repository(tmp_path / "feature", {BINDING: "# Binding\n"})
    install_hooks(root)
    write(root, LEDGER, owed.render(entries))
    snapshot(root, "chore: the upgrade")
    report = preflight_run(root, "4", gh_returning(issue(labels=("ready-for-agent",))))
    assert checks(report) == ["fill"]
    assert report.failures[0].produce == "/ratchet:fill"
    assert "(changed_tests)" in report.failures[0].detail
    write(root, LEDGER, owed.render([]))
    snapshot(root, "chore: the suites table is written")
    assert checks(preflight_run(root, "4", gh_returning(issue(labels=("ready-for-agent",))))) == []


def test_a_drain_does_not_open_while_the_suites_table_is_owed_and_does_once_filled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(hooks_installed, "check", lambda _root: None)
    (tmp_path / "tmp").mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "tmp"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    entries = owing(upgraded(tmp_path / "up"))
    root = project(tmp_path, **{LEDGER: owed.render(entries)})
    assert drain_run(root, "open", ALPHA) == 1
    out = capsys.readouterr().out
    assert "fill: 1 owed items an agent writes from the repository are not written yet" in out
    assert "(changed_tests)" in out
    assert "/ratchet:fill" in out
    write(root, LEDGER, owed.render([]))
    snapshot(root, "chore: the suites table is written")
    assert ALPHA in drain.plan(drain.Drain(root, None, 3)).start
    assert drain_run(root, "open", ALPHA) == 0


def test_the_three_edits_together_leave_conformance_owing_nothing_for_the_gate(
    tmp_path: Path,
) -> None:
    root = upgraded(tmp_path)
    binding = root / BINDING
    text = binding.read_text(encoding="utf-8")
    row = conformance.template_gate_row("changed tests run")
    after = text.replace("\n## Tests\n", f"\n## Tests\n\n{SUITES}", 1)
    assert after != text
    assert "| test count did not fall |" in after
    after = after.replace("| test count did not fall |", f"{row}\n| test count did not fall |", 1)
    binding.write_text(after, encoding="utf-8")
    justfile = (root / "justfile").read_text(encoding="utf-8")
    assert "tests-changed" not in justfile_text.listed(justfile, "tier1_gates")
    listed = justfile.replace(
        'tier1_gates := "test coverage', 'tier1_gates := "test coverage tests-changed', 1
    )
    assert listed != justfile
    (root / "justfile").write_text(listed, encoding="utf-8")
    assert "tests-changed" in justfile_text.listed(listed, "tier1_gates")
    upgrade.main(["upgrade.py", "--root", str(root), "--apply"])
    assert owing(root) == []
    found = [str(f) for f in conformance.findings(root)]
    mine = [line for line in found if "tests-changed" in line or "suites" in line]
    assert mine == [BITE_OWED]
    registry = root / "quality/bites.toml"
    registry.write_text(registry.read_text(encoding="utf-8") + BITE, encoding="utf-8")
    assert [line for line in map(str, conformance.findings(root)) if "tests-changed" in line] == []
