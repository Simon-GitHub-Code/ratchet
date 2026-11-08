"""The feature preflight: red with the exact failure before any agent spawns, green otherwise."""

import json
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

import feature_preflight as preflight
from tests.repos import git, repository, write

Out = pytest.CaptureFixture[str]
Gh = Callable[[Sequence[str]], str | None]
CONJECTURE = (
    "\n\n## Conjecture\n\nParse the ticket once in a script; the refuter is a green run on a"
    " ticket without a label. Alternatives: an agent reads it (killed: it spawns first).\n"
)
RECORD = """# Triage Labels

| Label in mattpocock/skills | Label in our tracker | Meaning |
| --- | --- | --- |
| `needs-triage` | `needs-triage` | Maintainer needs to evaluate this issue |
| `ready-for-agent` | `agent:ready` | Fully specified, ready for an AFK agent |
"""


def issue(
    *,
    body: str = "## What to build\n\nA thing." + CONJECTURE,
    labels: tuple[str, ...] = ("agent:ready",),
    state: str = "OPEN",
) -> str:
    return json.dumps(
        {
            "number": 4,
            "title": "Feature input: use the tracker ticket!",
            "body": body,
            "labels": [{"name": name} for name in labels],
            "state": state,
        }
    )


class FakeGh:
    """A ``gh`` that answers every call with one output and remembers the calls."""

    def __init__(self, output: str | None) -> None:
        self.output = output
        self.calls: list[Sequence[str]] = []

    def __call__(self, args: Sequence[str]) -> str | None:
        self.calls.append(args)
        return self.output


def gh_returning(output: str | None) -> FakeGh:
    return FakeGh(output)


def run(
    root: Path,
    ref: str,
    gh: Gh | None = None,
    *,
    base: str = "main",
    branch: str | None = None,
) -> preflight.Report:
    # Why: no test reaches the real tracker; a ticket number with no runner given is not found.
    runner = gh_returning(None) if gh is None else gh
    return preflight.preflight(ref, root, base=base, branch=branch, gh=runner)


def checks(report: preflight.Report) -> list[str]:
    return [failure.check for failure in report.failures]


def test_a_labelled_open_ticket_with_a_conjecture_on_a_clean_tree_is_green(tmp_path: Path) -> None:
    root = repository(tmp_path, {"docs/agents/triage-labels.md": RECORD})
    gh = gh_returning(issue())
    report = run(root, "#4", gh)
    assert report.ok
    assert report.failures == []
    assert gh.calls == [["issue", "view", "4", "--json", "number,title,body,labels,state"]]
    assert (report.number, report.slug) == (4, "feature-input-use-the-tracker-ticket")
    assert report.branch == "feature/4-feature-input-use-the-tracker-ticket"
    assert report.label == "agent:ready"


def test_without_a_record_the_role_name_is_the_label(tmp_path: Path) -> None:
    root = repository(tmp_path)
    assert checks(run(root, "4", gh_returning(issue()))) == ["label"]
    assert run(root, "4", gh_returning(issue(labels=("ready-for-agent",)))).ok


def test_each_missing_piece_names_what_produces_it(tmp_path: Path) -> None:
    root = repository(tmp_path, {"docs/agents/triage-labels.md": RECORD})
    report = run(root, "4", gh_returning(issue(body="## What to build\n\nA thing.\n", labels=())))
    assert checks(report) == ["conjecture", "label"]
    assert "owner adds `## Conjecture`" in report.failures[0].produce
    assert "triage" in report.failures[1].produce
    assert "labels: []" in report.failures[1].detail
    closed = run(root, "4", gh_returning(issue(state="CLOSED")))
    assert checks(closed) == ["open"]


@pytest.mark.parametrize(
    "body",
    [
        "## Conjecture\n\nTBD\n",
        "## Conjecture\n\n<the approach, its refuter, the alternatives>\n",
        "## Conjecture\n\nShort.\n\n## Next\n\nA long enough body under another heading.\n",
        "## Conjecture\n",
        "## Conjecture\n\n## Acceptance criteria\n\nSomething long enough to pass on its own.\n",
    ],
)
def test_a_placeholder_conjecture_is_no_conjecture(tmp_path: Path, body: str) -> None:
    root = repository(tmp_path)
    report = run(root, "4", gh_returning(issue(body=body, labels=("ready-for-agent",))))
    assert checks(report) == ["conjecture"]
    assert "states no conjecture" in report.failures[0].detail


