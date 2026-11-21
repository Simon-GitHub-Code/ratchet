import inspect
import json
from collections.abc import Sequence
from pathlib import Path

import pytest

import documents
import feature_preflight as preflight
import split
from ratchet_guards import splits
from tests.repos import branch, repository, snapshot
from tests.test_merge_ready import BASE, SPEC, TICKET, change, judged

PARENT_REF = ".scratch/f/issues/01-a.md"
PARENT = "# 01: A\n\nStatus: ready-for-agent\n\n- [ ] It saves.\n- [ ] It lists.\n"
HALVES = {
    "parts": [
        {"title": "Save", "promises": ["It saves."]},
        {"title": "List", "promises": ["It lists."], "after": [0]},
    ]
}
Out = pytest.CaptureFixture[str]


def issue(body: str = PARENT, labels: tuple[str, ...] = ("ready-for-agent",)) -> str:
    return json.dumps(
        {
            "number": 7,
            "title": "A",
            "body": body,
            "labels": [{"name": name} for name in labels],
            "state": "OPEN",
        }
    )


def tracker(output: str | None) -> preflight.Runner:
    return lambda _args: output


def test_a_plan_that_is_not_the_shape_has_no_parts() -> None:
    assert split.parts_of([]) == []
    assert split.parts_of({"parts": "x"}) == []
    found = split.parts_of(
        {"parts": ["x", {"title": " T ", "promises": "no", "after": ["a", 1]}, {"title": "U"}]}
    )
    assert [(p.title, p.promises, p.after) for p in found] == [
        ("T", (), (1,)),
        ("U", (), (1,)),
    ]


def test_a_split_is_refused_for_each_reason_it_names(tmp_path: Path) -> None:
    root = repository(tmp_path, {PARENT_REF: PARENT})
    parent = preflight.local_ticket(PARENT_REF, PARENT)
    parts = split.parts_of(HALVES)
    assert split.problems(None, parts, "L", holds=True) == ["the ticket is not there"]
    assert any("does not carry" in p for p in split.problems(parent, parts, "L", holds=False))
    child = preflight.local_ticket("02-b.md", PARENT + "\nSplit from: 01-a.md\n")
    assert any("not split again" in p for p in split.problems(child, parts, "L", holds=True))
    assert any(
        "two parts at least" in p for p in split.problems(parent, parts[:1], "L", holds=True)
    )
    untitled = split.parts_of({"parts": [{"promises": ["It saves."]}, {"title": "x"}]})
    assert "part 1 has no title" in split.problems(parent, untitled, "L", holds=True)
    assert split.parent_now(root, "missing.md", tracker(None)) is None


def test_the_parent_of_an_issue_is_what_the_tracker_says(tmp_path: Path) -> None:
    root = repository(tmp_path)
    found = split.parent_now(root, "7", tracker(issue()))
    assert found is not None
    assert found.title == "A"


def test_the_parent_of_a_local_ticket_is_what_the_default_branch_holds_not_the_work_tree(
    tmp_path: Path,
) -> None:
    root = repository(tmp_path, {PARENT_REF: PARENT})
    edited = PARENT.replace("It saves.", "It is edited.")
    (root / PARENT_REF).write_text(edited, encoding="utf-8")
    found = split.parent_now(root, PARENT_REF, tracker(None))
    assert found is not None
    assert "It saves." in found.body
    assert "It is edited." not in found.body


def test_a_checkbox_in_a_section_a_part_inherits_is_not_a_promise_the_part_makes() -> None:
    text = f"{PARENT}\n## Conjecture\n\n- [ ] It is fast.\n"
    parent = preflight.local_ticket(PARENT_REF, text)
    plan = {
        "parts": [
            {"title": "Save", "promises": ["It saves.", "It is fast."]},
            {"title": "List", "promises": ["It lists."], "after": [0]},
        ]
    }
    assert split.problems(parent, split.parts_of(plan), "L", holds=True) == []


