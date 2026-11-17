from collections.abc import Callable

import pytest

import budget
import change_size
import conformance
import justfile_text
import owed
import plugin_pin

Want = Callable[[str | None], str]

TEMPLATE = """# choose the plugin
plugin_choice := "a"
# the root
ratchet_plugin := "{{plugin_choice}}"
plugin_py := "{{ratchet_plugin}}/x"
# configured
configured_jobs := "1"
# how many
jobs := configured_jobs

# say hi
[private]
hello:
    #!/bin/sh
    # a note
    echo {{plugin_py}}

# plain
plain:
    echo x
"""
PROJECT = "a := 1\n\nold:\n    echo\n"
WORKFLOW_TEMPLATE = """env:
  RATCHET_PLUGIN: x
  # a reason the template gives
  OVER: report
"""
OLD_WORKFLOW = f"env:\n  RATCHET_PLUGIN: x\njobs:\n  - run: {conformance.OLD_TIER0_BUDGET}\n"


def comments_in(text: str) -> list[str]:
    return [
        line
        for line in text.splitlines()
        if line.lstrip().startswith("#") and not line.lstrip().startswith("#!")
    ]


def added_comments(before: str | None, after: str) -> list[str]:
    return [line for line in comments_in(after) if line not in comments_in(before or "")]


def test_the_ledger_as_written_holds_no_comment_line() -> None:
    entries = [owed.Entry("a.txt", "rule", "a problem", "0.1.0")]
    assert comments_in(owed.render(entries)) == []
    assert comments_in(owed.render([])) == []
    assert comments_in(owed.recorded(entries)(None)) == []


def test_a_ledger_written_with_the_old_header_still_reads() -> None:
    entry = owed.Entry("a.txt", "rule", "a problem", "0.1.0")
    old = "# What this project still owes.\n# Nobody edits it.\n" + owed.render([entry])
    assert owed.parse(old).entries == (entry,)
    assert owed.parse(old).problems == ()


@pytest.mark.parametrize(
    "want",
    [
        justfile_text.with_recipe(TEMPLATE, "hello"),
        justfile_text.with_recipe(TEMPLATE, "plain"),
        justfile_text.with_template_recipe(TEMPLATE, "plain"),
        justfile_text.with_template_recipe(TEMPLATE, "hello"),
        justfile_text.with_plugin_block(TEMPLATE),
        conformance.with_template_jobs(TEMPLATE),
    ],
)
def test_a_justfile_copy_from_the_template_adds_no_comment(want: Want) -> None:
    project = PROJECT + 'jobs := env_var_or_default("RATCHET_JOBS", num_cpus())\n'
    for text in (PROJECT, project, "plain:\n    echo old\n"):
        assert added_comments(text, want(text)) == []


def test_a_copied_recipe_keeps_the_interpreter_line_of_its_body() -> None:
    assert "    #!/bin/sh" in justfile_text.with_recipe(TEMPLATE, "hello")(PROJECT)


def test_the_jobs_rewrite_replaces_the_released_line_with_code_alone() -> None:
    released = 'x := 1\n# the old note\njobs := env_var_or_default("RATCHET_JOBS", num_cpus())\n'
    made = conformance.with_template_jobs(TEMPLATE)(released)
    assert "configured_jobs := " in made
    assert comments_in(made) == []


def test_a_workflow_gains_the_budget_flags_and_the_variable_without_its_comment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(conformance, "template_file", lambda _: WORKFLOW_TEMPLATE)
    after = conformance.budget_flags("ci.yml")(OLD_WORKFLOW)
    assert "  OVER: report" in after
    assert comments_in(after) == []


THRESHOLDS = "[ceilings.seconds]\ntier1 = 600\n"


def test_the_thresholds_the_scripts_write_hold_no_comment() -> None:
    wants: list[Want] = [
        conformance.with_change_ceiling,
        conformance.moved_change_ceiling,
        lambda text: budget.with_ceiling(text or "", "tier2", 30.0),
        lambda text: change_size.with_ceilings(text or "", (1, 2, 3)),
    ]
    for want in wants:
        for text in ("", THRESHOLDS, "[ceilings.change]\nfiles = 1\nlines = 2\n"):
            assert comments_in(want(text)) == []


def test_the_refs_the_scripts_write_into_a_workflow_hold_no_comment() -> None:
    workflow = "      repository: r\n      ref: abc\n"
    assert added_comments(workflow, plugin_pin.with_refs("f" * 40)(workflow)) == []


def test_the_justfile_edits_the_scripts_make_hold_no_comment() -> None:
    wants = [
        justfile_text.with_variable("v", lambda _: '"x"'),
        justfile_text.with_dependency("old", "other"),
        justfile_text.with_first_dependency("old", "other"),
        justfile_text.with_first_line("old", "echo first"),
        justfile_text.with_listed("a", "word"),
    ]
    for want in wants:
        assert added_comments(PROJECT, want(PROJECT)) == []
