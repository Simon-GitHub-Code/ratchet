"""``merge_ready.py``: the merge judge, one test per way a change is the owner's to merge.

Every test builds a real repository on ``main`` and a branch off it, since the judge reads
commits and nothing else. The names say what a misbehaving agent would try.
"""

import io
import json
import os
from pathlib import Path

import pytest

import merge_ready
from tests.repos import branch, commit, git, repository, write

MANIFEST = """\
[[group]]
name = "tool-config"
reason = "Rule sets and dependencies of every gate."
paths = ["pyproject.toml", "lint.toml"]

[[group]]
name = "gate-commands"
reason = "The command contract."
paths = ["justfile", "docs/process/**", ".github/**"]

[[group]]
name = "ratchets"
reason = "Recorded thresholds only ever rise."
paths = ["quality/**"]

[[group]]
name = "records"
reason = "An agent adds a term; the owner changes one."
edit = "append"
paths = ["GLOSSARY.md", "docs/adr/**"]

[[door]]
name = "one-way"
reason = "A migration cannot be taken back once it has run"
paths = ["migrations/**"]

[[door]]
name = "stored-data"
reason = "Old data must still load"
on = "rewrite"
paths = ["tests/data/**"]

[[door]]
name = "contracts"
reason = "A consumer depends on every line"
on = "removal"
paths = ["tests/contract/**"]
"""
GLOSSARY = "# Glossary\n\n## Language\n\n**Customer**:\nWho buys.\n_Avoid_: client, buyer\n"
ADR = "# We keep it plain\n\nBecause.\n\n## Refutation criterion\n\nA fancy one that works.\n"
THRESHOLDS = (
    '[coverage.core]\npaths = ["src/**"]\nabsolute = true\nline = 80.0\n\n'
    "[ceilings.seconds]\ntier1 = 300\n"
)
PYPROJECT = '[project]\nname = "a"\nversion = "1"\ndependencies = ["attrs>=23"]\n'
FLAGS = (
    '[[flag]]\nkey = "dark"\nowner = "core"\ndefault = false\nremove-by = 2999-01-01\n'
    'reason = "Ships dark until it is measured."\n\n'
    '[[flag]]\nkey = "lit"\nowner = "core"\ndefault = true\nremove-by = 2999-01-01\n'
    'reason = "Already on for everyone, due out."\n'
)
NEW_FLAG = (
    '\n[[flag]]\nkey = "fresh"\nowner = "core"\ndefault = false\nremove-by = 2999-01-01\n'
    'reason = "Ships dark until it is measured."\n'
)
NEW_FLAG_ON = NEW_FLAG.replace("default = false", "default = true")
API = '{"paths": {"/a": {"get": {"tags": ["a"], "ok": true}}}}\n'
MORE_API = '{"paths": {"/a": {"get": {"tags": ["a", "b"], "ok": true}}, "/b": {}}, "info": 1}\n'
SURFACE = "pkg.a.f(x)\npkg.a.g(x)\n"
MORE_SURFACE = "pkg.a.e(x)\n" + SURFACE + "pkg.b.h()\n"
BINDING = "docs/process/binding.md"
LIVE = "- Life events: deployed\n"
UNFLAGGED = (
    "unflagged: {surface}: the change adds surface a consumer can meet, and it neither declares a"
    " flag that is off in flags.toml nor names one in the code it adds; ship it dark, or the owner"
    " merges it"
)
DISPUTE = "# 0001: the limit\n\n- Status: {status}\n\n## Gate\n\n`lint.toml` and src/a.py.\n"
BASE = {
    "guards/manifest.toml": MANIFEST,
    "GLOSSARY.md": GLOSSARY,
    "docs/adr/0001-plain.md": ADR,
    "docs/disputes/.gitkeep": "",
    "quality/thresholds.toml": THRESHOLDS,
    "pyproject.toml": PYPROJECT,
    "lint.toml": "strict = true\n",
    "justfile": "check:\n    true\n",
    "flags.toml": FLAGS,
    BINDING: f"# Binding\n\n- Application types: service, library\n{LIVE}",
    "src/a.py": "x = 1\n",
    "tests/contract/api.json": API,
    "tests/contract/surface.txt": SURFACE,
    "tests/data/v1.json": '{"v": 1}\n',
    "migrations/0001_first.sql": "create table a (id int);\n",
}
VERDICT = {
    "slug": "thing",
    "ticket": "4",
    "verdict": "approve",
    "unresolved": [],
    "openQuestions": [],
    "assumptions": [{"question": "q", "assumed": "a", "refuter": "r"}],
}
RECORD = "docs/features/thing/verdict.json"
ONE_WAY = "A migration cannot be taken back once it has run"
REWRITE = "rewrites or deletes a file the default branch holds. Old data must still load"
CONSUMER = "A consumer depends on every line"
OPEN_DISPUTE = "dispute: docs/disputes/0001-limit.md is open and names src/a.py"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    repository(tmp_path, dict(BASE))
    branch(tmp_path, "feature/4-thing")
    return tmp_path


def stamp(root: Path, verdict: dict[str, object] | None = None) -> None:
    """Write the verdict record as the orchestrator does, and commit it alone."""
    assert merge_ready.write_record(root, json.dumps(verdict or VERDICT)) == 0
    commit(root, "docs(thing): the verdict record")


