import itertools
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from collections.abc import Callable
from dataclasses import replace
from functools import cache
from pathlib import Path
from typing import NamedTuple

import pytest
from hypothesis import Phase, settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

import conformance
import hooks_installed
import markdown_text
import merge_ready
import owed
import route
from tests import model_tla
from tests import route_model as model
from tests import route_world as real
from tests.repos import git, snapshot
from tests.route_model import (
    ActionName,
    Advice,
    Answers,
    Architecture,
    Defect,
    Flag,
    Head,
    Setup,
    Ticket,
    Tree,
    Upkeep,
    World,
)
from tests.support import PLUGIN_ROOT
from tests.test_drain import approval

HOOKS_DIRECTORY = Path(__file__).resolve().parents[1]
CHARTS = PLUGIN_ROOT / "docs/model/route.md"
HOOKS_EVERY = 7
SWEEP_CHUNKS = 32

STEPS = settings(
    max_examples=4,
    stateful_step_count=28,
    derandomize=True,
    deadline=None,
    phases=(Phase.explicit, Phase.generate),
)


def test_every_advice_is_followed_by_exactly_one_action() -> None:
    assert set(model.ADVISED) == set(Advice)
    followed = [advice for act in model.ACTIONS.values() for advice in act.follows]
    assert sorted(followed) == sorted(Advice)
    assert set(model.ACTIONS) == set(ActionName)


def test_every_advice_the_route_can_give_is_met_in_some_state() -> None:
    assert {model.advice(state) for state in model.graph().states} == set(Advice)


def test_every_action_the_model_has_happens_in_some_state() -> None:
    assert {edge.action for edge in model.graph().edges} == set(ActionName)


Label = Callable[[World], Advice]


def cannot_be_followed(label: Label) -> set[World]:
    taken = defaultdict(set)
    for edge in model.graph().edges:
        taken[edge.source].add(edge.action)
    return {s for s in model.graph().states if model.ADVISED[label(s)] not in taken[s]}


def never_rests(_label: Label) -> set[World]:
    return {s for s, rank in model.ranks(model.graph()).items() if rank == model.LOOPS}


def rank_kept(label: Label) -> set[World]:
    ranks = model.ranks(model.graph())
    return {
        source
        for source, name, target in model.graph().edges
        if name is model.ADVISED[label(source)] and 0 < ranks[source] <= ranks[target]
    }


def stays(label: Label) -> set[World]:
    return {
        source
        for source, name, target in model.graph().edges
        if name is model.ADVISED[label(source)] and source == target
    }


def merges_a_merged_branch(label: Label) -> set[World]:
    return {s for s in model.graph().states if s.head is Head.MERGED and label(s) is Advice.MERGE}


def owes_architecture(world: World) -> bool:
    return world.architecture is Architecture.OWED


def architecture_misnamed(label: Label) -> set[World]:
    return {
        s
        for s in model.graph().states
        if (label(s) is Advice.ARCHITECTURE)
        != (owes_architecture(s) and not model.above_architecture_open(s))
    }


def work_hidden(label: Label) -> set[World]:
    taken = defaultdict(set)
    for edge in model.graph().edges:
        taken[edge.source].add(edge.action)
    return {
        s
        for s in model.graph().states
        if label(s) is Advice.ARCHITECTURE
        and model.ADVISED.get(Advice(model.beneath(s))) not in taken[s]
    }


def changed_by_another(name: ActionName, *, enters: bool) -> Callable[[Label], set[World]]:
    def check(_label: Label) -> set[World]:
        return {
            source
            for source, act, target in model.graph().edges
            if owes_architecture(source) is not enters
            and owes_architecture(target) is enters
            and act is not name
        }

    return check


def builds_without_the_line(label: Label) -> set[World]:
    return {
        s
        for s in model.graph().states
        if s.flag is Flag.MISSING and label(s) in {Advice.BUILD, Advice.DRAIN}
    }


def builds_unproved(label: Label) -> set[World]:
    return {
        s
        for s in model.graph().states
        if s.setup is Setup.PINNED and label(s) in {Advice.BUILD, Advice.DRAIN}
    }


def runs_without_the_line(_label: Label) -> set[World]:
    return {
        source
        for source, act, _target in model.graph().edges
        if source.flag is Flag.MISSING and act in model.RUNS
    }


def missing_by_another(_label: Label) -> set[World]:
    return {
        source
        for source, act, target in model.graph().edges
        if source.flag is not Flag.MISSING
        and target.flag is Flag.MISSING
        and act is not ActionName.UPGRADE_ASKS_THE_FLAG_LINE
    }


def upgrade_moves_a_ticket(_label: Label) -> set[World]:
    return {
        source
        for source, act, target in model.graph().edges
        if act is ActionName.UPGRADE_ASKS_THE_FLAG_LINE
        and (source.tickets, source.inherits) != (target.tickets, target.inherits)
    }


def builds_on_a_stale_conjecture(label: Label) -> set[World]:
    return {s for s in model.graph().states if not model.stale_before_the_build(label)(s)}


def stale_slots(world: World) -> set[int]:
    return {slot for slot, ticket in enumerate(world.tickets) if ticket is Ticket.STALE}


def runs_on_a_stale_conjecture(_label: Label) -> set[World]:
    return {
        source
        for source, act, target in model.graph().edges
        if act in model.RUNS and stale_slots(source) - stale_slots(target)
    }


def cleared_by_another(_label: Label) -> set[World]:
    return {
        source
        for source, act, target in model.graph().edges
        for slot in stale_slots(source) - stale_slots(target)
        if act is not ActionName.OWNER_STATES_CONJECTURE
        or target.tickets[slot] is not Ticket.STATED
    }


def edit_moves_more(_label: Label) -> set[World]:
    stale = (Ticket.STATED, Ticket.STALE)
    return {
        source
        for source, act, target in model.graph().edges
        if act is ActionName.OWNER_EDITS_CRITERIA
        and (
            replace(source, tickets=target.tickets) != target
            or any(
                a != b and (a, b) != stale
                for a, b in zip(source.tickets, target.tickets, strict=True)
            )
        )
    }


def leaves_the_unit(edge: model.Edge) -> bool:
    return (
        not edge.source.left
        and edge.target.left
        and edge.action is not ActionName.UPGRADE_FINDS_AN_EXPIRED_UNIT
    )


def drops_its_answers(edge: model.Edge) -> bool:
    return edge.target.answers is Answers.DROPPED and edge.source.answers is not Answers.DROPPED


def left_by_a_step(graph: model.Graph) -> set[World]:
    return {edge.source for edge in graph.edges if leaves_the_unit(edge)}


def works_beside_an_expired_unit(label: Label, graph: model.Graph) -> set[World]:
    return {s for s in graph.states if not model.expired_before_the_work(label)(s)}


def answers_dropped(graph: model.Graph) -> set[World]:
    return {s for s in graph.states if s.answers is Answers.DROPPED}


def offers_the_defects_row(label: Label, graph: model.Graph) -> set[World]:
    return {s for s in graph.states if model.defect_open(s) and label(s) is Advice.SAFEGUARD}


def behind_before_the_round(label: Label, graph: model.Graph) -> set[World]:
    return {
        s
        for s in graph.states
        if s.upkeep in model.AHEAD_OF_BEHIND and label(s) is Advice.FORMAL_UPKEEP
    }


def upkeep_hides_the_work(label: Label, graph: model.Graph) -> set[World]:
    taken = defaultdict(set)
    for edge in graph.edges:
        taken[edge.source].add(edge.action)
    return {
        s
        for s in graph.states
        if label(s) in model.OFFERED
        and model.ADVISED.get(Advice(model.beneath(s, graph.variant.chain))) not in taken[s]
    }


def prune_misnamed(label: Label) -> set[World]:
    return {
        s
        for s in model.graph().states
        if (label(s) in model.PRUNES) != (model.pruning(s) and not model.above_prune_open(s))
    }


def prune_hides_the_work(label: Label, graph: model.Graph) -> set[World]:
    taken = defaultdict(set)
    for edge in graph.edges:
        taken[edge.source].add(edge.action)
    return {
        s
        for s in graph.states
        if label(s) in model.PRUNES
        and model.ADVISED.get(Advice(model.beneath(s, graph.variant.chain))) not in taken[s]
    }


def pages_before_comments(label: Label, graph: model.Graph) -> set[World]:
    return {s for s in graph.states if s.comments and label(s) is Advice.PRUNE_PAGES}


def unfollowable(label: Label, graph: model.Graph) -> set[World]:
    taken = defaultdict(set)
    for edge in graph.edges:
        taken[edge.source].add(edge.action)
    return {s for s in graph.states if model.ADVISED[label(s)] not in taken[s]}