class ParentGh:
    """A ``gh`` that serves the ticket and, when asked, its parent spec."""

    def __init__(self, ticket: str, parent: str | None) -> None:
        self.ticket, self.parent = ticket, parent
        self.calls: list[Sequence[str]] = []

    def __call__(self, args: Sequence[str]) -> str | None:
        self.calls.append(args)
        return self.parent if args[2] == "12" else self.ticket


def spec(body: str) -> str:
    return json.dumps(
        {"number": 12, "title": "The spec", "body": body, "labels": [], "state": "OPEN"}
    )


def test_a_ticket_inherits_the_conjecture_of_its_parent_spec(tmp_path: Path) -> None:
    root = repository(tmp_path)
    child = issue(
        body="**Parent:** #12\n\n## What to build\n\nA slice.\n", labels=("ready-for-agent",)
    )
    gh = ParentGh(child, spec("## Solution\n\nX." + CONJECTURE))
    report = run(root, "4", gh)
    assert report.ok, report.failures
    assert gh.calls[1][2] == "12"
    for parent, detail in [
        (None, "its parent #12 was not found"),
        (spec("## Solution\n\nNo conjecture here.\n"), "its parent #12 states none"),
        (spec("## Conjecture\n\nTBD\n"), "its parent #12 states none"),
    ]:
        failed = run(root, "4", ParentGh(child, parent))
        assert checks(failed) == ["conjecture"]
        assert detail in failed.failures[0].detail
    orphan = run(
        root, "4", gh_returning(issue(body="## What to build\n\nX.\n", labels=("ready-for-agent",)))
    )
    assert "names no parent" in orphan.failures[0].detail


def test_a_local_ticket_inherits_from_the_spec_beside_it(tmp_path: Path) -> None:
    files = {
        ".scratch/f/spec.md": "# F\n\n## Solution\n\nX." + CONJECTURE,
        ".scratch/f/issues/01-a.md": (
            "# 01: A\n\n**Parent:** `../spec.md`\n\n**Status:** `ready-for-agent`\n\nBody.\n"
        ),
        ".scratch/f/issues/02-b.md": "# 02: B\n\nParent: nowhere.md\n\nStatus: ready-for-agent\n",
    }
    root = repository(tmp_path, files)
    assert run(root, ".scratch/f/issues/01-a.md").ok
    assert run(root, "./.scratch/f/issues/01-a.md").ok
    missing = run(root, ".scratch/f/issues/02-b.md")
    assert "its parent .scratch/f/issues/nowhere.md was not found" in missing.failures[0].detail
    assert preflight.local_ticket("4", "Parent: spec.md\n").parent == "spec.md"


def test_a_conjecture_under_a_deeper_heading_still_counts(tmp_path: Path) -> None:
    root = repository(tmp_path)
    body = "## Conjecture\n\n### Approach\n\nA stated approach with its refuter, long enough.\n"
    assert run(root, "4", gh_returning(issue(body=body, labels=("ready-for-agent",)))).ok


@pytest.mark.parametrize("output", [None, "not json", "[]", '{"labels": "x", "state": "open"}'])
def test_an_unreadable_ticket_is_a_missing_ticket(tmp_path: Path, output: str | None) -> None:
    root = repository(tmp_path)
    report = run(root, "4", gh_returning(output))
    if output == '{"labels": "x", "state": "open"}':
        assert checks(report) == ["conjecture", "label"]
        assert report.slug == "ticket-4"
    else:
        assert checks(report) == ["ticket"]


def test_a_ref_that_is_neither_a_number_nor_a_file_asks_nothing_of_gh(tmp_path: Path) -> None:
    root = repository(tmp_path)
    gh = gh_returning(issue())
    assert checks(run(root, "docs/nowhere.md", gh)) == ["ticket"]
    assert gh.calls == []