def change(root: Path, files: dict[str, str | None], *, record: bool = True) -> None:
    """Commit ``files`` on the branch (``None`` deletes one), then the verdict record."""
    for path, text in files.items():
        if text is None:
            (root / path).unlink()
        else:
            write(root, path, text)
    commit(root, "feat(thing): the change")
    if record:
        stamp(root)


def judged(root: Path, **asked: object) -> list[str]:
    reasons = merge_ready.judge(root, "main", "HEAD", asked=merge_ready.Asked(**asked))  # type: ignore[arg-type]
    return [str(reason) for reason in reasons]


def codes(root: Path) -> list[str]:
    return [reason.code for reason in merge_ready.judge(root, "main", "HEAD")]


def link(root: Path, path: str, target: str) -> None:
    """Make ``path`` a symbolic link to ``target``, which may be any text, line breaks included."""
    (root / path).unlink(missing_ok=True)
    (root / path).symlink_to(target)


# --- what merges without the owner ------------------------------------------------------------


def test_a_clean_change_with_its_verdict_record_is_the_agents(root: Path) -> None:
    change(root, {"src/a.py": "x = 2\n", "docs/features/thing/spec.md": "# Thing\n"})
    assert judged(root) == []


def test_an_added_term_and_a_new_adr_are_no_guard_change(root: Path) -> None:
    term = GLOSSARY + "\n**Order**:\nWhat a customer places.\n_Avoid_: purchase\n"
    change(root, {"GLOSSARY.md": term, "docs/adr/0002-orders.md": ADR, "src/a.py": "x = 2\n"})
    assert judged(root) == []


def test_a_threshold_that_only_tightened_is_no_guard_change(root: Path) -> None:
    raised = THRESHOLDS.replace("line = 80.0", "line = 91.5").replace("tier1 = 300", "tier1 = 120")
    change(root, {"quality/thresholds.toml": raised, "src/a.py": "x = 2\n"})
    assert judged(root) == []


def test_a_new_flag_that_is_off_is_two_way(root: Path) -> None:
    change(root, {"flags.toml": FLAGS + NEW_FLAG})
    assert judged(root) == []


def test_additions_to_a_contract_behind_a_new_flag_and_a_new_fixture_are_two_way(
    root: Path,
) -> None:
    files = {
        "tests/contract/api.json": MORE_API,
        "tests/contract/surface.txt": MORE_SURFACE,
        "tests/contract/new.txt": "  anything goes in a new file\n",
        "tests/data/v2.json": '{"v": 2}\n',
        "flags.toml": FLAGS + NEW_FLAG,
    }
    change(root, files)
    assert judged(root) == []


@pytest.mark.parametrize(
    "files",
    [
        {"src/a.py": 'x = 2 if flag("dark") else 1\n'},
        {"src/a.py": "x = 1\n", "src/b.py": 'y = flag("dark")\n'},
    ],
)
def test_new_surface_whose_added_code_names_a_flag_that_is_off_is_two_way(
    root: Path, files: dict[str, str | None]
) -> None:
    change(root, {"tests/contract/surface.txt": MORE_SURFACE, **files})
    assert judged(root) == []


def test_a_snapshot_rewritten_to_say_the_same_adds_no_surface(root: Path) -> None:
    files = {
        "tests/contract/api.json": '{\n"paths": {"/a": {"get": {"ok": true, "tags": ["a"]}}}\n}\n',
        "tests/contract/surface.txt": "\n" + SURFACE.replace("\n", "\n\n"),
    }
    change(root, files)
    assert judged(root) == []


@pytest.mark.parametrize("kinds", ["library", "`library`"])
def test_a_library_is_not_asked_for_a_flag(tmp_path: Path, kinds: str) -> None:
    repository(tmp_path, BASE | {BINDING: f"# Binding\n\n- Application types: {kinds}\n"})
    branch(tmp_path, "feature/4-thing")
    change(tmp_path, {"tests/contract/surface.txt": MORE_SURFACE})
    assert judged(tmp_path) == []


# --- guard paths ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("files", "reason"),
    [
        ({"justfile": "check:\n    false\n"}, "guard: justfile (gate-commands)"),
        (
            {".github/workflows/ci.yml": "name: ci\n"},
            "guard: .github/workflows/ci.yml (gate-commands)",
        ),
        ({"justfile": None}, "guard: justfile (gate-commands)"),
        (
            {"guards/manifest.toml": MANIFEST.replace('"justfile", ', "")},
            "guard: guards/manifest.toml (harness)",
        ),
        (
            {"Justfile": "check:\n    true\n"},
            "guard: Justfile (gate-commands): matches a guard pattern but for its case",
        ),
        (
            {"glossary.md": GLOSSARY},
            "guard: glossary.md (records): matches a guard pattern but for its case",
        ),
        (
            {"GLOSSARY.md": GLOSSARY.replace("Who buys.", "Who pays.")},
            "guard: GLOSSARY.md (records): not additive: it changes the definition of Customer",
        ),
        (
            {"quality/mutation-register.toml": "# Register\n"},
            "guard: quality/mutation-register.toml (ratchets)",
        ),
    ],
)
def test_a_changed_guard_path_is_the_owners(
    root: Path, files: dict[str, str | None], reason: str
) -> None:
    change(root, files)
    assert judged(root) == [reason]


def test_a_rename_that_moves_a_guard_path_away_is_seen(root: Path) -> None:
    git(root, "mv", "justfile", "build.just")
    change(root, {})
    assert judged(root) == ["guard: justfile (gate-commands)"]