def test_the_parts_of_an_issue_are_filed_on_the_tracker_each_waiting_on_the_one_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repository(tmp_path)
    calls: list[Sequence[str]] = []

    def made(args: Sequence[str]) -> str:
        calls.append(args)
        return f"https://example.test/o/r/issues/{10 + len(calls)}\n"

    monkeypatch.setattr(preflight, "run_gh", made)
    children, refused = split.apply(root, "7", HALVES, gh=tracker(issue()))
    assert (children, refused) == (["#11", "#12"], [])
    assert all(call[:2] == ["issue", "create"] and "ready-for-agent" in call for call in calls)
    body = (root / ".ratchet" / "split-body.md").read_text(encoding="utf-8")
    assert "Blocked by: #11" in body
    assert "Split from: 7" in body


def test_a_tracker_that_files_nothing_stops_the_split_and_says_how_far_it_got(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repository(tmp_path)
    monkeypatch.setattr(preflight, "run_gh", lambda _args: None)
    with pytest.raises(RuntimeError, match="did not file part 1"):
        split.apply(root, "7", HALVES, gh=tracker(issue()))


def test_main_checks_then_applies_a_plan_and_refuses_one_it_cannot_read(
    tmp_path: Path, capsys: Out
) -> None:
    base = tmp_path / "repo"
    base.mkdir()
    root = repository(base, {PARENT_REF: PARENT})
    plan = tmp_path / "plan.json"
    argv = ["split", "check", PARENT_REF, "--plan", str(plan), "--root", str(root), "--offline"]
    assert split.main(argv) == 2, "no plan file"
    assert "does not read" in capsys.readouterr().out
    plan.write_text(json.dumps({"parts": HALVES["parts"][:1]}), encoding="utf-8")
    assert split.main(argv) == 1
    assert "refused" in capsys.readouterr().out
    plan.write_text(json.dumps(HALVES), encoding="utf-8")
    assert split.main(argv) == 0
    assert not (root / ".scratch/f/issues/02-save.md").exists(), "check files nothing"
    assert split.main([*argv[:1], "apply", *argv[2:]]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["children"] == [
        ".scratch/f/issues/02-save.md",
        ".scratch/f/issues/03-list.md",
    ]


ANSWERS = (
    "- conjecture.flag: A saved link list: should it stay hidden until you switch it on, or"
    " appear as soon as it is finished? Stay hidden until I switch it on.\n"
    "- A or B? A, *as written*.\n"
)
SECTIONS = f"## Conjecture\n\nIt helps.\n\n## Operability\n\nFlag: none\n\n## Answers\n\n{ANSWERS}"


def test_a_part_carries_the_answers_word_for_word_after_the_other_inherited_sections() -> None:
    part = split.parts_of(HALVES)[0]
    first = f"## Answers\n\n{ANSWERS}\n## Operability\n\nFlag: none\n\n## Conjecture\n\nA.\n"
    parent = preflight.local_ticket(PARENT_REF, f"{PARENT}\n{first}")
    text, _ = split.child_body(parent, part, waits=[], label="L")
    assert text.endswith(f"## Answers\n\n{ANSWERS}")
    assert text.index("## Conjecture") < text.index("## Operability") < text.index("## Answers")
    assert text.count("## Answers") == 1
    for empty in ("## Answers\n\n", "### Answers\n\n- A or B? A.\n"):
        bare = preflight.local_ticket(PARENT_REF, f"{PARENT}\n{empty}")
        assert "Answers" not in split.child_body(bare, part, waits=[], label="L")[0]


@pytest.mark.parametrize(
    ("lines", "written"),
    [
        (["Parent: ../plan.md"], "Parent: ../plan.md"),
        (["**Parent:** `../plan.md`  "], "**Parent:** `../plan.md`  "),
        (["Parent: notes.md", "Parent: ../other.md"], "Parent: notes.md"),
        (["```", "Parent: ../plan.md", "```"], "Parent: ../plan.md"),
    ],
    ids=["relative", "markup and trailing spaces", "the first of two", "fenced"],
)
def test_a_part_carries_the_parent_line_as_its_ticket_wrote_it_under_its_split_from_line(
    tmp_path: Path, lines: list[str], written: str
) -> None:
    ticket = PARENT.replace("\n\n- [ ]", "\n\n" + "\n".join(lines) + "\n\n- [ ]", 1)
    root = repository(
        tmp_path,
        {
            PARENT_REF: ticket + SECTIONS,
            ".scratch/f/plan.md": "# Plan\n",
            ".scratch/f/issues/notes.md": "# Notes\n",
        },
    )
    children, refused = split.apply(root, PARENT_REF, HALVES, gh=tracker(None))
    assert refused == []
    for child in children:
        text = (root / child).read_text(encoding="utf-8")
        assert f"\nSplit from: {PARENT_REF}\n{written}\nBlocked by: " in text
        assert text.count(written) == 1
    reach = {
        ref: preflight.preflight(ref, root, base="main", branch=None, gh=tracker(None)).parent
        for ref in [PARENT_REF, *children]
    }
    assert set(reach.values()) == {preflight.local_ticket(PARENT_REF, ticket).parent}
    assert None not in reach.values()


@pytest.mark.parametrize("beside", [False, True], ids=["no spec beside", "a spec beside"])
@pytest.mark.parametrize(
    "lines",
    ["Parent:\n../../other/spec.md", "**Parent:**\n`../../other/spec.md`  <!-- the plan -->"],
    ids=["plain", "markup"],
)
def test_a_part_resolves_and_passes_as_its_ticket_does_when_the_ref_is_on_the_next_line(
    tmp_path: Path, lines: str, *, beside: bool
) -> None:
    spec = "# Spec\n\n## Conjecture\n\nSaving a list lets people come back to it, as they asked.\n"
    ticket = PARENT.replace("\n\n- [ ]", f"\n\n{lines}\n\n- [ ]", 1)
    nearby = {".scratch/f/spec.md": spec.replace("Spec", "Beside")} if beside else {}
    root = repository(tmp_path, {PARENT_REF: ticket, ".scratch/other/spec.md": spec, **nearby})
    children, refused = split.apply(root, PARENT_REF, HALVES, gh=tracker(None))
    assert refused == []
    for child in children:
        assert f"\nSplit from: {PARENT_REF}\n{lines}\nBlocked by: " in (root / child).read_text(
            encoding="utf-8"
        )
    snapshot(root, "docs(f): the parts")
    reports = [
        preflight.preflight(ref, root, base="main", branch=None, gh=tracker(None))
        for ref in [PARENT_REF, *children]
    ]
    assert [report.parent for report in reports] == [".scratch/other/spec.md"] * 3
    assert [[f.check for f in r.failures if f.check != "blocked"] for r in reports] == [[]] * 3


def test_a_part_carries_every_answers_section_its_ticket_has_so_both_read_the_same_answers() -> (
    None
):
    text = f"{PARENT}\n## Answers\n\n- a? b.\n\n## Notes\n\nx\n\n## Answers\n\n- c? d.\n"
    body, _ = split.child_body(
        preflight.local_ticket(PARENT_REF, text), split.parts_of(HALVES)[0], waits=[], label="L"
    )
    assert documents.answers_of(body) == documents.answers_of(text) == ["- a? b.", "- c? d."]


def test_a_part_of_a_ticket_with_no_parent_line_and_no_answers_is_written_as_before() -> None:
    parent = preflight.local_ticket(PARENT_REF, f"{PARENT}\n## Conjecture\n\nIt helps.\n")
    first, second = split.parts_of(HALVES)
    assert split.child_body(parent, first, waits=["#3"], label="ready-for-agent") == (
        (
            "# Save\n\n**Status:** `ready-for-agent`\n\nSplit from: .scratch/f/issues/01-a.md\n"
            "Blocked by: #3\n\n## What to build\n\nSave\n\n## Acceptance criteria\n\n"
            "- [ ] It saves.\n\n## Conjecture\n\nIt helps.\n"
        ),
        None,
    )
    tracked = preflight.Ticket("7", 7, "A", PARENT, ("ready-for-agent",), is_open=True)
    assert split.child_body(tracked, second, waits=[], label="ready-for-agent") == (
        (
            "# List\n\nSplit from: 7\nBlocked by: none\n\n## What to build\n\nList\n\n"
            "## Acceptance criteria\n\n- [ ] It lists.\n\n"
        ),
        None,
    )


def test_a_part_does_not_carry_the_size_its_parent_was_too_large_for(tmp_path: Path) -> None:
    (tmp_path / "quality").mkdir()
    (tmp_path / "quality" / "thresholds.toml").write_text(
        "[ceilings.change]\nproduct_lines = 300\nproduct_files = 10\n"
    )
    sections = "## Conjecture\n\nIt helps.\n\n## Size\n\n- Product lines: ~500\n"
    parent = preflight.local_ticket(PARENT_REF, f"{PARENT}\n{sections}")
    assert preflight.size_failure(parent, tmp_path) is not None
    for index, part in enumerate(split.parts_of(HALVES)):
        text, _ = split.child_body(parent, part, waits=[], label="ready-for-agent")
        child = preflight.local_ticket(f".scratch/f/issues/0{index + 2}-p.md", text)
        assert preflight.size_failure(child, tmp_path) is None
        assert "## Size" not in text
        assert "## Conjecture\n\nIt helps." in text


OTHER = "../../other/spec.md"
LONG = "Saving a list lets people come back to it later, which they asked for."
DATED = (
    "\n## Answers\n\n- 2026-11-01: Should it arrive switched off? Yes, *off*.\n"
    "- 2026-11-02: A or B? `A`.\n"
)
PARENT_LINES = (
    "Parent: ../spec.md",
    f"Parent: {OTHER}",
    f"**Parent:** `{OTHER}`   ",
    "Parent: /abs/spec.md",
    "Parent: `my spec.md`",
    "Parent: my spec.md",
    f"Parent:\n{OTHER}",
    f"**Parent:**\n`{OTHER}`",
    f"Parent:\n{OTHER} and then more words",
    f"Parent:\n\n   \n{OTHER}",
    f"**Parent:** `\n{OTHER}`",
    f"**Parent:\n** {OTHER}",
    f"```\nParent: {OTHER}\n```",
    f"Parent: notes.md\nParent: {OTHER}",
    f"Parent: {OTHER} Parent: notes.md",
    f"Parent: {OTHER} ## Answers",
    f"Parent: {OTHER} ```",
    f"Parent:\n{OTHER} ```",
    "Parent:\n12. Step one",
    "Parent: ../../missing.md",
    "Parent: ../../../../outside.md",
    "Parent: 12",
    f"parent: {OTHER}",
    f"## Notes\n\nParent: {OTHER}",
)
LATER = (
    f"{PARENT}\n## Conjecture\n\n{LONG}\n{DATED}\nParent:\n{OTHER}",
    f"{PARENT}\n## Conjecture\n\n{LONG}\n{DATED}\nParent: {OTHER}",
    f"{PARENT}\n## Conjecture\n\nParent: {OTHER}\n\n{LONG}\n{DATED}",
    f"{PARENT}\nParent: {OTHER}\n{DATED}\n## Conjecture\n\n{LONG}\n",
    (
        f"{PARENT}\nParent: {OTHER}\n{DATED}\n## Notes\n\nnot inherited\n\n## Answers\n\n"
        f"- 2026-11-05: Later? Yes.\n\n## Conjecture\n\n{LONG}\n"
    ),
    (
        f"{PARENT}\nParent: {OTHER}\n{DATED}\n## Answers\n\n## Notes\n\nnot inherited\n\n"
        "## Answers\n\n- 2026-11-05: Later? Yes.\n"
    ),
    f"{PARENT}\nParent: {OTHER}\n\n## Conjecture\n\n{LONG}\n{DATED}\n# Appendix\n\nprose after\n",
    f"{PARENT}\nParent: {OTHER}\n\n## Conjecture\n\nshort\n\n## Conjecture\n\n{LONG}\n",
)
REVIEWED = (
    *(
        PARENT.replace("\n\n- [ ]", f"\n\n{lines}\n\n- [ ]", 1)
        + (f"\n## Conjecture\n\n{LONG}\n" if own else "")
        + DATED
        for lines in PARENT_LINES
        for own in (False, True)
    ),
    *LATER,
)
PLACES = {
    "directly inside": "\n## Answers\n\n- a? b.\n{box}\n",
    "after a level-one heading": "\n## Answers\n\n- a? b.\n\n# Appendix\n\n{box}\n",
    "inside a fence": "\n## Answers\n\n- a? b.\n\n```\n{box}\n```\n",
    "as its only line": "\n## Answers\n\n{box}\n",
}
CHILD = PARENT.replace("\n\n- [ ]", "\n\nParent: ../spec.md\n\n- [ ]", 1)
SAID = (
    "# 01: A\n\nStatus: ready-for-agent\n\nParent: ../spec.md\n\n- [ ] It saves a link.\n\n"
    "## Conjecture\n\nIt helps people come back later.\n\n## Answers\n\n"
    "- 2026-11-01: Should it arrive switched off? Yes.\n\n# Appendix\n\n- [ ] It lists links.\n"
)
FALLS = {
    **{
        f"`{box}` {where}": CHILD + body.format(box=box)
        for box in ("- [ ] Extra.", "- [x] Extra.")
        for where, body in PLACES.items()
    },
    "the review's ticket": SAID,
    "a promise between a Parent: line and a ref": f"{PARENT}\n## Answers\n\n"
    f"- a? b.\nParent:\n- [ ] Extra.\n{OTHER}\n",
    "a formal model line": f"{PARENT}\n## Answers\n\n- a? b.\nFormal model: round\n",
    "a status line": f"{PARENT}\n## Answers\n\n- a? b.\nStatus: done\n",
}
CORPUS = {**{f"reviewed {n}": text for n, text in enumerate(REVIEWED)}, **FALLS}
PLAINLY = SAID.replace("\n\n# Appendix\n\n- [ ] It lists links.\n", "\n").replace(
    "- [ ] It saves a link.\n", "- [ ] It saves a link.\n- [ ] It lists links.\n"
)


@pytest.mark.parametrize("name", list(CORPUS))
def test_a_part_reads_its_tickets_answers_and_parent_or_is_written_as_before_and_says_why(
    name: str,
) -> None:
    text = CORPUS[name]
    wanted = list(dict.fromkeys(splits.promises(text)))
    parts = split.parts_of(
        {"parts": [{"title": "S", "promises": wanted[:1]}, {"title": "L", "promises": wanted[1:]}]}
    )
    for ref in (PARENT_REF, "7"):
        ticket = (
            preflight.local_ticket(ref, text)
            if ref.endswith(".md")
            else preflight.Ticket(ref, 7, "A", text, ("L",), is_open=True)
        )
        assert split.problems(ticket, parts, "L", holds=True) == []
        for index, part in enumerate(parts):
            body, why = split.body_of(ticket, parts, ["02-s.md"], index=index, label="L")
            assert splits.promises(body) == list(part.promises), "each promise once, where planned"
            assert (why is not None) == (name in FALLS), why
            if why:
                assert documents.answers_of(body) == []
                assert f"Split from: {ref}\nBlocked by: " in body
                continue
            read = preflight.local_ticket(ref, body)
            assert documents.answers_of(body) == documents.answers_of(text)
            assert read.parent == ticket.parent
            assert (read.conjecture or "").strip() == (ticket.conjecture or "").strip()
            assert preflight.operability_lines(read, None) == preflight.operability_lines(
                ticket, None
            )


@pytest.mark.parametrize(
    ("text", "said"),
    [
        (SAID, True),
        (SAID.replace("\n\n# Appendix\n\n- [ ]", "\n- [x]"), True),
        (PLAINLY, False),
    ],
    ids=["a promise after a level-one heading", "a checked line in the answers", "plain answers"],
)
def test_the_judge_accepts_the_parts_a_passing_plan_makes_and_the_split_says_what_it_left_out(
    tmp_path: Path, capsys: Out, text: str, *, said: bool
) -> None:
    repository(tmp_path, {**BASE, SPEC: "# Thing\n\nStatus: ready-for-agent\n", TICKET: text})
    branch(tmp_path, "records-thing")
    plan = {
        "parts": [
            {"title": "Save", "promises": ["It saves a link."]},
            {"title": "List", "promises": ["It lists links."], "after": [0]},
        ]
    }
    children, refused = split.apply(tmp_path, TICKET, plan, gh=tracker(None))
    assert refused == []
    told = capsys.readouterr().out.splitlines()
    bodies = {child: (tmp_path / child).read_text(encoding="utf-8") for child in children}
    if said:
        assert [line.partition(":")[2].partition(" does")[0] for line in told] == [
            " part 1 (Save)",
            " part 2 (List)",
        ]
        assert all(line.endswith("the part's promises the judge counts") for line in told)
        assert all(documents.answers_of(body) == [] for body in bodies.values())
    else:
        assert told == []
        assert all(documents.answers_of(b) == documents.answers_of(text) for b in bodies.values())
    change(tmp_path, bodies, record=False)
    assert judged(tmp_path) == []


@pytest.mark.parametrize(
    ("summary", "why"),
    [
        (f"Parent: {OTHER}", "would name the parent .scratch/other/spec.md, not its ticket's None"),
        ("## Answers\n\n- Forged? Yes.", "would read other answers than its ticket"),
    ],
    ids=["a parent line", "an answers heading"],
)
def test_a_part_whose_plan_text_would_misread_the_copy_is_written_as_before(
    summary: str, why: str
) -> None:
    ticket = preflight.local_ticket(PARENT_REF, f"{PARENT}\n## Answers\n\n- a? b.\n")
    plan = {"parts": [{"title": "Save", "promises": ["It saves."], "summary": summary}]}
    body, reason = split.child_body(ticket, split.parts_of(plan)[0], waits=[], label="L")
    assert reason is not None
    assert reason.endswith(why)
    assert "- a? b." not in body


READ_BY = frozenset({"text", "body", "ticket", "child", "parent"})
BUILT_ON_WHAT_IT_CALLS = {
    "feature_preflight": (
        "bare",
        "unfilled",
        "says_something",
        "states_a_conjecture",
        "branch_of",
        "conjecture_failure",
        "named_gates",
        "operability_failure",
        "unlinked_part",
        "flag_of",
        "size_failure",
        "parent_of",
        "reads",
        "blocker_ref",
        "blockers",
        "open_blockers",
        "split_parts",
        "check_ticket",
    ),
    "ratchet_guards.splits": ("carries", "scope_problems", "refusal"),
    "documents": ("named",),
}
OF_OTHER_DOCUMENTS = (
    "shipped",
    "with_lines",
    "sections_of",
    "settled",
    "citations",
    "filled",
    "cuttable",
)
NOT_CALLED = {
    *(f"{module}.{name}" for module, names in BUILT_ON_WHAT_IT_CALLS.items() for name in names),
    *(f"documents.{name}" for name in OF_OTHER_DOCUMENTS),
}


def test_the_check_calls_every_reader_of_a_ticket_body_but_those_built_on_what_it_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticket = preflight.local_ticket(PARENT_REF, f"{PARENT}\n## Answers\n\n- a? b.\n")
    called: set[str] = set()

    def spy(key: str, real: object) -> object:
        def reading(*args: object, **kwargs: object) -> object:
            called.add(key)
            return real(*args, **kwargs)  # type: ignore[operator]

        return reading

    readers = {
        f"{module.__name__}.{name}": (module, name, found)
        for module in (preflight, splits, documents)
        for name, found in vars(module).items()
        if inspect.isfunction(found)
        and found.__module__ == module.__name__
        and READ_BY & set(inspect.signature(found).parameters)
    }
    for key, (module, name, found) in readers.items():
        monkeypatch.setattr(module, name, spy(key, found))
    for name, found in list(vars(preflight.Ticket).items()):
        if isinstance(found, property):
            readers[f"Ticket.{name}"] = (preflight.Ticket, name, found)
            monkeypatch.setattr(preflight.Ticket, name, property(spy(f"Ticket.{name}", found.fget)))
    split.child_body(ticket, split.parts_of(HALVES)[0], waits=[], label="L")
    assert set(readers) - called == NOT_CALLED
