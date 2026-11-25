import json
from pathlib import Path

import pytest

import audit
import conformance
import formal_model
import gatelib
import hooks_installed
import owed
import settings_rules
import triggers

PLUGIN = Path(__file__).resolve().parents[2]
SKILLS = PLUGIN / "skills"


def test_audit_skill_is_read_only_runs_audit_script_and_ends_with_route() -> None:
    text = (SKILLS / "audit/SKILL.md").read_text(encoding="utf-8")
    assert "disable-model-invocation: true" in text
    assert 'scripts/audit.py" --root .' in text
    assert 'scripts/route.py" --root .' in text
    assert "read-only" in text.lower()
    assert "binding-review" in text


def test_default_branch_falls_back_to_main_outside_git(tmp_path: Path) -> None:
    assert audit.default_branch(tmp_path) == "main"


def test_conformance_section_covers_red_warn_and_green() -> None:
    with_then = conformance.Finding("a.md", "broken", conformance.Agent(then=("fix a",)))
    without_then = conformance.Finding("b.md", "also broken", conformance.Agent())
    red = audit.conformance_section([with_then, without_then], ["ignored"])
    assert red.status == "red"
    assert red.summary == "2 failure(s)"
    assert "(fix a)" in red.items[0]
    assert "(" not in red.items[1]
    warn = audit.conformance_section([], ["lint"])
    assert warn.status == "warn"
    assert "lint" in warn.items[0]
    green = audit.conformance_section([], [])
    assert green.status == "green"
    assert green.items == ()


def test_plugin_pin_hooks_settings_and_incidents_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(audit.plugin_pin, "judged", lambda _pin, _plugin: ("mismatch", True))
    bad_pin = audit.plugin_pin_section("", PLUGIN)
    assert (bad_pin.status, bad_pin.items) == ("red", ("mismatch",))
    monkeypatch.setattr(
        audit.plugin_pin, "judged", lambda _pin, _plugin: ("plugin-pin: 0.15.8", False)
    )
    ok_pin = audit.plugin_pin_section("", PLUGIN)
    assert (ok_pin.status, ok_pin.summary) == ("green", "0.15.8")

    monkeypatch.setattr(
        audit.hooks_installed,
        "check",
        lambda _root: hooks_installed.Problem("missing hooks", "run pre-commit install"),
    )
    bad_hooks = audit.hooks_section(tmp_path)
    assert bad_hooks.status == "red"
    assert "run pre-commit install" in bad_hooks.items[0]
    monkeypatch.setattr(audit.hooks_installed, "check", lambda _root: None)
    assert audit.hooks_section(tmp_path).status == "green"

    drift = settings_rules.Drift("missing rule", settings_rules.Fault.MISSING)
    monkeypatch.setattr(audit.settings_rules, "drifts", lambda _root: [drift])
    bad_settings = audit.settings_section(tmp_path)
    assert (bad_settings.status, bad_settings.items) == ("red", ("missing rule",))
    monkeypatch.setattr(audit.settings_rules, "drifts", lambda _root: [])
    assert audit.settings_section(tmp_path).status == "green"

    inc = conformance.Finding("docs/incidents/0001.md", "missing bite", conformance.Agent())
    monkeypatch.setattr(audit.conformance, "check_incidents", lambda _root: [inc])
    bad_inc = audit.incidents_section(tmp_path)
    assert bad_inc.status == "red"
    assert "docs/incidents/0001.md" in bad_inc.items[0]
    monkeypatch.setattr(audit.conformance, "check_incidents", lambda _root: [])
    assert audit.incidents_section(tmp_path).status == "green"