@pytest.mark.parametrize(
    ("after", "why"),
    [
        (
            THRESHOLDS.replace("line = 80.0", "line = 79.0"),
            "coverage.core.line: lowered from 80.0 to 79.0",
        ),
        (
            THRESHOLDS.replace("tier1 = 300", "tier1 = 301"),
            "ceilings.seconds.tier1: ceiling raised from 300.0 to 301.0",
        ),
        (
            THRESHOLDS.replace("absolute = true", "absolute = false"),
            "coverage.core.absolute: turned off",
        ),
        (
            THRESHOLDS + "tier2 = 9\n",
            "a key, a path or a text value changed, where a ratchet moves numbers only",
        ),
        (
            THRESHOLDS.replace("src/**", "src/core/**"),
            "a key, a path or a text value changed, where a ratchet moves numbers only",
        ),
        ("[coverage\n", "the file does not parse on one side"),
        (None, "the file is new, gone or not text"),
    ],
)
def test_a_threshold_change_that_is_more_than_a_ratchet_is_the_owners(
    root: Path, after: str | None, why: str
) -> None:
    change(root, {"quality/thresholds.toml": after})
    assert judged(root) == [f"guard: quality/thresholds.toml (ratchets): {why}"]


def test_a_dependency_is_the_owners_whoever_named_it(root: Path) -> None:
    added = PYPROJECT.replace('"attrs>=23"', '"attrs>=23", "pytest-skip-all>=1"')
    change(
        root, {"pyproject.toml": added, "docs/features/thing/spec.md": "Needs pytest-skip-all.\n"}
    )
    assert judged(root) == [
        (
            "guard: pyproject.toml (tool-config): a dependency is the owner's to merge:"
            " pytest-skip-all (project.dependencies) (none) -> pytest-skip-all>=1"
        )
    ]


def test_a_manifest_change_that_is_more_than_a_dependency_says_so(root: Path) -> None:
    change(root, {"pyproject.toml": PYPROJECT + "\n[tool.ruff]\nselect = []\n"})
    (reason,) = judged(root)
    assert reason.startswith("guard: pyproject.toml (tool-config): not dependency-only: ")


# --- one-way doors ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("files", "reason"),
    [
        (
            {"migrations/0002_more.sql": "alter table a add b int;\n"},
            f"door: migrations/0002_more.sql (one-way): {ONE_WAY}",
        ),
        (
            {"Migrations/0002_more.sql": "alter table a add b int;\n"},
            f"door: Migrations/0002_more.sql (one-way): {ONE_WAY}",
        ),
        (
            {"tests/data/v1.json": '{"v": 1, "w": 2}\n'},
            (f"door: tests/data/v1.json (stored-data): {REWRITE}"),
        ),
        (
            {"tests/data/v1.json": None},
            (f"door: tests/data/v1.json (stored-data): {REWRITE}"),
        ),
        (
            {"tests/contract/api.json": None},
            f"door: tests/contract/api.json (contracts): deletes it. {CONSUMER}",
        ),
        (
            {"tests/contract/api.json": '{"paths": {"/a": {"get": {"tags": ["a"]}}}}\n'},
            (
                "door: tests/contract/api.json (contracts): a key, an item or a value the default"
                " branch holds is gone. A consumer depends on every line"
            ),
        ),
        (
            {"tests/contract/api.json": API.replace('["a"]', '["b"]')},
            (
                "door: tests/contract/api.json (contracts): a key, an item or a value the default"
                " branch holds is gone. A consumer depends on every line"
            ),
        ),
        (
            {"tests/contract/api.json": '{"paths": {"/a": {"get": {"tags": "a", "ok": true}}}}\n'},
            (
                "door: tests/contract/api.json (contracts): a key, an item or a value the default"
                " branch holds is gone. A consumer depends on every line"
            ),
        ),
        (
            {"tests/contract/api.json": '{"paths": {"/a": {"get": {"tags": ["a"], "ok": 1}}}}\n'},
            (
                "door: tests/contract/api.json (contracts): a key, an item or a value the default"
                " branch holds is gone. A consumer depends on every line"
            ),
        ),
        (
            {"tests/contract/api.json": '{"paths": []}\n'},
            (
                "door: tests/contract/api.json (contracts): a key, an item or a value the default"
                " branch holds is gone. A consumer depends on every line"
            ),
        ),
        (
            {"tests/contract/api.json": API.replace('"ok": true', '"ok": true, "ok": true')},
            (
                "door: tests/contract/api.json (contracts): one side is not JSON, or gives a key"
                " twice. A consumer depends on every line"
            ),
        ),
        (
            {"tests/contract/api.json": "not json\n"},
            (
                "door: tests/contract/api.json (contracts): one side is not JSON, or gives a key"
                " twice. A consumer depends on every line"
            ),
        ),
        (
            {"tests/contract/surface.txt": "pkg.a.f(x)\n"},
            (
                "door: tests/contract/surface.txt (contracts): a line the default branch holds is"
                " gone, changed or moved. A consumer depends on every line"
            ),
        ),
        (
            {"tests/contract/surface.txt": "pkg.a.g(x)\npkg.a.f(x)\n"},
            (
                "door: tests/contract/surface.txt (contracts): a line the default branch holds is"
                " gone, changed or moved. A consumer depends on every line"
            ),
        ),
        (
            {"tests/contract/surface.txt": SURFACE + "  pkg.a.h()\n"},
            (
                "door: tests/contract/surface.txt (contracts): a line is indented, so its meaning"
                " hangs on its neighbours; snapshot it as JSON. A consumer depends on every line"
            ),
        ),
        (
            {"tests/contract/surface.txt": SURFACE + "pkg.b()\npkg.b()\n"},
            (
                "door: tests/contract/surface.txt (contracts): a line is repeated, so one removed"
                " could hide behind one added. A consumer depends on every line"
            ),
        ),
    ],
)
def test_a_change_through_a_one_way_door_is_the_owners(
    root: Path, files: dict[str, str | None], reason: str
) -> None:
    change(root, files)
    assert judged(root) == [reason]


