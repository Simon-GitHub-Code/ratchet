"""Application types: which Gates rows apply to a repository, and what a row may then say."""

import re
import tomllib
from pathlib import Path

import pytest

import application_types
from application_types import Catalogue
from tests.repos import repository

Out = pytest.CaptureFixture[str]
SKILLS = Path(__file__).resolve().parents[2] / "skills"
TEMPLATE_BINDING = (SKILLS / "adopt/templates/docs/process/binding.md").read_text(encoding="utf-8")
QUESTION = re.compile(r"^\| ([^|]+?) \| ", re.MULTILINE)
SHIPPED = application_types.load()
CATALOGUE = Catalogue(
    types={"service": "answers requests", "app": "has a screen", "library": "is imported"},
    narrowed={"load": frozenset({"service"}), "a11y": frozenset({"service", "app"})},
    collections=frozenset({"tests"}),
)
ROWS = [("load", "k6"), ("a11y", "axe"), ("tests", "pytest"), ("lint", "ruff")]
ADRS = {1, 2}


def failures(types: str | None, rows: list[tuple[str, str]] | None = None) -> list[str]:
    binding = "# Binding\n\n" + ("" if types is None else f"- Application types: {types}\n")
    return list(CATALOGUE.failures(binding, ROWS if rows is None else rows, ADRS))


def bound(question: str, tool: str) -> list[tuple[str, str]]:
    return [(name, tool if name == question else cell) for name, cell in ROWS]


def test_the_shipped_catalogue_names_six_types_and_only_rows_of_the_binding_template() -> None:
    # Why: a question the catalogue misspells would apply to every type again, silently.
    rows = set(QUESTION.findall(TEMPLATE_BINDING))
    assert list(SHIPPED.types) == ["service", "web-ui", "app", "cli", "job", "library"]
    assert SHIPPED.questions <= rows, sorted(SHIPPED.questions - rows)
    for question, asked_of in SHIPPED.narrowed.items():
        assert asked_of < set(SHIPPED.types), question
    assert SHIPPED.asked_of("contract drift") == {"service"}
    assert SHIPPED.asked_of("format") == set(SHIPPED.types)
    assert {"fuzzing", "performance budget", "end to end"} <= SHIPPED.collections
    assert "contract drift" not in SHIPPED.collections


def test_the_catalogue_file_lists_each_question_once() -> None:
    data = tomllib.loads(application_types.CATALOGUE.read_text(encoding="utf-8"))
    names = [question["name"] for question in data["question"]]
    assert len(names) == len(set(names))
    keys = {"name", "applies-to", "collection", "directives", "proofs", "trigger"}
    assert all(set(question) <= keys for question in data["question"])


def test_every_proof_a_question_requires_is_described_and_every_description_is_required() -> None:
    required = {proof for proofs in SHIPPED.proofs.values() for proof in proofs}
    assert required == set(SHIPPED.meanings)
    assert SHIPPED.proofs == {
        "telemetry emitted": ("outside-the-seam",),
        "changed flags off and on": ("broken-flag-state",),
        "load smoke": ("erroring-route",),
    }
    assert set(SHIPPED.proofs) <= SHIPPED.questions
    assert Catalogue({}, {}, frozenset(), proofs={"only proofs": ("x",)}).questions == {
        "only proofs"
    }


def test_a_binding_declares_its_types_on_one_line() -> None:
    assert application_types.declared("# Binding\n") is None
    assert application_types.declared("- Application types: service, `app`\n") == ["service", "app"]
    assert application_types.declared("- Application types:\n") == []
    assert failures("service, app") == []


def test_a_missing_line_an_empty_one_and_an_unknown_type_are_red() -> None:
    known = "name one or more of service, app, library"
    assert failures(None) == [f"has no '- Application types:' line; {known}"]
    assert failures("") == [f"'- Application types:' names nothing; {known}"]
    assert failures("service, website") == [f"'- Application types:' names 'website'; {known}"]
    assert failures("<one or more of service, app>")[0].startswith("'- Application types:' names")