def test_triggers_and_formal_sections(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sg = triggers.Owed("formal verification", "code:formal-model", "formal", 2)
    rv = triggers.Owed("stored data", "code:stored-format", "stored", 1)
    monkeypatch.setattr(audit.triggers, "owed", lambda _root: [sg])
    monkeypatch.setattr(audit.triggers, "reviews_owed", lambda _root: [rv])
    trig_warn = audit.triggers_section(tmp_path)
    assert trig_warn.status == "warn"
    assert len(trig_warn.items) == 2
    monkeypatch.setattr(audit.triggers, "owed", lambda _root: [])
    monkeypatch.setattr(audit.triggers, "reviews_owed", lambda _root: [])
    assert audit.triggers_section(tmp_path).status == "green"

    monkeypatch.setattr(audit.formal_model, "read", lambda _b: None)
    assert audit.formal_section(tmp_path, "", "main").summary == "no formal model declared"
    model = formal_model.Formal(("formal/*.tla",), ("src/*.py",), "aaaa111122223333")
    monkeypatch.setattr(audit.formal_model, "read", lambda _b: model)
    monkeypatch.setattr(
        audit.formal_model,
        "behind",
        lambda _r, _b, _f: formal_model.Behind("aaaa111122223333", "bbbb444455556666"),
    )
    behind_with_head = audit.formal_section(tmp_path, "", "main")
    assert behind_with_head.status == "warn"
    assert "bbbb44445555" in behind_with_head.summary
    monkeypatch.setattr(
        audit.formal_model,
        "behind",
        lambda _r, _b, _f: formal_model.Behind("aaaa111122223333", ""),
    )
    behind_no_head = audit.formal_section(tmp_path, "", "main")
    assert "head" not in behind_no_head.summary
    monkeypatch.setattr(audit.formal_model, "behind", lambda _r, _b, _f: None)
    assert audit.formal_section(tmp_path, "", "main").status == "green"


def test_owed_and_opt_ins_sections(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.setattr(audit.owed, "read", lambda _r: owed.Ledger((), ("bad toml",)))
    assert audit.owed_section(tmp_path).status == "red"
    entry = owed.Entry("a.md", "rule", "problem", "0.15.0")
    monkeypatch.setattr(audit.owed, "read", lambda _r: owed.Ledger((entry,), ()))
    monkeypatch.setattr(audit.owed, "comments", lambda _r: [entry])
    with_comments = audit.owed_section(tmp_path)
    assert with_comments.status == "warn"
    assert "1 owed comment(s)" in with_comments.summary
    monkeypatch.setattr(audit.owed, "comments", lambda _r: [])
    no_comments = audit.owed_section(tmp_path)
    assert "owed comment" not in no_comments.summary
    monkeypatch.setattr(audit.owed, "read", lambda _r: owed.Ledger((), ()))
    assert audit.owed_section(tmp_path).status == "green"

    offered = [conformance.Finding("justfile", "custom jobs", conformance.Agent())]
    monkeypatch.setattr(audit.upgrade, "available_opt_ins", lambda _r: (("Build", "desc"),))
    opt_warn = audit.opt_ins_section(tmp_path, offered)
    assert opt_warn.status == "warn"
    assert len(opt_warn.items) == 2
    monkeypatch.setattr(audit.upgrade, "available_opt_ins", lambda _r: ())
    assert audit.opt_ins_section(tmp_path, []).status == "green"


def test_thresholds_budgets_records_and_binding_review_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    thresholds = tmp_path / gatelib.THRESHOLDS
    thresholds.parent.mkdir(parents=True)
    thresholds.write_text("[broken\n", encoding="utf-8")
    assert audit.parse_thresholds(tmp_path) == (False, set())
    thresholds.write_text("ceilings = 3\n", encoding="utf-8")
    assert audit.parse_thresholds(tmp_path) == (False, set())
    thresholds.write_text("[ceilings]\nloop = { seconds = true }\nseconds = 4\n", encoding="utf-8")
    assert audit.parse_thresholds(tmp_path) == (False, set())
    thresholds.write_text(
        "[ceilings.loop]\nseconds = 180\n[ceilings.seconds]\nlint = 10\nbad = true\n",
        encoding="utf-8",
    )
    assert audit.parse_thresholds(tmp_path) == (True, {"lint"})

    binding = (
        "# Binding\n\n"
        "## Gates\n\n"
        "| Question | Tool | Tier | Threshold source | Entry point |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| lint | ruff | 0 | `quality/thresholds.toml` | `just lint` |\n"
        "| types | mypy | 0 | `quality/thresholds.toml` | `just types` |\n"
        "| doc | none: not a library | 1 | `none` | `just doc` |\n"
        "| sym | none: no tool; ADR 0002 | 2 | `none` | `just sym` |\n"
        "| fuzz | later: code:untrusted-input | 2 | `none` | `just fuzz` |\n"
        "| empty |  | 1 | `none` |  |\n"
    )
    monkeypatch.setattr(audit.route, "loop_proposals", lambda _r: [])
    monkeypatch.setattr(audit.route, "ceiling_proposals", lambda _r: [])
    monkeypatch.setattr(audit.route, "slow_bite_proposals", lambda _r: [])
    green_budgets = audit.budgets_section(tmp_path, binding)
    assert green_budgets.status == "green"
    assert "1/2 bound gate(s) unbounded" in green_budgets.summary
    thresholds.write_text("", encoding="utf-8")
    warn_budgets = audit.budgets_section(tmp_path, binding)
    assert warn_budgets.status == "warn"

    review = audit.binding_review_section(binding)
    assert review.summary == "2 bound, 1 standing none, 1 later"
    assert len(review.items) == 2

    assert audit.open_disputes(tmp_path) == []
    disputes = tmp_path / "docs/disputes"
    disputes.mkdir(parents=True)
    (disputes / "0001-open.md").write_text("# 0001\n\n- Status: open\n", encoding="utf-8")
    (disputes / "0002-closed.md").write_text("# 0002\n\n- Status: upheld\n", encoding="utf-8")
    (disputes / "0003-link.md").symlink_to(disputes / "0001-open.md")
    assert audit.open_disputes(tmp_path) == ["open dispute: docs/disputes/0001-open.md"]

    monkeypatch.setattr(audit.route, "expired_switches", lambda _r: ["flag"])
    monkeypatch.setattr(audit.documents, "stale", lambda _r: [("docs/a.md", "src/b.py")])
    rec_warn = audit.records_section(tmp_path)
    assert rec_warn.status == "warn"
    assert len(rec_warn.items) == 3
    (disputes / "0001-open.md").unlink()
    monkeypatch.setattr(audit.route, "expired_switches", lambda _r: [])
    monkeypatch.setattr(audit.documents, "stale", lambda _r: [])
    assert audit.records_section(tmp_path).status == "green"


def test_life_events_section_covers_fresh_declined_undeclared_and_all_declared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = list(audit.application_types.load().events)
    first, second = events[0], events[1]
    monkeypatch.setattr(
        audit.application_types,
        "signs_seen",
        lambda _r: {first: {"a/**": "a/x.py"}, second: {"b/**": "b/y.py"}},
    )
    monkeypatch.setattr(audit.route, "declined_signs", lambda _r: {(second, "b/**")})
    warn = audit.life_events_section(tmp_path, "- Life events: none\n")
    assert warn.status == "warn"
    assert "declared: none" in warn.summary
    assert any(item.startswith(f"undeclared `{first}`") for item in warn.items)
    assert any(item.startswith(f"previously declined `{second}`") for item in warn.items)

    monkeypatch.setattr(audit.application_types, "signs_seen", lambda _r: {})
    monkeypatch.setattr(audit.route, "declined_signs", lambda _r: set())
    partial = audit.life_events_section(tmp_path, f"- Life events: {first}\n")
    assert partial.status == "green"
    assert f"declared: {first}" in partial.summary
    assert "undeclared (" in partial.summary

    all_declared = audit.life_events_section(tmp_path, f"- Life events: {', '.join(events)}\n")
    assert all_declared.status == "green"
    assert "all events declared" in all_declared.summary


def test_audit_and_main_render_text_and_json_with_exit_codes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    empty_code = audit.main(["audit.py", "--root", str(tmp_path), "--plugin", str(PLUGIN)])
    assert empty_code == 1
    out = capsys.readouterr().out
    assert out.startswith("ratchet-audit: red (")
    assert "[red]   conformance:" in out

    json_code = audit.main(["audit.py", "--root", str(tmp_path), "--plugin", str(PLUGIN), "--json"])
    assert json_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "red"
    assert payload["counts"]["red"] >= 1
    assert any(section["name"] == "conformance" for section in payload["sections"])

    green_sections = [
        audit.Section("conformance", "green", "0 failures"),
        audit.Section("owed", "warn", "1 entry(ies)", ("a: b",)),
    ]
    monkeypatch.setattr(audit, "audit", lambda _r, _p: green_sections)
    assert audit.main(["audit.py", "--root", str(tmp_path)]) == 0
    green_out = capsys.readouterr().out
    assert green_out.startswith("ratchet-audit: green (0 red, 1 warn, 1 green)\n")
    assert "  - a: b\n" in green_out
    assert audit.main(["audit.py", "--root", str(tmp_path), "--json"]) == 0
    green_payload = json.loads(capsys.readouterr().out)
    assert green_payload["verdict"] == "green"
    assert green_payload["counts"] == {"red": 0, "warn": 1, "green": 1}