def test_a_contract_that_is_not_text_cannot_be_called_an_addition(root: Path) -> None:
    (root / "tests/contract/api.json").write_bytes(b"\xff\xfe\x00")
    change(root, {})
    assert judged(root) == [
        (
            "door: tests/contract/api.json (contracts): one side is not text the judge can read."
            " A consumer depends on every line"
        )
    ]


@pytest.mark.parametrize(
    ("after", "found"),
    [
        (
            FLAGS.replace("default = false", "default = true"),
            ["flag 'dark' is on by default, which ships its behaviour to everyone"],
        ),
        (
            FLAGS + NEW_FLAG.replace("default = false", "default = true"),
            ["flag 'fresh' is on by default, which ships its behaviour to everyone"],
        ),
        (
            FLAGS.replace("remove-by = 2999-01-01", "permanent = true", 1),
            ["flag 'dark' becomes permanent"],
        ),
        (
            FLAGS.partition("\n\n")[2],
            [
                (
                    "flag 'dark' leaves the registry while off by default: its behaviour ships on,"
                    " or goes"
                )
            ],
        ),
        (FLAGS + NEW_FLAG + NEW_FLAG, ["a flag is declared twice, so its default cannot be read"]),
        (
            "[[flag]\n",
            ["the registry is gone or does not parse, so no flag's default can be compared"],
        ),
        (None, ["the registry is gone or does not parse, so no flag's default can be compared"]),
    ],
)
def test_a_flag_that_ships_on_or_stays_for_good_is_a_one_way_door(
    root: Path, after: str | None, found: list[str]
) -> None:
    change(root, {"flags.toml": after})
    assert judged(root) == [f"door: flags.toml: {text}" for text in found]


def test_removing_a_flag_that_is_already_on_changes_nothing_for_anyone(root: Path) -> None:
    change(root, {"flags.toml": FLAGS.partition("\n\n")[0] + "\n"})
    assert judged(root) == []


# --- new surface with no flag -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("files", "surface"),
    [
        ({"tests/contract/api.json": MORE_API}, "tests/contract/api.json"),
        ({"tests/contract/surface.txt": MORE_SURFACE}, "tests/contract/surface.txt"),
        ({"tests/contract/new.txt": "pkg.c.i()\n"}, "tests/contract/new.txt"),
        ({"Tests/Contract/new.txt": "pkg.c.i()\n"}, "Tests/Contract/new.txt"),
        (
            {"tests/contract/api.json": MORE_API, "tests/contract/surface.txt": MORE_SURFACE},
            "tests/contract/api.json, tests/contract/surface.txt",
        ),
        (
            {"tests/contract/surface.txt": MORE_SURFACE, "src/a.py": 'x = flag("lit")\n'},
            "tests/contract/surface.txt",
        ),
        (
            {
                "tests/contract/surface.txt": MORE_SURFACE + "pkg.dark()\n",
                "docs/features/thing/spec.md": "Feature flag: dark.\n",
                "flags.toml": FLAGS + "# dark stays off\n",
            },
            "tests/contract/surface.txt",
        ),
        (
            {
                "tests/contract/surface.txt": MORE_SURFACE,
                "flags.toml": FLAGS.replace("default = false", "default = true") + NEW_FLAG_ON,
            },
            "tests/contract/surface.txt",
        ),
    ],
)
def test_new_surface_that_no_flag_hides_is_the_owners(
    root: Path, files: dict[str, str | None], surface: str
) -> None:
    change(root, files)
    reasons = [reason for reason in judged(root) if not reason.startswith("door: flags.toml")]
    assert reasons == [UNFLAGGED.format(surface=surface)]


def test_a_flag_the_change_stops_reading_hides_nothing(tmp_path: Path) -> None:
    repository(tmp_path, BASE | {"src/a.py": 'x = flag("dark")\n'})
    branch(tmp_path, "feature/4-thing")
    change(tmp_path, {"tests/contract/surface.txt": MORE_SURFACE, "src/a.py": "x = 1\n"})
    assert codes(tmp_path) == ["unflagged"]


@pytest.mark.parametrize(
    "binding",
    [
        f"# Binding\n{LIVE}",
        f"# Binding\n\n- Application types: library, daemon\n{LIVE}",
    ],
)
def test_a_binding_that_names_no_known_type_is_asked_for_a_flag(
    tmp_path: Path, binding: str
) -> None:
    repository(tmp_path, BASE | {BINDING: binding})
    branch(tmp_path, "feature/4-thing")
    change(tmp_path, {"tests/contract/surface.txt": MORE_SURFACE})
    assert codes(tmp_path) == ["unflagged"]