def test_a_missing_or_unknown_types_line_hides_no_row_that_does_not_need_it() -> None:
    dropped = [
        f"the Gates table has no '{question}' row; a row is bound or none, never dropped"
        for question in ("a11y", "load", "tests")
    ]
    rows = [("lint", "")]
    assert failures(None, rows)[1:] == [
        *dropped,
        "gate 'lint' names no tool: an empty Tool cell is an unfinished binding",
    ]
    waiting = [("a11y", "axe"), ("tests", "pytest"), ("lint", "none")]
    assert failures("website", waiting) == [
        "'- Application types:' names 'website'; name one or more of service, app, library",
        dropped[1],
    ]
    assert CATALOGUE.types_problem("- Application types: app\n") is None


def test_a_row_the_catalogue_names_is_never_dropped() -> None:
    assert failures("library", [("lint", "ruff")]) == [
        f"the Gates table has no '{question}' row; a row is bound or none, never dropped"
        for question in ("a11y", "load", "tests")
    ]


def test_an_empty_tool_cell_is_red() -> None:
    assert failures("service", bound("lint", "")) == [
        "gate 'lint' names no tool: an empty Tool cell is an unfinished binding"
    ]


def test_a_row_that_applies_needs_a_reason_and_an_adr_the_repository_holds() -> None:
    assert failures("service", bound("load", "none")) == [
        "gate 'load' is bound to none without a reason of 10+ characters"
    ]
    assert failures("service", bound("load", "none: nothing to load here")) == [
        "gate 'load' is bound to none and names no ADR that accepts the gap"
    ]
    assert failures("service", bound("load", "none: not a service")) == [
        "gate 'load' is bound to none and names no ADR that accepts the gap"
    ]
    assert failures("service", bound("load", "none: no load tool yet; ADR 0007")) == [
        "gate 'load' is bound to none under ADR 0007, which docs/adr does not hold"
    ]
    assert failures("service", bound("load", "none: no load tool yet; ADR 0002 accepts it")) == []
    assert failures("service", bound("lint", "none until a linter exists, ADR-0001")) == []
    assert failures("service", bound("lint", "nonesuch, a linter")) == []


def test_a_row_that_does_not_apply_may_say_which_type_it_is_not() -> None:
    assert failures("library", bound("load", "none: not a service")) == []
    assert failures("library", bound("a11y", "none: not an app")) == []
    assert failures("app, library", bound("load", "none: not a service")) == []
    # Why: the reason names a type the question is asked of, never just any type.
    hint = "; it is not asked of these types, so write `none: not a service`"
    assert failures("library", bound("load", "none: not an app")) == [
        f"gate 'load' is bound to none and names no ADR that accepts the gap{hint}"
    ]
    assert failures("library", bound("load", "none")) == [
        f"gate 'load' is bound to none without a reason of 10+ characters{hint}"
    ]
    assert failures("library", bound("load", "none: not a serviceable thing")) == [
        f"gate 'load' is bound to none and names no ADR that accepts the gap{hint}"
    ]
    assert failures("library", bound("load", "none: load is out of scope; ADR 0001")) == []
    assert failures("library", bound("load", "k6 anyway")) == []