CHECKS: dict[str, tuple[Callable[[Label], set[World]], Label]] = {
    "AdviceCanBeFollowed": (cannot_be_followed, model.advice),
    "AdviceReachesRest": (never_rests, model.advice),
    "FollowingAdviceLowersRank": (rank_kept, model.advice),
    "RealAdviceCanBeFollowed": (cannot_be_followed, model.real_advice),
    "FollowingRealAdviceLowersRank": (rank_kept, model.real_advice),
    "AdviceChangesTheState": (stays, model.advice),
    "RealAdviceChangesTheState": (stays, model.real_advice),
    "AdviceNeverMergesAMergedBranch": (merges_a_merged_branch, model.advice),
    "RealAdviceNeverMergesAMergedBranch": (merges_a_merged_branch, model.real_advice),
    "ArchitectureNamedExactlyWhenOwed": (architecture_misnamed, model.advice),
    "OwedArchitectureNeverHidesTheWork": (work_hidden, model.advice),
    "OnlyAnUpgradeOwesTheArchitecture": (
        changed_by_another(ActionName.UPGRADE_LEAVES_ROWS_TO_FILL, enters=True),
        model.advice,
    ),
    "OnlyRecordingEndsTheOwedArchitecture": (
        changed_by_another(ActionName.OWNER_RECORDS_ARCHITECTURE, enters=False),
        model.advice,
    ),
    "FlagLineBeforeTheBuild": (builds_without_the_line, model.advice),
    "RealFlagLineBeforeTheBuild": (builds_without_the_line, model.real_advice),
    "NoRunStartsWithoutTheFlagLine": (runs_without_the_line, model.advice),
    "OnlyTheUpgradeLeavesTheFlagLineMissing": (missing_by_another, model.advice),
    "TheUpgradeMovesNoTicket": (upgrade_moves_a_ticket, model.advice),
    "UnprovedAdoptionNeverBuilds": (builds_unproved, model.advice),
    "RealUnprovedAdoptionNeverBuilds": (builds_unproved, model.real_advice),
    "StaleConjectureBeforeTheBuild": (builds_on_a_stale_conjecture, model.advice),
    "RealStaleConjectureBeforeTheBuild": (builds_on_a_stale_conjecture, model.real_advice),
    "NoRunStartsOnAStaleConjecture": (runs_on_a_stale_conjecture, model.advice),
    "OnlyTheRedraftClearsAStaleConjecture": (cleared_by_another, model.advice),
    "TheCriteriaEditMovesNothingElse": (edit_moves_more, model.advice),
    "NoStepLeavesTheUnitItExpires": (lambda _label: left_by_a_step(model.graph()), model.advice),
    "ExpiredUnitBeforeTheWork": (
        lambda label: works_beside_an_expired_unit(label, model.graph()),
        model.advice,
    ),
    "RealExpiredUnitBeforeTheWork": (
        lambda label: works_beside_an_expired_unit(label, model.graph()),
        model.real_advice,
    ),
    "TheOwnersAnswersOutliveTheUnit": (lambda _label: answers_dropped(model.graph()), model.advice),
    "DefectTicketBeforeTheSafeguard": (
        lambda label: offers_the_defects_row(label, model.graph()),
        model.advice,
    ),
    "RealDefectTicketBeforeTheSafeguard": (
        lambda label: offers_the_defects_row(label, model.graph()),
        model.real_advice,
    ),
    "RoundBeforeBehind": (
        lambda label: behind_before_the_round(label, model.graph()),
        model.advice,
    ),
    "RealRoundBeforeBehind": (
        lambda label: behind_before_the_round(label, model.graph()),
        model.real_advice,
    ),
    "UpkeepNeverHidesTheWork": (
        lambda label: upkeep_hides_the_work(label, model.graph()),
        model.advice,
    ),
    "PruneNamedExactlyWhenDue": (prune_misnamed, model.advice),
    "PruneNeverHidesTheWork": (
        lambda label: prune_hides_the_work(label, model.graph()),
        model.advice,
    ),
    "CommentsBeforePages": (
        lambda label: pages_before_comments(label, model.graph()),
        model.advice,
    ),
}


def test_every_rule_the_export_names_is_checked_here() -> None:
    assert set(CHECKS) == set(model.RULES)


@pytest.mark.parametrize("name", list(CHECKS))
def test_a_rule_breaks_only_where_a_divergence_says_it_does(name: str) -> None:
    check, label = CHECKS[name]
    broken = check(label)
    where: dict[str, set[World]] = defaultdict(set)
    for state in model.graph().states:
        for entry in model.divergences(state):
            if name in entry.breaks:
                where[entry.name].add(state)
    assert broken <= set().union(*where.values())
    assert all(broken & states for states in where.values()), sorted(where)
    named = {entry.name for entry in model.DIVERGENCES if name in entry.breaks}
    assert set(where) == named
    assert label is model.real_advice or not broken


def test_the_architecture_rules_are_met_where_they_bite() -> None:
    graph = model.graph()
    named = [s for s in graph.states if model.advice(s) is Advice.ARCHITECTURE]
    assert {model.beneath(s) for s in named} >= {Advice.BUILD, Advice.DRAIN}
    assert {model.advice(s) for s in graph.states if owes_architecture(s)} >= {
        Advice.FILL,
        Advice.SAFEGUARD,
        Advice.COMMIT,
        Advice.ARCHITECTURE,
    }
    changes = {act for a, act, b in graph.edges if owes_architecture(a) != owes_architecture(b)}
    assert changes == {
        ActionName.UPGRADE_LEAVES_ROWS_TO_FILL,
        ActionName.OWNER_RECORDS_ARCHITECTURE,
    }
    adopted = {b.architecture for _, act, b in graph.edges if act is ActionName.OWNER_ADOPTS}
    assert adopted == {Architecture.RECORDED}


def test_the_stale_conjecture_rules_are_met_where_they_bite() -> None:
    graph = model.graph()
    edits = [(a, b) for a, act, b in graph.edges if act is ActionName.OWNER_EDITS_CRITERIA]
    assert {a.inherits for a, _ in edits} == {True, False}
    assert any(len(stale_slots(b)) == 2 for a, b in edits if a.inherits)
    named = {
        (model.advice(s), model.real_advice(s))
        for s in graph.states
        if stale_slots(s) and model.advice(model.as_its_criteria_unread(s)) in model.BUILDS
    }
    assert named >= {
        (Advice.CONJECTURE, Advice.BUILD),
        (Advice.CONJECTURE, Advice.DRAIN),
        (Advice.BUILD, Advice.DRAIN),
    }
    for state in graph.states:
        if stale_slots(state) and model.advice(state) is Advice.CONJECTURE:
            redrafts = model.ACTIONS[ActionName.OWNER_STATES_CONJECTURE].successors(state)
            assert any(stale_slots(after) < stale_slots(state) for after in redrafts), state


def shortest_run_that_breaks(
    graph: model.Graph, breaks: Callable[[model.Edge], bool]
) -> list[ActionName]:
    parent: dict[World, model.Edge] = {}
    for edge in graph.edges:
        if edge.target not in parent and edge.target != graph.states[0]:
            parent[edge.target] = edge

    def trail(world: World) -> list[ActionName]:
        return [*trail(parent[world].source), parent[world].action] if world in parent else []

    first = min((e for e in graph.edges if breaks(e)), key=lambda e: len(trail(e.source)))
    return [*trail(first.source), first.action]


TO_TWO_TICKETS_IN_TRIAGE = [
    ActionName.OWNER_INITS,
    ActionName.OWNER_SETS_UP_TRACKER,
    ActionName.OWNER_ADOPTS,
    ActionName.AGENT_INSTALLS_HOOKS,
    ActionName.OWNER_GRILLS,
    ActionName.AGENT_COMMITS_ON_A_BRANCH,
    ActionName.BRANCH_MERGES,
    ActionName.OWNER_WRITES_SPEC,
    ActionName.AGENT_LANDS_RECORDS,
    ActionName.OWNER_SPLITS_SPEC,
    ActionName.AGENT_LANDS_RECORDS,
]
BOTH_FINISHED_BY_HAND = [
    *TO_TWO_TICKETS_IN_TRIAGE,
    ActionName.OWNER_WORKS_TICKET_THEMSELF,
    ActionName.OWNER_WORKS_TICKET_THEMSELF,
]
WITHOUT_THE_DELETER: dict[str, tuple[model.Deleter, Callable[[model.Edge], bool]]] = {
    "NoStepLeavesTheUnitItExpires": (model.leaves, leaves_the_unit),
    "TheOwnersAnswersOutliveTheUnit": (model.drops_the_answers, drops_its_answers),
}


@pytest.mark.parametrize("rule", list(WITHOUT_THE_DELETER))
def test_without_the_deleter_the_rule_breaks_after_both_tickets_are_finished_by_hand(
    rule: str,
) -> None:
    deleter, breaks = WITHOUT_THE_DELETER[rule]
    graph = model.graph(World(), model.Variant(deleter=deleter))
    assert shortest_run_that_breaks(graph, breaks) == BOTH_FINISHED_BY_HAND
    assert not breaks_in(model.graph())[rule]
    assert {name for name, broken in breaks_in(graph).items() if broken} == {rule}