def test_the_binding_the_branch_holds_does_not_decide_the_types(root: Path) -> None:
    files = {
        "tests/contract/surface.txt": MORE_SURFACE,
        BINDING: f"# Binding\n\n- Application types: library\n{LIVE}",
    }
    change(root, files)
    assert codes(root) == ["guard", "unflagged"]


# --- what waits for a life event (ADR 0028) ----------------------------------------------------

SURFACE_CHANGE = {"tests/contract/api.json": MORE_API}
DOOR_CHANGE = {"tests/contract/surface.txt": "pkg.a.g(x)\n"}
EVENTS = "# Binding\n\n- Application types: service\n- Life events: {events}\n"


def young(tmp_path: Path, binding: str | None) -> Path:
    files = {path: text for path, text in BASE.items() if path != BINDING}
    repository(tmp_path, files | ({BINDING: binding} if binding is not None else {}))
    branch(tmp_path, "feature/4-thing")
    return tmp_path


@pytest.mark.parametrize("binding", [None, EVENTS.format(events="none")])
def test_new_surface_and_doors_wait_until_an_event_is_declared(
    tmp_path: Path, binding: str | None
) -> None:
    repo = young(tmp_path, binding)
    notes: list[str] = []
    change(repo, SURFACE_CHANGE | DOOR_CHANGE | {"flags.toml": FLAGS + NEW_FLAG_ON})
    reasons = merge_ready.judge(repo, "main", "HEAD", notes=notes)
    assert reasons == []
    assert [note.split(" let through")[0] for note in notes] == [
        "a one-way door",
        "new surface with no flag that is off",
    ]
    assert "deployed, irreplaceable-data" in notes[0]
    assert "(ADR 0028)" in notes[1]


@pytest.mark.parametrize(
    ("events", "woken"),
    [
        ("deployed", {"door", "unflagged"}),
        ("irreplaceable-data", {"door"}),
        ("depended-on", set()),
        ("depended-on, irreplaceable-data", {"door"}),
    ],
)
def test_each_event_wakes_only_the_judgement_it_guards(
    tmp_path: Path, events: str, woken: set[str]
) -> None:
    repo = young(tmp_path, EVENTS.format(events=events))
    change(repo, SURFACE_CHANGE)
    assert ("unflagged" in codes(repo)) is ("unflagged" in woken)
    repo = young(tmp_path / "second", EVENTS.format(events=events))
    change(repo, DOOR_CHANGE)
    assert ("door" in codes(repo)) is ("door" in woken)


def test_a_head_that_declares_the_event_does_not_wake_the_judge(tmp_path: Path) -> None:
    repo = young(tmp_path, EVENTS.format(events="none"))
    change(repo, SURFACE_CHANGE | DOOR_CHANGE | {BINDING: EVENTS.format(events="deployed")})
    assert codes(repo) == ["guard"]


def test_a_binding_with_no_events_line_declares_every_event(tmp_path: Path) -> None:
    # Why: a 0.10 adoption that upgrades has no line yet and may already be deployed, so a missing
    # line must keep the owner's review of doors and unflagged surface; only `none` turns it off.
    repo = young(tmp_path, "# Binding\n")
    change(repo, SURFACE_CHANGE | DOOR_CHANGE)
    assert {"door", "unflagged"} <= set(codes(repo))


@pytest.mark.parametrize("after", [None, "[[flag]\n"])
def test_a_registry_that_cannot_be_read_reaches_the_owner_before_any_event(
    tmp_path: Path, after: str | None
) -> None:
    # Why: the judge is blind to every flag then, which is no door the project has yet to meet.
    repo = young(tmp_path, EVENTS.format(events="none"))
    change(repo, {"flags.toml": after})
    assert codes(repo) == ["door"]


def test_a_branch_forked_before_the_declaration_still_meets_the_owner(tmp_path: Path) -> None:
    files = {path: text for path, text in BASE.items() if path != BINDING}
    repository(tmp_path, files | {BINDING: EVENTS.format(events="none")})
    git(tmp_path, "branch", "old-point")
    write(tmp_path, BINDING, EVENTS.format(events="deployed"))
    commit(tmp_path, "chore: declare deployed")
    git(tmp_path, "switch", "-q", "-c", "feature/4-thing", "old-point")
    change(tmp_path, DOOR_CHANGE | SURFACE_CHANGE)
    # Why: the declaration landed on the default branch after the fork, and merge.py passes no
    # `current`, so the fork point's `none` must not let the change dodge it.
    assert {"door", "unflagged"} <= set(codes(tmp_path))


def test_guard_paths_and_disputes_do_not_wait_for_an_event(tmp_path: Path) -> None:
    repo = young(tmp_path, None)
    change(
        repo,
        {
            "justfile": "check:\n    false\n",
            "docs/disputes/0001-limit.md": DISPUTE.format(status="open"),
        },
    )
    assert codes(repo) == ["guard", "dispute"]


def test_a_record_needs_no_critic_fields(root: Path) -> None:
    change(root, {"src/a.py": "x = 2\n"}, record=False)
    stamp(root, {k: v for k, v in VERDICT.items() if k != "assumptions"})
    assert codes(root) == []