def test_the_command_prints_the_types_then_the_rows_they_leave_out(capsys: Out) -> None:
    assert application_types.main(["application_types"]) == 0
    assert (
        capsys.readouterr().out.splitlines()[5]
        == "library: code other code imports; nothing to boot"
    )
    assert application_types.main(["application_types", "cli", "library"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "contract drift: none: not a service" in lines
    assert "deep interface conformance: applies" in lines
    assert "accessibility: none: not a web-ui or an app" in lines
    assert "telemetry emitted: none: not a service, a web-ui, an app or a job" in lines
    assert len(lines) == len(SHIPPED.narrowed)
    assert application_types.main(["application_types", "website"]) == 2
    assert capsys.readouterr().out == (
        "application_types: 'website' is not one of service, web-ui, app, cli, job, library\n"
    )


def test_life_events_are_read_from_the_binding_and_default_to_none() -> None:
    assert application_types.life_events("# Binding\n") == frozenset()
    assert application_types.life_events("- Life events: none\n") == frozenset()
    assert application_types.life_events("- Life events: deployed, `depended-on`\n") == {
        "deployed",
        "depended-on",
    }


def test_an_event_owes_the_questions_whose_trigger_names_it() -> None:
    owed = SHIPPED.owed_by("deployed")
    assert "rolls back safely" in owed, "a trigger joined to a kind of code still names the event"
    assert all("life:deployed" in SHIPPED.triggers[question] for question in owed)
    assert SHIPPED.owed_by("famous") == ()


def test_the_signs_of_an_undeclared_event_are_the_first_paths_the_tree_matches(
    tmp_path: Path,
) -> None:
    binding = "- Application types: cli\n- Life events: depended-on\n"
    files = {
        "docs/process/binding.md": binding,
        "fly.toml": "x",
        "deploy/up.sh": "x",
        "SECURITY.md": "x",
    }
    root = repository(tmp_path / "repo", files)
    assert application_types.signs_seen(root) == {
        "deployed": {"fly.toml": "fly.toml", "deploy/**": "deploy/up.sh"}
    }
    assert application_types.signs_seen(tmp_path) == {}


def test_a_missing_events_line_declares_every_event_to_a_judge() -> None:
    every = frozenset(application_types.load().events)
    assert application_types.declared_or_all("# Binding\n") == every
    assert application_types.declared_or_all("- Life events: none\n") == frozenset()
    assert application_types.declared_or_all("- Life events: deployed\n") == {"deployed"}


TRIGGERED = Catalogue(
    types={"service": "answers requests", "cli": "a command", "library": "is imported"},
    narrowed={"listen": frozenset({"service"})},
    collections=frozenset(),
    triggers={
        "listen": "code:listener",
        "restore": "life:irreplaceable-data",
        "format": "always",
        "dial": "code:outbound-call",
    },
    kinds={"listener": "a server", "outbound-call": "a call out"},
    events={"irreplaceable-data": application_types.Event("users' records", ("migrations/**",))},
)


def later(question: str, tool: str, types: str = "service") -> list[str]:
    binding = f"# Binding\n\n- Application types: {types}\n"
    rows = [(name, tool if name == question else "a tool") for name in TRIGGERED.questions]
    return list(TRIGGERED.failures(binding, rows, ADRS))


def test_the_shipped_catalogue_gives_every_question_a_trigger_it_can_fire() -> None:
    data = tomllib.loads(application_types.CATALOGUE.read_text(encoding="utf-8"))
    assert set(SHIPPED.triggers) == {question["name"] for question in data["question"]}
    assert {SHIPPED.trigger_problem(trigger) for trigger in SHIPPED.triggers.values()} == {None}
    assert SHIPPED.trigger_of("format") == "always"
    assert SHIPPED.trigger_of("rolls back safely") == "life:deployed and code:stored-format"
    assert SHIPPED.trigger_of("production answers") == "life:deployed"
    assert SHIPPED.trigger_of("incidents tighten a gate") == "always"
    assert {"deployed", "irreplaceable-data", "depended-on"} == set(SHIPPED.events)
    assert "outbound-call" in SHIPPED.kinds
    assert "migrations/**" in SHIPPED.events["irreplaceable-data"].signs
    # Why: with no signs the route could never propose `depended-on`, and ADR 0028's refuter
    # (a project people rely on is asked its depended-on questions) could not be tried.
    assert "CONTRIBUTING.md" in SHIPPED.events["depended-on"].signs
    assert "README.md" not in SHIPPED.events["depended-on"].signs


def test_a_trigger_the_catalogue_cannot_fire_is_named() -> None:
    assert SHIPPED.trigger_problem("always") is None
    assert SHIPPED.trigger_problem("code:listener") is None
    assert SHIPPED.trigger_problem("life:deployed") is None
    for wrong in ("code:nothing", "life:famous", "sometimes", "code:", "deployed"):
        assert SHIPPED.trigger_problem(wrong) == (
            f"'{wrong}' is not `always`, `code:<kind>` or `life:<event>` of this catalogue,"
            " or such parts joined by ` and `"
        )


def test_a_compound_trigger_is_valid_when_every_part_is_and_never_holds_always() -> None:
    assert SHIPPED.trigger_problem("life:deployed and code:stored-format") is None
    assert application_types.parts("life:deployed and code:listener") == [
        "life:deployed",
        "code:listener",
    ]
    assert application_types.code_kinds("life:deployed and code:listener") == ["listener"]
    assert application_types.code_kinds("life:deployed") == []
    for wrong in (
        "always and code:listener",
        "life:deployed and always",
        "life:deployed and code:nothing",
        "life:famous and code:listener",
    ):
        assert SHIPPED.trigger_problem(wrong) is not None


def test_the_kinds_a_repository_must_find_are_those_its_types_are_asked_about() -> None:
    assert TRIGGERED.kinds_asked({"service"}) == {"listener", "outbound-call"}
    assert TRIGGERED.kinds_asked({"cli"}) == {"outbound-call"}
    assert TRIGGERED.kinds_asked(set()) == set()


def test_every_code_part_of_a_compound_trigger_is_a_kind_to_find() -> None:
    both = Catalogue(
        types={"service": "answers requests", "library": "is imported"},
        narrowed={"keep": frozenset({"service"})},
        collections=frozenset(),
        triggers={"keep": "life:deployed and code:stored-format", "two": "code:a and code:b"},
        kinds={"stored-format": "a file", "a": "a", "b": "b"},
        events={"deployed": application_types.Event("reachable", ())},
    )
    assert both.kinds_asked({"service"}) == {"stored-format", "a", "b"}
    assert both.kinds_asked({"library"}) == {"a", "b"}


def test_a_compound_later_cell_must_equal_the_whole_compound() -> None:
    assert (
        SHIPPED.later_problem("rolls back safely", "life:deployed and code:stored-format") is None
    )
    assert SHIPPED.later_problem("rolls back safely", "life:deployed") == (
        "gate 'rolls back safely' says `later: life:deployed`, but its trigger is"
        " `life:deployed and code:stored-format`:"
        " write `later: life:deployed and code:stored-format`"
    )


def test_a_later_row_must_name_the_trigger_of_its_question_and_only_when_asked() -> None:
    assert later("listen", "later: code:listener") == []
    assert later("listen", "`later: code:listener`") == []
    assert later("restore", "later: life:irreplaceable-data", "cli") == []
    assert later("listen", "later: code:outbound-call") == [
        (
            "gate 'listen' says `later: code:outbound-call`, but its trigger is `code:listener`:"
            " write `later: code:listener`"
        )
    ]
    assert later("listen", "later: code:listener because") == [
        (
            "gate 'listen' says `later: code:listener because`, but its trigger is `code:listener`:"
            " write `later: code:listener`"
        )
    ]
    assert later("format", "later: code:listener") == [
        "gate 'format' says `later: code:listener`, but it is asked from the first commit: bind it"
    ]
    # A bound row for a type that is not asked is already accepted, so a waiting one is too.
    assert later("listen", "later: code:listener", "cli") == []


def test_a_catalogue_without_triggers_asks_every_question_always() -> None:
    plain = Catalogue({"cli": "a command"}, {}, frozenset({"tests"}))
    assert plain.trigger_of("tests") == "always"
    assert plain.questions == {"tests"}
    assert plain.kinds_asked({"cli"}) == set()
    assert application_types.dropped("tests").startswith("the Gates table has no 'tests' row")