TO_A_KIND_OWED = [*BOTH_FINISHED_BY_HAND, ActionName.KIND_OF_CODE_ARRIVES]
TO_MIRRORED_CODE = [*BOTH_FINISHED_BY_HAND, ActionName.MIRRORED_CODE_LANDS]
TO_A_DEFECT = [*TO_A_KIND_OWED, ActionName.AGENT_BINDS_SAFEGUARD]
WITH_COMMENTS_OWED = [
    ActionName.OWNER_INITS,
    ActionName.OWNER_SETS_UP_TRACKER,
    ActionName.OWNER_ADOPTS,
    ActionName.AGENT_INSTALLS_HOOKS,
]
WITHOUT_THE_STEP: dict[str, tuple[model.Variant, str, frozenset[str], list[ActionName]]] = {
    "the safeguard's filter of the defect's row": (
        model.Variant(
            chain=model.swapped(
                model.SAFEGUARD_OWED, model.Rule(Advice.SAFEGUARD, lambda w: w.owes)
            )
        ),
        "DefectTicketBeforeTheSafeguard",
        frozenset({"AdviceCanBeFollowed", "AdviceReachesRest"}),
        TO_A_DEFECT,
    ),
    "the step that frees the defect's own hold": (
        model.Variant(
            chain=model.swapped(
                model.MEET,
                model.Rule(Advice.SAFEGUARD, lambda w: model.held_for(w, Ticket.OWES_ITS_ROW)),
            )
        ),
        "DefectTicketBeforeTheSafeguard",
        frozenset({"AdviceCanBeFollowed", "AdviceReachesRest"}),
        [*TO_A_DEFECT, ActionName.OWNER_STATES_CONJECTURE, ActionName.DRAIN_PARKS_TICKET],
    ),
    "the round asked before behind": (
        model.Variant(
            chain=model.swapped(
                model.ROUND_TICKET,
                model.Rule(
                    Advice.FORMAL_UPKEEP,
                    lambda w: w.upkeep in model.BEHIND | model.AHEAD_OF_BEHIND,
                ),
                model.ROUND_TICKET,
            )
        ),
        "RoundBeforeBehind",
        frozenset({"AdviceCanBeFollowed", "AdviceReachesRest", "UpkeepNeverHidesTheWork"}),
        [*TO_MIRRORED_CODE, ActionName.AGENT_RUNS_A_ROUND],
    ),
    "the conjecture of a round's ticket that states none": (
        model.Variant(
            chain=model.swapped(
                model.ROUND_TICKET,
                model.Rule(Advice.FORMAL_TICKET, lambda w: w.upkeep in model.ROUND_TICKETS),
            )
        ),
        "AdviceCanBeFollowed",
        frozenset({"AdviceReachesRest"}),
        [*TO_MIRRORED_CODE, ActionName.AGENT_RUNS_A_ROUND],
    ),
    "the work offered beside behind": (
        model.Variant(refused_behind=frozenset(model.RUNS)),
        "UpkeepNeverHidesTheWork",
        frozenset(),
        [
            *TO_MIRRORED_CODE,
            ActionName.OWNER_WRITES_SPEC,
            ActionName.AGENT_LANDS_RECORDS,
            ActionName.OWNER_STATES_SPEC_CONJECTURE,
            ActionName.AGENT_LANDS_RECORDS,
            ActionName.OWNER_SPLITS_SPEC,
            ActionName.AGENT_LANDS_RECORDS,
            ActionName.OWNER_AGREES_TICKET,
        ],
    ),
    "the prune asked after the formal model's upkeep": (
        model.Variant(
            chain=(
                *model.CHAIN[: model.CHAIN.index(model.SAFEGUARD_OWED) - 1],
                model.COMMENTS_OWED,
                model.PAGES_DUE,
                *(
                    rule
                    for rule in model.CHAIN[model.CHAIN.index(model.SAFEGUARD_OWED) - 1 :]
                    if rule.advice not in model.PRUNES
                ),
            )
        ),
        "PruneNamedExactlyWhenDue",
        frozenset({"PruneNeverHidesTheWork"}),
        [
            *BOTH_FINISHED_BY_HAND,
            ActionName.MERGES_LEAVE_A_PAGE_STALE,
            ActionName.KIND_OF_CODE_ARRIVES,
        ],
    ),
    "the prune asked before the ticket step": (
        model.Variant(
            chain=(
                *(rule for rule in model.CHAIN[:-1] if rule.advice not in model.PRUNES),
                model.COMMENTS_OWED,
                model.PAGES_DUE,
                model.CHAIN[-1],
            )
        ),
        "PruneNamedExactlyWhenDue",
        frozenset({"AdviceReachesRest"}),
        WITH_COMMENTS_OWED,
    ),
    "the work offered beside a due prune": (
        model.Variant(prune_blocks=True),
        "PruneNeverHidesTheWork",
        frozenset(),
        WITH_COMMENTS_OWED,
    ),
    "the comments asked before the pages": (
        model.Variant(
            chain=model.swapped(model.COMMENTS_OWED, model.PAGES_DUE, model.COMMENTS_OWED)
        ),
        "CommentsBeforePages",
        frozenset(),
        [
            *WITH_COMMENTS_OWED,
            ActionName.OWNER_GRILLS,
            ActionName.AGENT_COMMITS_ON_A_BRANCH,
            ActionName.BRANCH_MERGES,
            ActionName.MERGES_LEAVE_A_PAGE_STALE,
        ],
    ),
}


def broken_by(graph: model.Graph) -> dict[str, set[World]]:
    label = graph.variant.advice
    found = {
        "AdviceCanBeFollowed": unfollowable(label, graph),
        "AdviceReachesRest": {s for s, r in model.ranks(graph).items() if r == model.LOOPS},
        "DefectTicketBeforeTheSafeguard": offers_the_defects_row(label, graph),
        "RoundBeforeBehind": behind_before_the_round(label, graph),
        "UpkeepNeverHidesTheWork": upkeep_hides_the_work(label, graph),
        "PruneNamedExactlyWhenDue": {
            s
            for s in graph.states
            if (label(s) in model.PRUNES) != (model.pruning(s) and not model.above_prune_open(s))
        },
        "PruneNeverHidesTheWork": prune_hides_the_work(label, graph),
        "CommentsBeforePages": pages_before_comments(label, graph),
    }
    return {name: states for name, states in found.items() if states}


@pytest.mark.parametrize("dropped", list(WITHOUT_THE_STEP))
def test_without_the_step_that_keeps_it_the_rule_breaks(dropped: str) -> None:
    variant, rule, also, run = WITHOUT_THE_STEP[dropped]
    graph = model.graph(World(), variant)
    broken = broken_by(graph)
    assert set(broken) == {rule, *also}
    assert broken_by(model.graph()) == {}
    assert shortest_run_that_breaks(graph, lambda edge: edge.target in broken[rule]) == run


def test_the_formal_rules_are_met_where_they_bite() -> None:
    graph = model.graph()
    defects = [s for s in graph.states if model.defect_open(s)]
    assert all(s.owes for s in defects)
    assert {model.advice(s) for s in defects} >= {
        Advice.CONJECTURE,
        Advice.BUILD,
        Advice.CHANGE_TICKET,
        Advice.MERGE,
        Advice.RESOLVE,
    }
    kept = {model.advice(s) for s in graph.states if s.defect is Defect.KEPT and model.calm(s)}
    assert kept == {Advice.SAFEGUARD}
    named = {
        (s.upkeep, model.advice(s))
        for s in graph.states
        if s.upkeep in model.AHEAD_OF_BEHIND and model.settled(s)
    }
    assert named == {
        (Upkeep.TICKET, Advice.FORMAL_TICKET),
        (Upkeep.UNSTATED, Advice.CONJECTURE),
        (Upkeep.WAITING_BEHIND, Advice.FORMAL_ROUND),
    }
    stated = {
        (a.upkeep, b.upkeep)
        for a, act, b in graph.edges
        if act is ActionName.OWNER_STATES_CONJECTURE and a.upkeep is not b.upkeep
    }
    assert stated == {(Upkeep.UNSTATED, Upkeep.TICKET)}
    offered = {model.beneath(s) for s in graph.states if model.advice(s) in model.OFFERED}
    assert offered == {
        Advice.SPECIFY,
        Advice.CONJECTURE_SPEC,
        Advice.SPLIT,
        Advice.TRIAGE,
        Advice.BLOCKED,
        Advice.BUILD,
        Advice.RESOLVE,
        *model.PRUNES,
    }
    rounds = {
        (a.upkeep, b.upkeep) for a, act, b in graph.edges if act is ActionName.AGENT_RUNS_A_ROUND
    }
    assert rounds == {
        (Upkeep.BEHIND, Upkeep.WAITING),
        (Upkeep.BEHIND, Upkeep.TICKET),
        (Upkeep.BEHIND, Upkeep.UNSTATED),
        (Upkeep.FIXED, Upkeep.WAITING),
    }