def test_the_command_prints_a_note_for_what_it_let_through(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = young(tmp_path, None)
    change(repo, SURFACE_CHANGE)
    capsys.readouterr()
    assert merge_ready.main(["merge_ready", "--root", str(repo)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "merge: agent"
    assert out[1].startswith("note: new surface with no flag that is off let through:")


# --- disputes ---------------------------------------------------------------------------------


def test_a_dispute_the_change_writes_is_the_owners_to_decide(root: Path) -> None:
    change(root, {"docs/disputes/0001-limit.md": DISPUTE.format(status="rejected")})
    assert judged(root) == [
        (
            "dispute: docs/disputes/0001-limit.md: the change adds or edits a dispute, and the"
            " owner decides one"
        )
    ]


@pytest.mark.parametrize(
    ("status", "touched", "expected"),
    [
        ("open", "src/a.py", [OPEN_DISPUTE]),
        ("Open?", "src/a.py", [OPEN_DISPUTE]),
        ("upheld", "src/a.py", []),
        ("open", "src/b.py", []),
    ],
)
def test_an_open_dispute_that_names_a_touched_path_waits_for_the_owner(
    tmp_path: Path, status: str, *, touched: str, expected: list[str]
) -> None:
    files = {**BASE, "docs/disputes/0001-limit.md": DISPUTE.format(status=status)}
    repository(tmp_path, files)
    branch(tmp_path, "feature/4-thing")
    change(tmp_path, {touched: "x = 2\n"})
    assert judged(tmp_path) == expected


def test_a_dispute_with_no_status_line_counts_as_open(tmp_path: Path) -> None:
    repository(tmp_path, {**BASE, "docs/disputes/0001-limit.md": "# 0001\n\nsrc/a.py\n"})
    branch(tmp_path, "feature/4-thing")
    change(tmp_path, {"src/a.py": "x = 2\n"})
    assert codes(tmp_path) == ["dispute"]


# --- the verdict record -----------------------------------------------------------------------


def test_a_change_with_no_verdict_record_is_not_the_pipelines(root: Path) -> None:
    change(root, {"src/a.py": "x = 2\n"}, record=False)
    assert judged(root) == [
        (
            "record: no verdict record (docs/features/<slug>/verdict.json) is part of the change,"
            " so it is not the product of the pipeline"
        )
    ]


def test_a_commit_after_the_review_invalidates_the_record(root: Path) -> None:
    change(root, {"src/a.py": "x = 2\n"})
    write(root, "src/a.py", "x = 3\n")
    commit(root, "feat(thing): one more thing")
    (reason,) = judged(root)
    assert reason.startswith(f"record: {RECORD}: it certifies tree ")
    assert reason.endswith("a commit came after the review, or the record is another change's")


def test_a_record_copied_from_another_feature_names_another_tree(root: Path) -> None:
    change(root, {"src/a.py": "x = 2\n"})
    approved = (root / RECORD).read_text(encoding="utf-8")
    git(root, "checkout", "-q", "-b", "feature/5-other", "main")
    change(root, {"src/a.py": "x = 666\n", RECORD: approved}, record=False)
    assert codes(root) == ["record"]
    change(root, {"docs/features/other/verdict.json": approved}, record=False)
    assert judged(root) == [
        (
            "record: the change touches 2 verdict records (docs/features/other/verdict.json,"
            f" {RECORD}), and one change carries one"
        )
    ]


def test_a_record_under_another_slug_is_not_this_features(root: Path) -> None:
    write(root, "src/a.py", "x = 2\n")
    commit(root, "feat(thing): the change")
    stamp(root, {**VERDICT, "slug": "other"})
    git(root, "mv", "docs/features/other", "docs/features/thing")
    commit(root, "docs(thing): move the record")
    reasons = judged(root)
    assert reasons[0] == f"record: {RECORD}: it names the feature 'other' and sits under 'thing'"


@pytest.mark.parametrize(
    ("verdict", "problem"),
    [
        ({"verdict": "request_changes"}, "its verdict is 'request_changes', not 'approve'"),
        (
            {"unresolved": ["wrong total", "no test"]},
            "2 unresolved blocker or major finding(s): wrong total; no test",
        ),
        ({"openQuestions": ["keep the old codes?"]}, "1 open question(s): keep the old codes?"),
        ({"unresolved": 0}, "its `unresolved` is not a list of texts"),
        ({"openQuestions": [1]}, "its `openQuestions` is not a list of texts"),
    ],
)
def test_a_record_that_is_not_a_clean_approval_is_the_owners(
    root: Path, verdict: dict[str, object], problem: str
) -> None:
    write(root, "src/a.py", "x = 2\n")
    commit(root, "feat(thing): the change")
    stamp(root, {**VERDICT, **verdict})
    assert judged(root) == [f"record: {RECORD}: {problem}"]


@pytest.mark.parametrize("text", ["not json\n", "[]\n"])
def test_a_record_that_is_not_an_object_is_no_record(root: Path, text: str) -> None:
    change(root, {"src/a.py": "x = 2\n", RECORD: text}, record=False)
    assert judged(root) == [f"record: {RECORD} is gone or is not a JSON object"]


def test_deleting_a_merged_record_is_not_a_record(tmp_path: Path) -> None:
    repository(tmp_path, {**BASE, RECORD: "{}\n"})
    branch(tmp_path, "feature/4-thing")
    change(tmp_path, {RECORD: None}, record=False)
    assert judged(tmp_path) == [f"record: {RECORD} is gone or is not a JSON object"]


# --- what the caller knows --------------------------------------------------------------------


def test_a_pull_request_that_targets_another_branch_is_the_owners(root: Path) -> None:
    change(root, {"src/a.py": "x = 2\n"})
    assert judged(root, target="main") == []
    assert judged(root, target="feature/3-base") == [
        "target: the pull request targets `feature/3-base`, not the default branch `main`"
    ]


def test_a_branch_behind_the_default_branch_ran_its_gates_on_an_older_one(root: Path) -> None:
    change(root, {"src/a.py": "x = 2\n"})
    assert judged(root, current=True) == []
    git(root, "checkout", "-q", "main")
    write(root, "src/b.py", "y = 1\n")
    commit(root, "feat(core): something else merged")
    git(root, "checkout", "-q", "feature/4-thing")
    assert judged(root) == []
    (reason,) = judged(root, current=True)
    assert reason.startswith("behind: the branch does not hold the tip of `main` (")


def test_a_red_tier_2_on_the_default_branch_stops_the_merge(root: Path) -> None:
    change(root, {"src/a.py": "x = 2\n"})
    assert judged(root, tier2="green") == []
    assert judged(root, tier2="red") == [
        (
            "tier2: tier 2 is red on `main`: that is the next piece of work, and the owner merges"
            " what fixes it"
        )
    ]


# --- failing closed ---------------------------------------------------------------------------


def test_refs_that_name_nothing_are_nothing_to_judge(root: Path) -> None:
    assert [str(r) for r in merge_ready.judge(root, "nowhere", "HEAD")] == [
        "refs: `nowhere` names no commit here, so there is nothing to judge"
    ]
    assert [str(r) for r in merge_ready.judge(root, "main", "nothing")] == [
        "refs: `nothing` names no commit here, so there is nothing to judge"
    ]


def test_histories_that_share_nothing_cannot_be_judged(root: Path) -> None:
    git(root, "checkout", "-q", "--orphan", "island")
    commit(root, "feat(core): an island")
    assert judged(root) == ["refs: `main` and `HEAD` share no history the judge can read"]


def test_a_path_that_is_not_text_cannot_be_judged(root: Path) -> None:
    (root / os.fsdecode(b"\xff.txt")).write_text("x\n", encoding="utf-8")
    change(root, {}, record=False)
    assert judged(root) == ["refs: `main` and `HEAD` share no history the judge can read"]


@pytest.mark.parametrize(
    ("manifest", "reason"),
    [
        (
            None,
            (
                "manifest: the default branch has no guards/manifest.toml, so nothing says what a"
                " guard path or a one-way door is"
            ),
        ),
        (
            "[[group]]\n",
            (
                "manifest: guards/manifest.toml on the default branch does not parse: group 'paths'"
                " must be a list"
            ),
        ),
    ],
)
def test_a_default_branch_without_a_readable_manifest_judges_nothing(
    tmp_path: Path, manifest: str | None, reason: str
) -> None:
    files = {k: v for k, v in BASE.items() if k != "guards/manifest.toml"}
    repository(tmp_path, files | ({"guards/manifest.toml": manifest} if manifest else {}))
    branch(tmp_path, "feature/4-thing")
    change(tmp_path, {"src/a.py": "x = 2\n"})
    assert judged(tmp_path) == [reason]


def test_the_manifest_the_branch_holds_is_never_the_one_that_judges(root: Path) -> None:
    weaker = '[[group]]\nname = "none"\nreason = "Nothing is guarded."\npaths = ["nothing"]\n'
    change(root, {"guards/manifest.toml": weaker, "justfile": "check:\n    false\n"})
    assert codes(root) == ["guard", "guard"]


# --- the command ------------------------------------------------------------------------------


def test_the_judge_prints_its_answer_and_exits_by_it(
    root: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    change(root, {"src/a.py": "x = 2\n"})
    capsys.readouterr()
    report = tmp_path / "comment.md"
    assert merge_ready.main(["merge_ready", "--root", str(root), "--report", str(report)]) == 0
    assert capsys.readouterr().out == "merge: agent\n"
    head = git(root, "rev-parse", "HEAD")[:12]
    assert report.read_text(encoding="utf-8") == (
        f"<!-- ratchet:merge -->\n**merge: agent** at `{head}`: the merge judge found nothing to"
        " wait for.\n"
    )
    write(root, "justfile", "check:\n    false\n")
    commit(root, "chore(gates): loosen")
    arguments = ["--root", str(root), "--base", "main", "--head", "feature/4-thing"]
    assert merge_ready.main(["merge_ready", *arguments, "--report", str(report)]) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[:2] == ["merge: owner", "guard: justfile (gate-commands)"]
    assert out[2].startswith(f"record: {RECORD}: it certifies tree ")
    head = git(root, "rev-parse", "HEAD")[:12]
    assert report.read_text(encoding="utf-8").startswith(
        f"<!-- ratchet:merge -->\n**merge: owner** at `{head}`. The merge judge leaves this pull"
        " request to the owner:\n\n- `guard`: justfile (gate-commands)\n- `record`: "
    )


def test_a_head_that_names_nothing_is_reported_as_given(
    root: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    report = tmp_path / "comment.md"
    arguments = ["--root", str(root), "--head", "nothing", "--report", str(report)]
    assert merge_ready.main(["merge_ready", *arguments]) == 1
    assert capsys.readouterr().out.splitlines()[0] == "merge: owner"
    assert "at `nothing`" in report.read_text(encoding="utf-8")


def test_the_record_command_reads_the_pipelines_verdict_from_stdin(
    root: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    write(root, "src/a.py", "x = 2\n")
    commit(root, "feat(thing): the change")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(VERDICT)))
    assert merge_ready.main(["merge_ready", "record", "--root", str(root)]) == 0
    tree = git(root, "rev-parse", "HEAD^{tree}")
    assert capsys.readouterr().out == (
        f"record: {RECORD} certifies tree {tree[:12]}; commit it alone, last\n"
    )
    written = json.loads((root / RECORD).read_text(encoding="utf-8"))
    assert written == {**VERDICT, "schema": 1, "tree": tree}
    assert merge_ready.write_record(root, json.dumps(VERDICT)) == 0, "writing it again is the same"
    commit(root, "docs(thing): the verdict record")
    assert judged(root) == []


@pytest.mark.parametrize("text", ["not json", "[]", '{"slug": "../x"}', '{"slug": 4}', "{}"])
def test_the_record_command_refuses_what_is_not_a_verdict(
    root: Path, text: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert merge_ready.write_record(root, text) == 1
    assert capsys.readouterr().out == (
        "record: stdin is not the pipeline's verdictRecord: a JSON object with its slug\n"
    )


def test_the_record_command_refuses_an_uncommitted_change(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(root, "src/a.py", "x = 2\n")
    write(root, "src/new.py", "y = 1\n")
    assert merge_ready.write_record(root, json.dumps(VERDICT)) == 1
    assert capsys.readouterr().out == (
        "record: commit the change first, so the record names the tree that was reviewed:"
        " src/a.py, src/new.py\n"
    )
    assert not (root / RECORD).exists()


def test_the_record_command_refuses_a_repository_with_no_commit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    git(tmp_path, "init", "-q", "-b", "main")
    assert merge_ready.write_record(tmp_path, json.dumps(VERDICT)) == 1
    assert capsys.readouterr().out.endswith("HEAD cannot be read\n")


def test_the_judge_needs_no_report_file(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert merge_ready.main(["merge_ready", "--root", str(root)]) == 1
    assert capsys.readouterr().out.startswith("merge: owner\nrecord: no verdict record ")


def test_the_default_base_is_the_default_branch(tmp_path: Path) -> None:
    repository(tmp_path, dict(BASE))
    assert merge_ready.default_ref(tmp_path) == "main"
    empty = tmp_path / "empty"
    empty.mkdir()
    git(empty, "init", "-q", "-b", "trunk")
    assert merge_ready.default_ref(empty) == "origin/main"


# --- a path that is not a regular file ----------------------------------------------------------

TERM = GLOSSARY + "\n**Order**:\nWhat a customer places.\n"
LINKED = "and the judge reads regular files only"


@pytest.mark.parametrize(
    ("path", "target"),
    [
        ("GLOSSARY.md", TERM),
        ("docs/adr/0002-orders.md", ADR),
        ("tests/contract/surface.txt", SURFACE + "pkg.a.h(x)\n"),
    ],
)
def test_a_guard_or_door_path_that_is_a_link_is_the_owners(
    root: Path, path: str, target: str
) -> None:
    # Why: git shows a link's target as the file's text, so a link whose target is the text of
    # an addition read as one, while a gate that opens the path follows the link somewhere else.
    link(root, path, target)
    change(root, {"src/a.py": "x = 2\n"})
    assert git(root, "show", f"HEAD~1:{path}") == target.strip()
    # A linked surface snapshot is also read as unflagged surface: either reason is the owner's.
    assert f"file: {path} is a symbolic link at the head, {LINKED}" in judged(root)


def test_a_verdict_record_that_is_a_link_is_the_owners(root: Path) -> None:
    change(root, {"src/a.py": "x = 2\n"})
    text = (root / RECORD).read_text(encoding="utf-8")
    link(root, RECORD, text)
    commit(root, "docs(thing): the record, as a link")
    assert f"file: {RECORD} is a symbolic link at the head, {LINKED}" in judged(root)


def test_a_link_or_a_submodule_the_default_branch_holds_is_the_owners_to_replace(
    tmp_path: Path,
) -> None:
    repository(tmp_path, dict(BASE))
    link(tmp_path, "tests/contract/extra.txt", "a\n")
    held = commit(tmp_path, "test(contract): a link")
    git(tmp_path, "update-index", "--add", "--cacheinfo", f"160000,{held},migrations/vendor")
    git(tmp_path, "commit", "-q", "-m", "chore(migrations): a submodule")
    branch(tmp_path, "feature/4-thing")
    (tmp_path / "tests/contract/extra.txt").unlink()
    git(tmp_path, "rm", "-q", "--cached", "migrations/vendor")
    change(tmp_path, {"tests/contract/extra.txt": "a\nb\n", "migrations/vendor": "x\n"})
    reasons = judged(tmp_path)
    assert (
        f"file: tests/contract/extra.txt is a symbolic link at the merge base, {LINKED}" in reasons
    )
    assert f"file: migrations/vendor is a submodule at the merge base, {LINKED}" in reasons


def test_a_link_outside_the_guard_and_door_paths_is_no_reason(root: Path) -> None:
    link(root, "src/alias.py", "a.py")
    change(root, {"src/a.py": "x = 2\n"})
    assert judged(root) == []
