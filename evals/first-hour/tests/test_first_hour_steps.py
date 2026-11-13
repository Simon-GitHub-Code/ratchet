import pytest
from first_hour import steps
from first_hour.steps import Action, Budget, Instruction, Seen, Stop


def instruction(command, stage, also=()):
    return Instruction(command=command, why="w", then="t", stage=stage, also=also)


def test_the_routes_json_parses_with_or_without_proposals():
    data = {"command": "/ratchet:adopt", "why": "w", "then": "t", "stage": "adopt", "also": ["x"]}
    assert Instruction.parse(data) == instruction("/ratchet:adopt", "adopt", ("x",))
    assert Instruction.parse({"command": "/ratchet:init"}).also == ()


@pytest.mark.parametrize(
    ("command", "stage", "action", "text"),
    [
        ("/ratchet:init", "init", Action.TYPE, "/ratchet:init"),
        ("/ratchet:feature 01.md", "build", Action.TYPE, "/ratchet:feature 01.md"),
        ("/ratchet:safeguard", "safeguard", Action.TYPE, "/ratchet:safeguard"),
        ("run `just hooks-install`", "hooks", Action.TYPE, "/ratchet:next"),
        ("run `python3 x/hooks_install.py`", "hooks", Action.TYPE, "/ratchet:next"),
        ("open the pull request", "pull-request", Action.FORGE, "open the pull request"),
        ("nothing: the merge job merges it", "review", Action.FORGE, None),
    ],
)
def test_a_command_is_typed_and_a_forge_stage_is_not_followed(command, stage, action, text):
    move = steps.move_for(instruction(command, stage))
    assert move.action is action
    if text is not None:
        assert move.text == text


def test_a_merge_is_pasted_as_the_ticked_command():
    move = steps.move_for(instruction("`just merge feature/x`", "merge"))
    assert move == steps.Move(Action.PASTE, "`just merge feature/x`", ("just merge feature/x",))


def test_an_owner_git_config_fix_is_pasted():
    move = steps.move_for(instruction("run `git config core.hooksPath /a/b`", "hooks"))
    assert move.action is Action.PASTE
    assert move.commands == ("git config core.hooksPath /a/b",)


@pytest.mark.parametrize("stage", ["change", "triage", "resolve", "blocked", "blocked-run"])
def test_work_named_in_words_is_asked_of_the_assistant(stage):
    move = steps.move_for(instruction("run `just check`, then commit on branch `main`", stage))
    assert move.action is Action.ASK
    assert "run `just check`, then commit" in move.text
    assert move.commands == ()


def test_an_instruction_with_no_command_in_it_is_asked_of_the_assistant():
    move = steps.move_for(instruction("upgrade git to 2.36 or later", "hooks"))
    assert move.action is Action.ASK


def test_only_ticked_spans_that_read_as_commands_are_shell_commands():
    text = "the owner types `git switch main && git merge --no-ff x`, then `main` and `just check`"
    assert steps.shell_commands(text) == ("git switch main && git merge --no-ff x", "just check")


def test_an_owner_merge_names_the_merge_and_the_deep_check():
    output = (
        "merge: owner\nmanifest: no manifest\n"
        "next: the owner types `git switch main && git merge --no-ff adopt-ratchet`, then"
        " `just check-deep` on `main`: no gate ran on this merge\n"
    )
    assert steps.next_commands(output) == (
        "git switch main && git merge --no-ff adopt-ratchet",
        "just check-deep",
    )


def test_a_merge_the_entry_point_made_names_the_bare_deep_check_once():
    output = (
        "merged: `f` into `main` at abc\n"
        "next: just check-deep on `main`: tier 2 runs after every merge\n"
        "next: just check-deep on `main`: again\n"
        "next: write a dispute\n"
    )
    assert steps.next_commands(output) == ("just check-deep",)


BUDGET = Budget(minutes=10, usd=5, repeats=3, steps=None)


def stop(history=(), *, elapsed=0.0, cost=0.0, usable=False, budget=BUDGET):
    return steps.stop_reason(
        budget, elapsed_s=elapsed, cost_usd=cost, usable=usable, history=list(history)
    )


def test_a_run_carries_on_inside_its_budgets():
    assert stop([Seen("/a", "1"), Seen("/b", "2")], elapsed=599, cost=4.99) is None


def test_a_usable_merge_stops_the_run_before_any_budget():
    assert stop(elapsed=10_000, cost=100, usable=True) is Stop.USABLE


def test_the_wall_clock_then_the_cost_stop_the_run():
    assert stop(elapsed=600, cost=100) is Stop.WALL_CLOCK
    assert stop(elapsed=1, cost=5) is Stop.COST


def test_the_same_instruction_on_an_unchanged_checkout_is_a_repeat():
    assert stop([Seen("/a", "1"), Seen("/a", "1")]) is Stop.REPEAT


def test_the_same_instruction_after_progress_is_not_a_repeat_until_the_limit():
    assert stop([Seen("/a", "1"), Seen("/a", "2")]) is None
    assert stop([Seen("/a", "1"), Seen("/b", "2"), Seen("/a", "3"), Seen("/a", "4")]) is (
        Stop.REPEAT
    )


def test_a_step_limit_stops_the_run_after_that_many_steps():
    budget = Budget(steps=1)
    assert stop([Seen("/a", "1")], budget=budget) is None
    assert stop([Seen("/a", "1"), Seen("/b", "2")], budget=budget) is Stop.MAX_STEPS