def test_the_prune_rules_are_met_where_they_bite() -> None:
    graph = model.graph()
    due = [s for s in graph.states if model.pruning(s)]
    assert {model.advice(s) for s in due} >= {
        Advice.HOOKS,
        Advice.COMMIT,
        Advice.MERGE,
        Advice.LAND_RECORDS,
        Advice.MERGE_RECORDS,
        Advice.FILL,
        Advice.REVIEW,
        Advice.SAFEGUARD,
        Advice.ARCHITECTURE,
        Advice.FORMAL_ROUND,
        Advice.FORMAL_UPKEEP,
        *model.PRUNES,
    }
    named = [s for s in due if model.advice(s) in model.PRUNES]
    assert {model.beneath(s) for s in named} >= {
        Advice.GRILL,
        Advice.EXPIRE,
        Advice.SPECIFY,
        Advice.TRIAGE,
        Advice.BUILD,
        Advice.RESOLVE,
        Advice.BLOCKED,
    }
    assert any(s.pages and model.advice(s) is Advice.PRUNE_COMMENTS for s in named)
    changed = defaultdict(set)
    for a, act, b in graph.edges:
        for part in ("comments", "pages"):
            if getattr(a, part) != getattr(b, part):
                changed[part, getattr(b, part)].add(act)
    assert changed == {
        ("comments", True): {ActionName.OWNER_ADOPTS},
        ("comments", False): {ActionName.AGENT_PRUNES_COMMENTS},
        ("pages", True): {ActionName.MERGES_LEAVE_A_PAGE_STALE},
        ("pages", False): {ActionName.AGENT_PRUNES_PAGES},
    }
    passes = {model.ADVISED[label] for label in model.PRUNES}
    assert {
        replace(b, comments=a.comments, pages=a.pages) == a
        for a, act, b in graph.edges
        if act in passes
    } == {True}


def test_the_walk_takes_pages_passes_that_cut_and_that_cut_nothing() -> None:
    passes = [
        real.Change(edge.source, edge.target)
        for edge in model.graph().edges
        if edge.action is ActionName.AGENT_PRUNES_PAGES
    ]
    cut = sum(real.cuts_a_page(change) for change in passes)
    assert 0 < cut < len(passes), f"{cut} of the {len(passes)} pages passes cut a page"


def breaks_in(graph: model.Graph) -> dict[str, bool]:
    ranks = model.ranks(graph)
    return {
        "NoStepLeavesTheUnitItExpires": bool(left_by_a_step(graph)),
        "ExpiredUnitBeforeTheWork": bool(works_beside_an_expired_unit(model.advice, graph)),
        "TheOwnersAnswersOutliveTheUnit": bool(answers_dropped(graph)),
        "AdviceReachesRest": model.LOOPS in ranks.values(),
    }


def test_a_unit_left_by_an_older_plugin_is_deleted_before_the_next_spec() -> None:
    graph = model.graph()
    left = [s for s in graph.states if s.left]
    assert left
    assert {(model.advice(s), model.real_advice(s)) for s in left} == {
        (label, label) for label in (Advice.EXPIRE, *model.PRUNES)
    }
    deleted = {
        b.tree for a, act, b in graph.edges if act is ActionName.AGENT_DELETES_THE_EXPIRED_UNIT
    }
    assert deleted == {Tree.RECORDS}
    expiring = {(act, b.tree) for a, act, b in graph.edges if model.expires(a, b)}
    assert expiring == {
        (ActionName.OWNER_RESOLVES_TICKET, Tree.RECORDS),
        (ActionName.OWNER_WORKS_TICKET_THEMSELF, Tree.CLEAN),
    }


def test_the_finish_the_blocked_line_names_never_expires_the_unit() -> None:
    graph = model.graph()
    blocked = {s for s in graph.states if Advice.BLOCKED in {model.advice(s), model.real_advice(s)}}
    finish = model.ADVISED[Advice.BLOCKED]
    assert blocked
    assert not any(s.left for s in blocked)
    assert not [
        b for a, act, b in graph.edges if a in blocked and act is finish and model.expires(a, b)
    ]
    assert any(act is finish and model.expires(a, b) for a, act, b in graph.edges)


def test_the_last_mark_leaves_the_answers_carried_in_records_and_names_their_landing() -> None:
    graph = model.graph()
    marks = [
        (a, b)
        for a, act, b in graph.edges
        if model.expires(a, b) and act is ActionName.OWNER_RESOLVES_TICKET
    ]
    assert {(a.defect, b.tree, b.answers) for a, b in marks} == {
        (Defect.NONE, Tree.RECORDS, Answers.CARRIED),
        (Defect.OPEN, Tree.RECORDS, Answers.CARRIED),
        (Defect.OPEN, Tree.RECORDS, Answers.NONE),
    }
    marks = [b for _a, b in marks]
    assert {model.advice(b) for b in marks if b.setup is Setup.BOUND} == {Advice.LAND_RECORDS}
    landings = {ActionName.AGENT_LANDS_RECORDS, ActionName.OWNER_MERGES_RECORDS}
    ends = {
        (act, b.parked)
        for a, act, b in graph.edges
        if a.answers is Answers.CARRIED and b.answers is not Answers.CARRIED
    }
    assert ends == {(act, False) for act in landings}


def test_the_last_mark_appends_to_the_brief_only_dated_answers_of_the_files_it_deletes(
    tmp_path: Path,
) -> None:
    marks = [
        edge
        for edge in model.graph().edges
        if edge.action is ActionName.OWNER_RESOLVES_TICKET
        and model.expires(edge.source, edge.target)
    ]
    mark = min(marks, key=lambda edge: len(spanning().trail(edge.source)))
    path = [*path_to(mark.source), mark]
    repo = real.Repo.start(tmp_path)
    for source, name, target in path:
        real.apply(repo, name, real.Change(source, target))
    status = git(repo.root, "status", "--porcelain", "--no-renames").splitlines()
    brief = str(route.DESIGN_BRIEF)
    deleted = sorted(line[3:] for line in status if line[:2].strip() == "D")
    assert sorted(line[3:] for line in status) == sorted([*deleted, brief])
    assert deleted
    assert all(name.startswith(f"{repo.feature()}/") for name in deleted)
    diff = git(repo.root, "diff", "--unified=0", "HEAD", "--", brief).splitlines()
    added = [line[1:] for line in diff if line.startswith("+") and not line.startswith("+++")]
    removed = [line for line in diff if line.startswith("-") and not line.startswith("---")]
    answers = {
        line
        for name in deleted
        for line in markdown_text.section(
            git(repo.root, "show", f"HEAD:{name}"), real.ANSWERS
        ).splitlines()
        if line.strip()
    }
    assert added
    assert removed == []
    assert {line.replace(f"- {real.EXPIRED_ON}: ", "- ", 1) for line in added} <= answers


def test_the_step_that_resolves_the_last_ticket_deletes_the_unit_and_keeps_its_answers(
    tmp_path: Path,
) -> None:
    finished = replace(ADOPTED, foundation=True, tickets=(Ticket.RESOLVED, Ticket.RESOLVED))
    path = path_to(finished)
    assert [edge.action for edge in path] == BOTH_FINISHED_BY_HAND
    repo = real.Repo.start(tmp_path)
    for source, name, target in path:
        real.apply(repo, name, real.Change(source, target))
    assert not (repo.root / repo.feature()).exists()
    brief = (repo.root / route.DESIGN_BRIEF).read_text(encoding="utf-8")
    assert "`spec.scope`: one command, not a list of them." in brief
    assert git(repo.root, "status", "--porcelain") == ""


def advice_loop(graph: model.Graph) -> list[World]:
    follows = {
        state: () if model.rest(state) else targets
        for state, targets in graph.followed(model.advice).items()
    }
    done: set[World] = set()

    def visit(state: World, path: list[World]) -> list[World]:
        if state in path:
            return [*path[path.index(state) :], state]
        if state in done:
            return []
        for target in follows[state]:
            if loop := visit(target, [*path, state]):
                return loop
        done.add(state)
        return []

    return next((loop for state in graph.states if (loop := visit(state, []))), [])


def test_following_advice_never_loops_except_through_rest() -> None:
    loop = advice_loop(model.graph())
    assert not loop, " -> ".join(model.advice(state) for state in loop)


def test_every_state_reaches_rest_by_some_actions() -> None:
    graph = model.graph()
    before = defaultdict(set)
    for edge in graph.edges:
        before[edge.target].add(edge.source)
    reaching = {state for state in graph.states if model.rest(state)}
    todo = list(reaching)
    while todo:
        for source in before[todo.pop()] - reaching:
            reaching.add(source)
            todo.append(source)
    assert set(graph.states) - reaching == set()


RELEASED_BY = {
    Ticket.HELD: ActionName.OWNER_ANSWERS_BLOCKED_RUN,
    Ticket.ASKED: ActionName.OWNER_ANSWERS_ON_THE_TICKET,
    Ticket.GUARDED: ActionName.OWNER_LANDS_GUARD_EDITS,
    Ticket.OWED: ActionName.AGENT_BINDS_SAFEGUARD,
    Ticket.PARKED: ActionName.OWNER_CHANGES_TICKET,
    Ticket.OWES_ITS_ROW: ActionName.OWNER_CHANGES_TICKET,
}


@pytest.mark.parametrize("hold", sorted(model.HOLDS))
def test_a_hold_is_released_by_its_own_step_and_by_no_other(hold: Ticket) -> None:
    kept: set[ActionName] = set()
    released: set[ActionName] = set()
    for source, name, target in model.graph().edges:
        for slot, ticket in enumerate(source.tickets):
            if ticket is hold:
                (kept if target.tickets[slot] is hold else released).add(name)
    assert released == {RELEASED_BY[hold]}
    assert bool(kept) is (hold is not Ticket.OWES_ITS_ROW)