def test_a_local_ticket_file_is_read_in_place(tmp_path: Path) -> None:
    text = (
        "# 02: Add the preflight\n\n**Status:** `ready-for-agent`\n\n## What to build\n\nX."
        + CONJECTURE
    )
    root = repository(tmp_path, {".scratch/x/issues/02-add-the-preflight.md": text})
    gh = gh_returning(None)
    report = run(root, ".scratch/x/issues/02-add-the-preflight.md", gh)
    assert report.ok, report.failures
    assert gh.calls == []
    assert (report.number, report.title) == (2, "Add the preflight")
    assert report.branch == "feature/2-add-the-preflight"


def test_a_local_ticket_without_heading_number_or_status(tmp_path: Path) -> None:
    root = repository(tmp_path, {"notes/idea.md": "Just prose." + CONJECTURE})
    report = run(root, "notes/idea.md")
    assert checks(report) == ["label"]
    assert (report.number, report.title, report.branch) == (None, "idea", "feature/idea")
    (tmp_path / "b").mkdir()
    done = repository(tmp_path / "b", {"t.md": "# T\n\nStatus: done" + CONJECTURE})
    assert checks(run(done, "t.md")) == ["open", "label"]


def test_the_record_is_read_defensively(tmp_path: Path) -> None:
    for record, expected in [
        ("| Role | Label |\n| --- | --- |\n| ready-for-agent | x |\n", "ready-for-agent"),
        (
            RECORD.replace("| `agent:ready` | Fully specified, ready for an AFK agent |", ""),
            "ready-for-agent",
        ),
        (RECORD.replace("`agent:ready`", ""), "ready-for-agent"),
        ("prose only\n", "ready-for-agent"),
        (RECORD, "agent:ready"),
    ]:
        root = tmp_path / str(abs(hash(record)))
        write(root, "docs/agents/triage-labels.md", record)
        assert preflight.ready_label(root) == expected, record
    assert preflight.ready_label(tmp_path / "nowhere") == "ready-for-agent"


def test_the_checkout_must_be_clean_and_on_the_branch(tmp_path: Path) -> None:
    root = repository(tmp_path, {"docs/agents/triage-labels.md": RECORD})
    gh = gh_returning(issue())
    write(root, "src/x.py", "x = 1\n")
    report = run(root, "4", gh)
    assert checks(report) == ["clean"]
    assert "src/x.py" in report.failures[0].detail
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "x")
    assert checks(run(root, "4", gh, branch="feature/4-x")) == ["branch"]
    git(root, "switch", "-q", "-c", "feature/4-x")
    assert run(root, "4", gh, branch="feature/4-x").ok
    assert checks(run(root, "4", gh, base="feature/4-x", branch="feature/4-x")) == ["branch"]
    assert checks(run(root, "4", gh, base="main", branch="main"))[0] == "branch"


def test_outside_a_checkout_the_tree_cannot_be_clean(tmp_path: Path) -> None:
    report = run(tmp_path, "4", gh_returning(issue(labels=("ready-for-agent",))), branch="x")
    assert checks(report) == ["clean"]
    assert "not a git checkout" in report.failures[0].detail


def test_slugs_are_kebab_case_and_bounded() -> None:
    assert preflight.slug_of("  Hello, World! ", 1) == "hello-world"
    assert preflight.slug_of("x" * 60, 1) == "x" * 40
    assert preflight.slug_of("a" * 39 + "-b", 1) == "a" * 39
    assert (
        preflight.slug_of("Walking skeleton: an empty page that builds and serves", 2)
        == "walking-skeleton-an-empty-page-that"
    ), "a word the width cuts is dropped whole"
    assert preflight.slug_of("", 7) == "ticket-7"
    assert preflight.slug_of("!!!", None) == "feature"