def test_the_graph_is_the_same_every_time_it_is_walked() -> None:
    walked = model.graph.__wrapped__()
    assert walked == model.graph()
    assert walked.states[0] == World()


@pytest.mark.parametrize("entry", model.DIVERGENCES, ids=lambda entry: entry.name)
def test_a_divergence_names_a_sentence_of_the_pages_and_a_state_where_it_holds(
    entry: model.Divergence,
) -> None:
    page = (PLUGIN_ROOT / entry.source).read_text(encoding="utf-8")
    assert entry.contradicts in " ".join(page.split())
    assert entry.example in model.graph().states
    assert model.divergences(entry.example) == (entry,)
    assert entry.breaks <= set(model.RULES)


def test_the_exporter_names_every_action_rule_and_advice() -> None:
    exported = model.export()
    for name in ActionName:
        assert f"\n{name} ==\n" in exported.module
    (following,) = (line for line in exported.module.splitlines() if line.startswith("Next == "))
    assert set(following.removeprefix("Next == ").split(" \\/ ")) == set(ActionName)
    for label in Advice:
        assert f'"{label}"' in exported.module
    for name, checked in model.RULES.items():
        kind = "INVARIANT" if isinstance(checked, model_tla.Invariant) else "PROPERTY"
        assert f"\n{kind} {name}\n" in exported.config
        assert f"\\* {checked.says}\n{name} ==\n" in exported.module
    assert exported.module.startswith("---- MODULE Route ----\n")
    assert exported.module.endswith("\n====\n")


def test_the_exporter_writes_the_same_bytes_whatever_the_hash_seed(tmp_path: Path) -> None:
    for seed in ("1", "2"):
        subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
            [sys.executable, "-m", "tests.model_tla", "tests.route_model", str(tmp_path / seed)],
            cwd=HOOKS_DIRECTORY,
            env={**os.environ, "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
    for name in ("Route.tla", "Route.cfg", "RouteShown.tla", "RouteShown.cfg"):
        assert (tmp_path / "1" / name).read_bytes() == (tmp_path / "2" / name).read_bytes()
    for exported in (model.export(), model.summary()):
        written = tmp_path / "1" / f"{exported.name}.tla"
        assert written.read_text(encoding="utf-8") == exported.module


def test_the_charts_were_drawn_from_the_current_export() -> None:
    assert not (why := model_tla.stale(CHARTS, model.export(), model.summary())), why


def follow(repo: real.Repo, world: World, outcome: int) -> tuple[World, list[Advice]]:
    labels: list[Advice] = []
    while not labels or not model.rest(world):
        expected = model.real_advice(world)
        step = real.read(repo)
        assert real.classify(step) is expected, (labels, expected, step)
        labels.append(model.advice(world))
        name = model.ADVISED[model.advice(world)]
        after = list(model.ACTIONS[name].successors(world))[outcome]
        real.apply(repo, name, real.Change(world, after))
        world = after
    assert real.classify(real.read(repo)) is model.real_advice(world)
    return world, labels


def test_following_the_advice_from_an_empty_repository_reaches_rest(tmp_path: Path) -> None:
    world, labels = follow(real.Repo.start(tmp_path), World(), 0)
    assert model.rest(world)
    assert labels == [
        Advice.INIT,
        Advice.TRACKER,
        Advice.ADOPT,
        Advice.HOOKS,
        Advice.GRILL,
        Advice.COMMIT,
        Advice.MERGE,
    ]


def test_following_the_advice_after_an_edit_at_rest_commits_it_then_merges_it(
    tmp_path: Path,
) -> None:
    repo = real.Repo.start(tmp_path)
    world, _ = follow(repo, World(), 0)
    edited = next(model.ACTIONS[ActionName.SOMEONE_EDITS_CODE].successors(world))
    real.apply(repo, ActionName.SOMEONE_EDITS_CODE, real.Change(world, edited))
    world, labels = follow(repo, edited, 0)
    assert model.rest(world)
    assert labels == [Advice.COMMIT, Advice.MERGE]


@pytest.mark.parametrize("outcome", [0, -1], ids=["first outcomes", "last outcomes"])
def test_following_the_advice_through_a_whole_feature_returns_to_rest(
    tmp_path: Path, outcome: int
) -> None:
    repo = real.Repo.start(tmp_path)
    world, _ = follow(repo, World(), outcome)
    world, labels = follow(repo, world, outcome)
    assert model.rest(world)
    assert labels[0] is Advice.SPECIFY
    assert Advice.RESOLVE in labels


class Spanning(NamedTuple):
    order: tuple[World, ...]
    place: dict[World, int]
    parent: dict[World, model.Edge]
    children: dict[World, list[World]]
    size: dict[World, int]

    def trail(self, world: World) -> list[str]:
        names: list[str] = []
        while world in self.parent:
            names.insert(0, str(self.parent[world].action))
            world = self.parent[world].source
        return names


@cache
def spanning() -> Spanning:
    graph = model.graph()
    parent: dict[World, model.Edge] = {}
    children: dict[World, list[World]] = defaultdict(list)
    for edge in graph.edges:
        if edge.target not in parent and edge.target != graph.states[0]:
            parent[edge.target] = edge
            children[edge.source].append(edge.target)
    order: list[World] = []
    size: dict[World, int] = {}

    def visit(state: World) -> int:
        order.append(state)
        size[state] = 1 + sum(visit(child) for child in children[state])
        return size[state]

    visit(graph.states[0])
    place = {state: index for index, state in enumerate(order)}
    return Spanning(tuple(order), place, parent, children, size)


@cache
def leaving() -> dict[World, list[model.Edge]]:
    out: dict[World, list[model.Edge]] = defaultdict(list)
    for edge in model.graph().edges:
        out[edge.source].append(edge)
    return out


def ask(repo: real.Repo, world: World, path: list[str]) -> None:
    step = real.read(repo)
    assert real.classify(step) is model.real_advice(world), disagreement(world, step, path)
    if real.classify(step) in {Advice.RESOLVE, Advice.BLOCKED}:
        taken = model.ACTIONS[model.ADVISED[model.real_advice(world)]].successors(world)
        deletes = any(model.expires(world, after) for after in taken)
        named = f", then run `{route.EXPIRE}`" in step.command
        assert named is deletes, disagreement(world, step, path)
    if real.classify(step) is Advice.ARCHITECTURE:
        instead = without(repo, "architecture_step")
        under = model.beneath(model.as_the_route_reads(world))
        assert real.classify(instead) is under, disagreement(world, instead, path)
        assert step.then == instead.command, disagreement(world, step, path)
        assert f"Or carry on with {instead.command}:" in step.why, disagreement(world, step, path)
    if real.classify(step) is Advice.SAFEGUARD:
        after = without(repo, "safeguard_step")
        owed = world.architecture is Architecture.OWED
        assert (real.classify(after) is Advice.ARCHITECTURE) == owed, disagreement(
            world, after, path
        )
        assert step.then == after.command, disagreement(world, step, path)
        work = without(repo, "safeguard_step", "architecture_step", "formal_step")
        assert f"Or carry on with {work.command}:" in step.why, disagreement(world, step, path)
    if real.classify(step) in model.PRUNES:
        instead = without(repo, "prune_step")
        under = model.beneath(model.as_the_route_reads(world))
        assert real.classify(instead) is under, disagreement(world, instead, path)
        assert step.then == instead.command, disagreement(world, step, path)
        carry = f"Or carry on with {instead.command}: nothing is red for it"
        assert carry in step.why, disagreement(world, step, path)
    if real.classify(step) in UPKEEP:
        instead = without(repo, "formal_step")
        under = model.beneath(model.as_the_route_reads(world))
        assert real.classify(instead) is under, disagreement(world, instead, path)
        assert step.then == instead.command, disagreement(world, step, path)
        carry = f"Or carry on with {instead.command}: nothing is red for it"
        assert carry in step.why, disagreement(world, step, path)


UPKEEP = frozenset({Advice.FORMAL_TICKET, Advice.FORMAL_ROUND, Advice.FORMAL_UPKEEP})


def without(repo: real.Repo, *steps: str) -> route.Step:
    with pytest.MonkeyPatch.context() as patch:
        for name in steps:
            patch.setattr(route, name, lambda *_args: None)
        return real.read(repo)


def sweep(repo: real.Repo, world: World, span: range, *, scratch: Path) -> int:
    tree = spanning()
    path = tree.trail(world)
    here = tree.place[world] in span
    below = [
        child
        for child in tree.children[world]
        if tree.place[child] < span.stop and tree.place[child] + tree.size[child] > span.start
    ]
    ends = [
        edge
        for edge in (leaving()[world] if here else [])
        if not (edge.target in below and tree.parent[edge.target] is edge)
    ]
    for index, edge in enumerate(ends):
        last = not below and index == len(ends) - 1
        there = repo if last else repo.copy(scratch / f"{tree.place[world]}-{index}")
        real.apply(there, edge.action, real.Change(world, edge.target))
        ask(there, edge.target, [*path, edge.action])
        if not last:
            shutil.rmtree(there.root)
    taken = len(ends)
    for index, child in enumerate(below):
        edge = tree.parent[child]
        last = index == len(below) - 1
        down = repo if last else repo.copy(scratch / str(tree.place[child]))
        real.apply(down, edge.action, real.Change(world, child))
        if here:
            ask(down, child, [*path, edge.action])
            taken += 1
        taken += sweep(down, child, span, scratch=scratch)
    return taken


@cache
def spans() -> list[range]:
    tree = spanning()
    steps = [len(leaving()[state]) for state in tree.order]
    total, bounds, done = sum(steps), [0], 0
    for place, count in enumerate(steps):
        done += count
        if done * SWEEP_CHUNKS >= total * len(bounds) and len(bounds) < SWEEP_CHUNKS:
            bounds.append(place + 1)
    bounds.append(len(tree.order))
    return [range(start, stop) for start, stop in itertools.pairwise(bounds)]


def test_the_chunks_cover_every_state_once() -> None:
    assert [place for span in spans() for place in span] == list(range(len(spanning().order)))
    assert len(spans()) == SWEEP_CHUNKS


@pytest.fixture
def hooks_checked_once(monkeypatch: pytest.MonkeyPatch) -> None:
    check = hooks_installed.check
    green: set[tuple[bytes | None, ...]] = set()

    def checked(root: Path) -> hooks_installed.Problem | None:
        files = [root / ".git/config", *(root / ".git/hooks" / n for n in hooks_installed.HOOKS)]
        key = tuple(path.read_bytes() if path.is_file() else None for path in files)
        if key in green:
            return None
        problem = check(root)
        if problem is None:
            green.add(key)
        return problem

    monkeypatch.setattr(hooks_installed, "check", checked)


@pytest.mark.usefixtures("hooks_checked_once")
@pytest.mark.parametrize(
    "chunk", [pytest.param(n, marks=pytest.mark.shard_slot(n)) for n in range(SWEEP_CHUNKS)]
)
def test_the_route_names_what_the_model_says_after_every_step(tmp_path: Path, chunk: int) -> None:
    span = spans()[chunk]
    repo = real.Repo.start(tmp_path)
    if 0 in span:
        ask(repo, World(), [])
    taken = sweep(repo, World(), span, scratch=tmp_path / "copies")
    tree = spanning()
    assert taken == sum(len(leaving()[tree.order[place]]) for place in span)


def disagreement(world: World, step: route.Step, path: list[str]) -> str:
    names = ", ".join(entry.name for entry in model.divergences(world))
    return "\n".join(
        [
            "the route and the model disagree after:",
            *(f"  {index}. {line}" for index, line in enumerate(path, 1)),
            f"world:        {world}",
            f"model:        {model.real_advice(world)}"
            + (f" (divergence {names}; the pages say {model.advice(world)})" if names else ""),
            f"classified:   {real.classify(step)}",
            f"route stage:  {step.stage}",
            f"route next:   {step.command}",
            f"route why:    {step.why}",
            f"route then:   {step.then}",
        ]
    )


def described(before: World, after: World) -> str:
    fields = (f for f in World.__dataclass_fields__ if getattr(before, f) != getattr(after, f))
    return ", ".join(f"{f}={getattr(after, f)!s}" for f in fields)


def toward(world: World, aim: Callable[[World], bool]) -> list[model.Edge]:
    out: dict[World, list[model.Edge]] = defaultdict(list)
    for edge in model.graph().edges:
        out[edge.source].append(edge)
    frontier: dict[World, dict[model.Edge, None]] = {world: {}}
    seen = {world}
    while frontier:
        found = {
            first: None for state, firsts in frontier.items() if aim(state) for first in firsts
        }
        if found:
            return list(found)
        step: dict[World, dict[model.Edge, None]] = {}
        for state, firsts in frontier.items():
            for edge in out[state]:
                if edge.target not in seen:
                    step.setdefault(edge.target, {}).update(firsts or {edge: None})
        seen |= set(step)
        frontier = step
    return []


def path_to(target: World) -> list[model.Edge]:
    path: list[model.Edge] = []
    world = World()
    while world != target:
        path.append(toward(world, lambda state: state == target)[0])
        world = path[-1].target
    return path


@pytest.mark.parametrize(
    "entry",
    [
        pytest.param(
            entry,
            marks=pytest.mark.xfail(
                strict=True,
                reason=f"divergence {entry.name}: delete the entry once the route is fixed",
            ),
            id=entry.name,
        )
        for entry in model.DIVERGENCES
    ],
)
def test_the_route_names_what_the_pages_say_where_it_diverges(
    tmp_path: Path, entry: model.Divergence
) -> None:
    repo = real.Repo.start(tmp_path)
    for source, name, target in path_to(entry.example):
        real.apply(repo, name, real.Change(source, target))
    assert real.classify(real.read(repo)) is model.advice(entry.example)


def test_a_commit_the_hold_did_not_name_leaves_a_guard_hold_and_its_edits_release_it(
    tmp_path: Path,
) -> None:
    parked = World(
        setup=Setup.BOUND,
        hooks=True,
        foundation=True,
        tickets=(Ticket.GUARDED, Ticket.TRIAGE),
        answers=Answers.ON_THE_UNIT,
    )
    repo = real.Repo.start(tmp_path)
    for source, name, target in path_to(parked):
        real.apply(repo, name, real.Change(source, target))
    worlds, tips = [parked], [git(repo.root, "rev-parse", "main")]
    said = [real.classify(real.read(repo))]
    for name in (ActionName.OWNER_AGREES_TICKET, ActionName.OWNER_LANDS_GUARD_EDITS):
        (after,) = model.ACTIONS[name].successors(worlds[-1])
        real.apply(repo, name, real.Change(worlds[-1], after))
        worlds.append(after)
        tips.append(git(repo.root, "rev-parse", "main"))
        said.append(real.classify(real.read(repo)))
    assert len(set(tips)) == len(tips), "each step commits on the default branch"
    expected = [Advice.LAND_GUARD, Advice.LAND_GUARD, Advice.BUILD]
    assert said == [model.real_advice(world) for world in worlds] == expected


def architecture_found(repo: real.Repo) -> list[conformance.Finding]:
    check = conformance.check_architecture
    return [replace(f, rule=conformance.rule_of(check)) for f in check(repo.root)]


def built(tmp_path: Path, world: World) -> real.Repo:
    repo = real.Repo.start(tmp_path)
    for source, name, target in path_to(world):
        real.apply(repo, name, real.Change(source, target))
    return repo


def parked_by(world: World) -> real.Change:
    tree = spanning()
    edge = tree.parent[world]
    while edge.source.parked:
        edge = tree.parent[edge.source]
    return real.Change(edge.source, edge.target)


def test_the_walk_leaves_records_waiting_on_branches_of_both_kinds_of_name() -> None:
    waiting = [world for world in spanning().order if world.parked]
    named = sum(real.named_by_an_agent(parked_by(world)) for world in waiting)
    assert 0 < named < len(waiting), (
        f"of the {len(waiting)} states of the walk where a records branch waits, {named} name it"
        f" as an agent picked it and {len(waiting) - named} `records/<time>`"
    )


@cache
def landings_on_a_branch_an_agent_named() -> list[model.Edge]:
    tree = spanning()
    landings = [
        edge
        for edge in model.graph().edges
        if edge.action is ActionName.AGENT_LANDS_RECORDS
        and real.named_by_an_agent(real.Change(edge.source, edge.target))
    ]
    return sorted(landings, key=lambda edge: len(tree.trail(edge.source)))


@pytest.mark.parametrize("tickets", [False, True], ids=["a spec", "tickets"])
def test_records_waiting_on_a_branch_an_agent_named_are_named_for_its_merge(
    tmp_path: Path, *, tickets: bool
) -> None:
    landing = next(
        edge
        for edge in landings_on_a_branch_an_agent_named()
        if (edge.source.tickets != (Ticket.NONE, Ticket.NONE)) == tickets
    )
    edges = [*path_to(landing.source), landing]
    repo = real.Repo.start(tmp_path)
    for source, name, target in edges:
        real.apply(repo, name, real.Change(source, target))
    step = real.read(repo)
    why = disagreement(landing.target, step, [str(edge.action) for edge in edges])
    assert str(repo.parked).startswith(real.TICKETS_BRANCH), repo.parked
    assert model.real_advice(landing.target) is Advice.MERGE_RECORDS, why
    assert real.classify(step) is Advice.MERGE_RECORDS, why
    assert f" {repo.parked}`" in step.command, why


def upgraded(repo: real.Repo, world: World) -> World:
    (after,) = model.ACTIONS[ActionName.UPGRADE_LEAVES_ROWS_TO_FILL].successors(world)
    real.apply(repo, ActionName.UPGRADE_LEAVES_ROWS_TO_FILL, real.Change(world, after))
    return after


DONE = World(
    setup=Setup.BOUND, hooks=True, foundation=True, tickets=(Ticket.RESOLVED, Ticket.RESOLVED)
)


FILLED_BY_THE_UPGRADE = frozenset(
    {
        "changed_tests",
        "comments_section",
        "dependencies",
        "documents",
        "installs",
        "pins",
        "signs",
        "suppressions",
    }
)


def owes(repo: real.Repo, rules: frozenset[str]) -> list[owed.Entry]:
    return [entry for entry in owed.read(repo.root).entries if entry.rule in rules]


def test_an_adoption_records_the_architecture_so_an_upgrade_owes_none(tmp_path: Path) -> None:
    world = replace(DONE, architecture=Architecture.RECORDED)
    repo = built(tmp_path, world)
    assert architecture_found(repo) == []
    world = upgraded(repo, world)
    assert world.architecture is Architecture.RECORDED
    assert owes(repo, frozenset({route.ARCHITECTURE_RULE})) == []
    assert {entry.rule for entry in owed.fill_first(repo.root)} == FILLED_BY_THE_UPGRADE
    _, labels = follow(repo, world, 0)
    assert labels == [Advice.FILL]
    assert owed.fill_first(repo.root) == []


def test_an_older_adoption_owes_the_architecture_after_an_upgrade_until_it_is_recorded(
    tmp_path: Path,
) -> None:
    world = replace(DONE, architecture=Architecture.UNASKED)
    repo = built(tmp_path, world)
    assert ActionName.OLDER_PLUGIN_ADOPTS in [step.action for step in path_to(world)]
    assert architecture_found(repo)
    world = upgraded(repo, world)
    found = architecture_found(repo)
    assert found
    owing = owed.read(repo.root).entries
    assert {e.key for e in owing if e.rule == route.ARCHITECTURE_RULE} == {f.key() for f in found}
    assert conformance.owed_split(repo.root, found) == ([], found)
    world, labels = follow(repo, world, 0)
    assert labels == [Advice.FILL, Advice.ARCHITECTURE, Advice.MERGE]
    assert model.rest(world)
    assert architecture_found(repo) == []
    assert owes(repo, FILLED_BY_THE_UPGRADE | {route.ARCHITECTURE_RULE}) == []


def test_the_real_upgrade_owes_more_than_the_model_and_leaves_a_faithful_adoption_green(
    tmp_path: Path,
) -> None:
    world = replace(DONE, architecture=Architecture.UNASKED)
    repo = built(tmp_path, world)
    upgraded(repo, world)
    beyond = {e.rule for e in owed.read(repo.root).entries} - FILLED_BY_THE_UPGRADE
    assert beyond - {route.ARCHITECTURE_RULE} == {
        "adrs",
        "binding",
        "bites",
        "brief",
        "commit_hooks",
        "doors",
        "glossary",
        "manifest",
        "operability",
        "quality",
        "shared_reports",
        "speed",
    }
    red, _ = conformance.owed_split(repo.root, conformance.findings(repo.root))
    assert [str(finding) for finding in red] == []


def test_a_review_and_a_safeguard_owed_beside_the_architecture_come_before_it(
    tmp_path: Path,
) -> None:
    world = replace(DONE, architecture=Architecture.OWED)
    repo = built(tmp_path, world)
    said = []
    for name, owing in (
        (ActionName.KIND_THAT_REOPENS_THE_ARCHITECTURE_ARRIVES, {"reviews": True}),
        (ActionName.KIND_OF_CODE_ARRIVES, {"owes": True}),
        (ActionName.AGENT_LANDS_THE_REVIEW, {"reviews": False}),
        (ActionName.AGENT_BINDS_SAFEGUARD, {"owes": False}),
    ):
        after = replace(world, **owing)
        real.apply(repo, name, real.Change(world, after))
        world = after
        said.append(real.classify(real.read(repo)))
        assert said[-1] is model.advice(world)
    assert said == [Advice.REVIEW, Advice.REVIEW, Advice.SAFEGUARD, Advice.ARCHITECTURE]


ADOPTED = World(setup=Setup.BOUND, hooks=True, architecture=Architecture.RECORDED)
IN_FLIGHT = replace(ADOPTED, foundation=True, answers=Answers.ON_THE_UNIT)
BRANCHES = {
    "the adoption's branch": (replace(ADOPTED, setup=Setup.PINNED, head=Head.WORK), ()),
    "a working branch": (
        replace(ADOPTED, foundation=True),
        (ActionName.SOMEONE_EDITS_CODE, ActionName.AGENT_COMMITS_ON_A_BRANCH),
    ),
}
TIER_2_NEXT = "next: true on `main`: tier 2 runs after every merge, and a red one comes first"


def typed(repo: real.Repo, command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 # -- the binding's or the route's own command
        shlex.split(command.removeprefix("`").removesuffix("`")),
        cwd=repo.root,
        capture_output=True,
        text=True,
        check=False,
    )


def unchanged(repo: real.Repo) -> tuple[str, str, str]:
    return git(repo.root, "rev-parse", "main"), repo.head(), git(repo.root, "status", "--short")


@pytest.mark.parametrize("case", list(BRANCHES))
def test_after_the_real_merge_the_route_never_names_that_merge_again(
    tmp_path: Path, case: str
) -> None:
    world, steps = BRANCHES[case]
    repo = real.Repo.merging(tmp_path)
    for source, name, target in path_to(world):
        real.apply(repo, name, real.Change(source, target))
    for name in steps:
        (after,) = model.ACTIONS[name].successors(world)
        real.apply(repo, name, real.Change(world, after))
        world = after
    if steps:
        assert merge_ready.write_record(repo.root, json.dumps(approval("edit"))) == 0
        snapshot(repo.root, "docs(edit): the verdict record")
    branch, path = repo.head(), [case]
    ask(repo, world, path)
    ran = typed(repo, real.read(repo).command)
    assert ran.returncode == 0, ran.stdout
    step = real.read(repo)
    assert real.classify(step) is not Advice.MERGE, disagreement(world, step, [*path, "merge"])
    assert ran.stdout.splitlines()[-1] == TIER_2_NEXT, ran.stdout
    assert repo.head() == "main", f"the merge left `{branch}` checked out:\n{ran.stdout}"
    (merged,) = (
        target
        for target in model.ACTIONS[ActionName.BRANCH_MERGES].successors(world)
        if target != world
    )
    ask(repo, merged, [*path, "merge"])
    assert git(repo.root, "branch", "--list", branch), "the merge keeps the branch"
    git(repo.root, "switch", "-q", branch)
    again = replace(merged, head=Head.MERGED)
    assert again in model.graph().states or again.setup is Setup.PINNED
    path.append(f"switch to {branch}")
    ask(repo, again, path)
    was = unchanged(repo)
    typed(repo, f"{repo.entries['merge']} {branch}")
    assert unchanged(repo) == was, "a merge of a branch already merged changes nothing"
    ask(repo, again, [*path, "merge it again"])
    switched = typed(repo, real.read(repo).command)
    assert switched.returncode == 0, switched.stderr
    ask(repo, merged, [*path, "merge it again", "the switch the route names"])


def on_a_working_branch(tmp_path: Path) -> tuple[real.Repo, World]:
    world, steps = BRANCHES["a working branch"]
    repo = real.Repo.merging(tmp_path)
    for source, name, target in path_to(world):
        real.apply(repo, name, real.Change(source, target))
    for name in steps:
        (after,) = model.ACTIONS[name].successors(world)
        real.apply(repo, name, real.Change(world, after))
        world = after
    assert merge_ready.write_record(repo.root, json.dumps(approval("edit"))) == 0
    snapshot(repo.root, "docs(edit): the verdict record")
    return repo, world


def default_elsewhere(repo: real.Repo) -> None:
    git(repo.root, "worktree", "add", "-q", str(repo.root.parent / "main"), "main")


def edit_left_uncommitted(repo: real.Repo) -> None:
    (repo.root / "src/half.py").write_text("x = 1\n", encoding="utf-8")


def merged_from_main(repo: real.Repo) -> None:
    git(repo.root, "switch", "-q", "main")


SWITCH_FIRST = "next: `git switch main`, then " + TIER_2_NEXT.removeprefix("next: ")
LEFT = {
    "a dirty tree": (edit_left_uncommitted, SWITCH_FIRST, Head.MERGED, Tree.CODE),
    "the default branch in another work tree": (default_elsewhere, SWITCH_FIRST, Head.MERGED, None),
    "the default branch checked out": (merged_from_main, TIER_2_NEXT, Head.MAIN, None),
}


@pytest.mark.parametrize("case", list(LEFT))
def test_a_merge_that_does_not_switch_says_so_and_the_route_names_what_the_model_says(
    tmp_path: Path, case: str
) -> None:
    before, said, head, tree = LEFT[case]
    repo, world = on_a_working_branch(tmp_path)
    branch = repo.head()
    before(repo)
    was = repo.head()
    ran = typed(repo, f"{repo.entries['merge']} {branch}")
    assert ran.returncode == 0, ran.stdout
    assert ran.stdout.splitlines()[-1] == said, ran.stdout
    assert repo.head() == was
    landed = replace(world, head=head, tree=tree or world.tree)
    assert landed in model.graph().states
    ask(repo, landed, [case, f"merge {branch}"])


@pytest.mark.xfail(strict=True, reason="the switch is refused while another work tree holds main")
def test_the_switch_named_beside_another_checkout_of_the_default_branch_can_be_taken(
    tmp_path: Path,
) -> None:
    repo, _ = on_a_working_branch(tmp_path)
    branch = repo.head()
    default_elsewhere(repo)
    typed(repo, f"{repo.entries['merge']} {branch}")
    switched = typed(repo, real.read(repo).command)
    assert switched.returncode == 0, switched.stderr


def test_a_build_stopped_on_the_branch_it_cut_is_left_and_built_again(tmp_path: Path) -> None:
    stated = replace(IN_FLIGHT, tickets=(Ticket.STATED, Ticket.TRIAGE))
    repo = built(tmp_path, stated)
    cut = replace(stated, head=Head.MERGED)
    assert cut in model.ACTIONS[ActionName.BUILD_STOPS].successors(stated)
    real.apply(repo, ActionName.BUILD_STOPS, real.Change(stated, cut))
    assert git(repo.root, "rev-parse", "HEAD") == git(repo.root, "rev-parse", "main")
    world, labels = follow(repo, cut, 0)
    assert model.rest(world)
    assert labels[:3] == [Advice.SWITCH, Advice.BUILD, Advice.MERGE]
    assert labels.count(Advice.SWITCH) == 1


def first_outcomes_meet() -> set[Advice]:
    world, met = World(), set()
    for _ in range(2):
        while True:
            label = model.advice(world)
            met.add(label)
            world = next(model.ACTIONS[model.ADVISED[label]].successors(world))
            if model.rest(world):
                break
    return met


DETOURS = [
    Advice.FINISH_RECORDS,
    Advice.SWITCH,
    Advice.MERGE_RECORDS,
    Advice.FILL,
    Advice.REVIEW,
    Advice.SAFEGUARD,
    Advice.ARCHITECTURE,
    Advice.EXPIRE,
    Advice.BLOCKED,
    Advice.BLOCKED_RUN,
    Advice.ANSWER,
    Advice.LAND_GUARD,
    Advice.CHANGE_TICKET,
    Advice.DRAIN,
    Advice.CONJECTURE,
    Advice.FORMAL_TICKET,
    Advice.FORMAL_ROUND,
    Advice.FORMAL_UPKEEP,
    Advice.PRUNE_COMMENTS,
    Advice.PRUNE_PAGES,
]


def test_the_detours_are_the_advice_the_first_outcomes_never_reach() -> None:
    assert set(DETOURS) == set(Advice) - first_outcomes_meet()


class RouteMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.scratch = Path(tempfile.mkdtemp(prefix="route-model-"))
        self.repo = real.Repo.start(self.scratch)
        self.world = World()
        self.path: list[str] = []
        self.aims: list[Advice] = []
        self.met: set[Advice] = set()

    def teardown(self) -> None:
        shutil.rmtree(self.scratch, ignore_errors=True)

    def take(self, name: ActionName, after: World) -> None:
        real.apply(self.repo, name, real.Change(self.world, after))
        self.path.append(f"{name} ({described(self.world, after)})")
        self.world = after
        self.met.add(model.advice(after))

    @initialize(aims=st.permutations(DETOURS))
    def the_setup_is_done(self, aims: list[Advice]) -> None:
        self.aims = aims
        while not model.adopted(self.world):
            name = model.ADVISED[model.advice(self.world)]
            after = next(model.ACTIONS[name].successors(self.world))
            self.take(name, after)

    @rule(data=st.data())
    def an_action_happens(self, data: st.DataObject) -> None:
        advised = model.ADVISED[model.advice(self.world)]
        every = [
            (name, after)
            for name, act in model.ACTIONS.items()
            for after in act.successors(self.world)
        ]
        aim = next((advice for advice in self.aims if advice not in self.met), None)
        heading = (
            [
                (edge.action, edge.target)
                for edge in toward(self.world, lambda s: model.advice(s) is aim)
            ]
            if aim
            else []
        )
        followed = [option for option in every if option[0] is advised]
        pool = heading or (followed if data.draw(st.booleans()) else every)
        self.take(*data.draw(st.sampled_from(pool)))

    @invariant()
    def the_route_names_what_the_model_says(self) -> None:
        step = real.read(self.repo)
        assert real.classify(step) is model.real_advice(self.world), disagreement(
            self.world, step, self.path
        )

    @precondition(lambda self: model.adopted(self.world) and len(self.path) % HOOKS_EVERY == 0)
    @invariant()
    def without_its_commit_hooks_the_repository_is_asked_for_them_first(self) -> None:
        real.remove_hooks(self.repo)
        step = real.read(self.repo)
        real.reinstall_hooks(self.repo)
        assert (step.stage, step.then) == ("hooks", route.NEXT), disagreement(
            self.world, step, self.path
        )


def test_the_route_names_what_the_model_says_on_long_runs_of_a_real_repository() -> None:
    run_state_machine_as_test(RouteMachine, settings=STEPS)


EXPIRED_SWITCH = "old-checkout"
EXPIRED = (
    f'[[flag]]\nkey = "{EXPIRED_SWITCH}"\nowner = "the owner"\ndefault = false\n'
    'remove-by = 2000-01-01\nreason = "Hidden until the owner switches it on."\n'
)
ROUTE = Path(route.__file__)


def routed(repo: real.Repo) -> dict[str, object]:
    ran = subprocess.run(  # noqa: S603 # -- the plugin's own route, no shell
        [sys.executable, str(ROUTE), "--root", str(repo.root), "--offline", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(ran.stdout)


def with_an_expired_switch(repo: real.Repo, root: Path) -> real.Repo:
    copy = repo.copy(root)
    (copy.root / "flags.toml").write_text(EXPIRED, encoding="utf-8")
    snapshot(copy.root, "feat: a switch past its removal date")
    return copy


def test_a_switch_past_its_removal_date_outranks_no_step(tmp_path: Path) -> None:
    repo, world, asked = real.Repo.start(tmp_path), World(), 0
    for _ in range(2):
        while True:
            if model.settled(world):
                asked += 1
                expired = with_an_expired_switch(repo, tmp_path / f"expired-{asked}")
                without, beside = routed(repo), routed(expired)
                assert {k: v for k, v in beside.items() if k != "also"} == {
                    k: v for k, v in without.items() if k != "also"
                }
                shutil.rmtree(expired.root)
            name = model.ADVISED[model.advice(world)]
            after = next(model.ACTIONS[name].successors(world))
            real.apply(repo, name, real.Change(world, after))
            world = after
            if model.rest(world):
                break
    assert asked > 1


def test_the_route_names_each_switch_past_its_removal_date(tmp_path: Path) -> None:
    repo = built(tmp_path / "start", ADOPTED)
    also = routed(with_an_expired_switch(repo, tmp_path / "expired"))["also"]
    assert any(EXPIRED_SWITCH in line for line in also), also


@pytest.mark.xfail(
    strict=True,
    reason="feature_preflight.py reads the `- Criteria:` line as a conjecture: flip it with that",
)
def test_a_conjecture_that_holds_only_its_criteria_line_states_none(tmp_path: Path) -> None:
    agreed = replace(IN_FLIGHT, tickets=(Ticket.READY, Ticket.TRIAGE))
    repo = built(tmp_path, agreed)
    text = repo.ticket_text(0, Ticket.READY, inherits=False)
    lined = f"{text}\n## Conjecture\n\n{real.criteria_line(text, real.CRITERIA)}"
    (repo.root / repo.ticket_path(0)).write_text(lined, encoding="utf-8")
    snapshot(repo.root, "docs(tracker): a conjecture of its criteria line alone")
    assert real.classify(real.read(repo)) is Advice.CONJECTURE


def criteria_current(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    (line,) = real.CRITERIA_LINE.findall(text)
    heading = line.split("`")[1]
    return line == real.criteria_line(text, heading).rstrip("\n")


HOLDERS = {"issues/01-a1.md": False, "spec.md": True}


@pytest.mark.parametrize("holder", list(HOLDERS))
def test_the_criteria_line_the_world_writes_is_read_as_the_skill_reads_it(
    tmp_path: Path, holder: str
) -> None:
    tickets = (Ticket.STALE, Ticket.TRIAGE)
    stale = replace(IN_FLIGHT, tickets=tickets, inherits=HOLDERS[holder])
    repo = real.Repo.start(tmp_path)
    path = repo.root / ".scratch/f1" / holder
    for source, name, target in path_to(stale):
        if name is ActionName.OWNER_EDITS_CRITERIA:
            assert criteria_current(path)
        real.apply(repo, name, real.Change(source, target))
    assert not criteria_current(path)
    (stated,) = model.ACTIONS[ActionName.OWNER_STATES_CONJECTURE].successors(stale)
    real.apply(repo, ActionName.OWNER_STATES_CONJECTURE, real.Change(stale, stated))
    assert criteria_current(path)
    assert real.classify(real.read(repo)) is model.real_advice(stated) is Advice.BUILD