def test_run_gh_returns_stdout_on_success_and_none_otherwise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        assert argv[:2] == ["gh", "issue"]
        return subprocess.CompletedProcess(argv, 1 if "fail" in argv else 0, stdout="{}", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert preflight.run_gh(["issue", "view"]) == "{}"
    assert preflight.run_gh(["issue", "fail"]) is None

    def missing(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(subprocess, "run", missing)
    assert preflight.run_gh(["issue", "view"]) is None


def test_main_prints_json_and_the_verdict(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repository(tmp_path, {"t.md": "# T\n\nStatus: ready-for-agent" + CONJECTURE})
    assert preflight.main(["preflight", "t.md", "--root", str(root)]) == 0
    out, err = capsys.readouterr()
    assert json.loads(out)["ok"] is True
    assert err.strip() == "preflight: green"
    monkeypatch.setattr(preflight, "run_gh", gh_returning(None))
    assert preflight.main(["preflight", "9", "--root", str(root), "--branch", "main"]) == 1
    out, err = capsys.readouterr()
    assert [f["check"] for f in json.loads(out)["failures"]] == ["ticket", "branch"]
    assert "preflight: ticket: 9: no such ticket" in err
    assert "produce: mattpocock-skills:to-spec" in err
    assert err.rstrip().endswith("preflight: red")


def test_a_parent_section_names_the_spec_as_to_tickets_writes_it(tmp_path: Path) -> None:
    # Why: mattpocock-skills' ticket template has a `## Parent` section, not a `Parent:` line.
    root = repository(tmp_path)
    for body in ("## Parent\n\n#12\n", "## Parent\n\nhttps://github.com/o/r/issues/12\n"):
        child = issue(body=body + "\n## What to build\n\nA slice.\n", labels=("ready-for-agent",))
        report = run(root, "4", ParentGh(child, spec("## Solution\n\nX." + CONJECTURE)))
        assert report.ok, report.failures


def test_a_local_ticket_inherits_from_its_feature_spec_without_naming_it(tmp_path: Path) -> None:
    # Why: the local tracker files tickets at `<feature>/issues/NN-<slug>.md` beside
    # `<feature>/spec.md`, and its template omits the Parent section.
    files = {
        ".scratch/f/spec.md": "# F\n\n## Solution\n\nX." + CONJECTURE,
        ".scratch/f/issues/01-a.md": "# 01: A\n\n**Status:** ready-for-agent\n\nBody.\n",
        ".scratch/g/issues/01-b.md": "# 01: B\n\nStatus: ready-for-agent\n",
    }
    root = repository(tmp_path, files)
    assert run(root, ".scratch/f/issues/01-a.md").ok
    assert "names no parent" in run(root, ".scratch/g/issues/01-b.md").failures[0].detail


def test_an_open_blocker_holds_the_ticket_back(tmp_path: Path) -> None:
    ready = "Status: ready-for-agent" + CONJECTURE
    files = {
        ".scratch/f/issues/01-a.md": "# 01: A\n\nStatus: ready-for-human\n",
        ".scratch/f/issues/02-b.md": "# 02: B\n\nStatus: resolved\n",
        ".scratch/f/issues/03-c.md": "# 03: C\n\n**Blocked by:** 01 (A), 02 (B)\n\n" + ready,
        ".scratch/f/issues/04-d.md": "# 04: D\n\nBlocked by: 02-b.md\n\n" + ready,
        ".scratch/f/issues/05-e.md": "# 05: E\n\nBlocked by: None (can start now)\n\n" + ready,
        ".scratch/f/issues/06-f.md": "# 06: F\n\nBlocked by: 09\n\n" + ready,
    }
    root = repository(tmp_path, files)
    held = run(root, ".scratch/f/issues/03-c.md")
    assert checks(held) == ["blocked"]
    assert "01-a.md is still open" in held.failures[0].detail
    assert run(root, ".scratch/f/issues/04-d.md").ok
    assert run(root, ".scratch/f/issues/05-e.md").ok
    assert "blocker 09 was not found" in run(root, ".scratch/f/issues/06-f.md").failures[0].detail


def test_a_forge_ticket_is_held_by_an_open_blocking_issue(tmp_path: Path) -> None:
    root = repository(tmp_path)
    child = issue(
        body="## Blocked by\n\n- #12 Log 2 brews\n" + CONJECTURE, labels=("ready-for-agent",)
    )
    for state, expected in (("OPEN", ["blocked"]), ("CLOSED", [])):
        blocker = json.dumps({"number": 12, "title": "B", "body": "", "labels": [], "state": state})
        assert checks(run(root, "4", ParentGh(child, blocker))) == expected
