"""The quote check: a finding is found when the record it names holds its quote word for word."""

import json
from pathlib import Path

import pytest

import quotes

Out = pytest.CaptureFixture[str]
COPY = ": copy the whole sentence, list item, cell or line as the record writes it"

QUOTE = "A blocker or a major quotes what it breaks."


def finding(record: str, quote: str, severity: str = "major", *, title: str = "a finding") -> dict:
    return {"severity": severity, "title": title, "breaks": {"record": record, "quote": quote}}


def put(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")


def reasons(verdict: quotes.Verdict) -> list[str]:
    return [entry["reason"] for entry in verdict["not_found"]]


def test_a_quote_the_record_holds_is_found(tmp_path: Path) -> None:
    put(tmp_path, "AGENTS.md", f"# Rules\n\n{QUOTE} Nothing else.\n")
    verdict = quotes.check([finding("AGENTS.md", QUOTE)], tmp_path)
    assert verdict["not_found"] == []
    assert verdict["found"] == [
        {"index": 0, "title": "a finding", "record": "AGENTS.md", "quote": QUOTE}
    ]


def test_a_quote_the_record_does_not_hold_is_not_found(tmp_path: Path) -> None:
    put(tmp_path, "AGENTS.md", "Every change is reviewed.\n")
    verdict = quotes.check([finding("AGENTS.md", "No change is ever reviewed.")], tmp_path)
    assert verdict["found"] == []
    assert reasons(verdict) == ["the record `AGENTS.md` does not hold the quote" + COPY]


def test_a_quote_that_spans_lines_is_found_however_the_record_wraps_it(tmp_path: Path) -> None:
    put(tmp_path, "AGENTS.md", "A blocker or a\n  major quotes\twhat it\r\nbreaks.\n")
    assert quotes.check([finding("AGENTS.md", QUOTE)], tmp_path)["not_found"] == []
    put(tmp_path, "GLOSSARY.md", "A blocker or a major quotes what it breaks.\n")
    wrapped = "A blocker or a\nmajor   quotes what\nit breaks."
    assert quotes.check([finding("GLOSSARY.md", wrapped)], tmp_path)["not_found"] == []


def test_only_white_space_is_read_loosely(tmp_path: Path) -> None:
    put(tmp_path, "AGENTS.md", "A blocker or a `major` quotes what it breaks.\n")
    for changed in (
        "a blocker or a major quotes what it breaks.",
        "A blocker or a major quotes what it breaks.",
        "A blocker or a major quotes what it break",
        "A blocker, or a major quotes what it breaks.",
        "A blocker or a major quotes what it breaks. And more.",
    ):
        assert quotes.check([finding("AGENTS.md", changed)], tmp_path)["found"] == []


AGENTS = """# Rules and the handler

The handler must stop. A page that fails is a blocker. The page, e.g. a file, is a unit.
Fatal errors end the run; the run never resumes! Is that clear?

- Every page passes the automated accessibility checks
  at the agreed level.
- [ ] The tool lists every file.
> A quoted line stays whole.

| Name | Meaning |
| :---: | :--- |
| --- | --- |
| the cell | a \\| b |

---

***

A sentence
wrapped over two lines.
"""


def found(root: Path, quote: str, record: str = "AGENTS.md") -> bool:
    return bool(quotes.check([finding(record, quote)], root)["found"])


def test_only_a_whole_line_of_a_file_that_is_not_markdown_is_found(tmp_path: Path) -> None:
    put(tmp_path, "guards/manifest.toml", "First clause. Second clause.\n")
    assert found(tmp_path, "First clause. Second clause.", "guards/manifest.toml")
    assert not found(tmp_path, "Second clause.", "guards/manifest.toml")


@pytest.mark.parametrize(
    "quote",
    [
        "the",
        "andler",
        "a",
        "#",
        "---",
        ":---",
        "***",
        "| --- |",
        ":---:",
        "Fatal errors end the run; the",
        "e.g. a file, is a unit.",
        "The handler must stop. A page that fails",
        "A page that fails is a",
        "page that fails is a blocker.",
        "that fails is a blocker.",
        "at the agreed level.",
        "Every page passes the automated accessibility checks",
        "Rules and the",
        "the run never resumes! Is that",
        "The tool",
        "wrapped over two lines",
        "A sentence wrapped",
    ],
)
def test_a_quote_that_is_not_a_whole_unit_is_not_found(tmp_path: Path, quote: str) -> None:
    put(tmp_path, "AGENTS.md", AGENTS)
    assert not found(tmp_path, quote)


@pytest.mark.parametrize(
    "quote",
    [
        "The handler must stop.",
        "The handler must stop. A page that fails is a blocker.",
        "A page that fails is a blocker.",
        "The page, e.g. a file, is a unit.",
        "A page that fails is a blocker. The page, e.g. a file, is a unit.",
        "Fatal errors end the run; the run never resumes!",
        "Is that clear?",
        "Rules and the handler",
        "Every page passes the automated accessibility checks at the agreed level.",
        "Every page passes the automated accessibility checks\nat the agreed level.",
        "The tool lists every file.",
        "A quoted line stays whole.",
        "the cell",
        "a \\| b",
        "Name",
        "A sentence wrapped over two lines.",
        "A sentence\nwrapped over two lines.",
        (
            "A page that fails is a blocker. The page, e.g. a file, is a unit.\nFatal errors end"
            " the run; the run never resumes! Is that clear?"
        ),
    ],
)
def test_a_whole_unit_of_the_record_is_found(tmp_path: Path, quote: str) -> None:
    put(tmp_path, "AGENTS.md", AGENTS)
    assert found(tmp_path, quote)


def test_a_sentence_after_an_abbreviation_inside_one_sentence_is_found(tmp_path: Path) -> None:
    put(tmp_path, "AGENTS.md", "Use tools, e.g. a linter. It runs.\n")
    assert found(tmp_path, "a linter.")
    assert found(tmp_path, "Use tools, e.g. a linter.")
    assert found(tmp_path, "Use tools, e.g. a linter. It runs.")


def test_a_closing_quote_or_bracket_after_the_full_stop_still_ends_the_sentence(
    tmp_path: Path,
) -> None:
    put(tmp_path, "AGENTS.md", 'He said "stop." Then (it ended.) Then ended.\n')
    assert found(tmp_path, 'He said "stop."')
    assert found(tmp_path, "Then (it ended.)")
    assert found(tmp_path, "Then ended.")


def test_the_manifest_is_read_by_line_and_a_one_word_path_line_is_found(tmp_path: Path) -> None:
    put(tmp_path, "guards/manifest.toml", 'paths = [\n  "scripts/gate.py",\n]\nname = "gate one"\n')
    assert found(tmp_path, '"scripts/gate.py",', "guards/manifest.toml")
    assert found(tmp_path, 'name = "gate one"', "guards/manifest.toml")
    assert not found(tmp_path, '"gate', "guards/manifest.toml")
    assert not found(tmp_path, 'paths = [ "scripts/gate.py",', "guards/manifest.toml")


def test_a_list_item_that_is_empty_or_a_bare_heading_marker_holds_nothing(tmp_path: Path) -> None:
    put(tmp_path, "AGENTS.md", "#\n\n- \n-\nText.\n")
    assert not found(tmp_path, "#")
    assert not found(tmp_path, "-")
    assert found(tmp_path, "Text.")


def test_a_quote_with_no_word_is_never_found(tmp_path: Path) -> None:
    put(tmp_path, "AGENTS.md", "Some text\n")
    assert reasons(quotes.check([finding("AGENTS.md", " \n\t ")], tmp_path)) == [
        "the record `AGENTS.md` does not hold the quote" + COPY
    ]


def test_a_record_that_is_absent_holds_no_quote(tmp_path: Path) -> None:
    verdict = quotes.check([finding("GLOSSARY.md", QUOTE)], tmp_path)
    assert reasons(verdict) == ["the record `GLOSSARY.md` is absent"]


def test_a_record_that_is_a_directory_holds_what_any_file_in_it_holds(tmp_path: Path) -> None:
    put(tmp_path, "docs/adr/0001-first.md", "Nothing here.\n")
    put(tmp_path, "docs/adr/0072-later.md", f"The ruling.\n{QUOTE}\n")
    put(tmp_path, "docs/adr/nested/0003-deep.md", "A deeper rule says so.\n")
    for name in ("docs/adr/", "docs/adr"):
        assert quotes.check([finding(name, QUOTE)], tmp_path)["not_found"] == []
        assert quotes.check([finding(name, "A deeper rule says so.")], tmp_path)["not_found"] == []
    assert reasons(quotes.check([finding("docs/adr/", "an invented rule")], tmp_path)) == [
        "the record `docs/adr/` does not hold the quote" + COPY
    ]


def test_a_path_to_one_decision_record_names_that_file_alone(tmp_path: Path) -> None:
    put(tmp_path, "docs/adr/0001-first.md", "The first rule.\n")
    put(tmp_path, "docs/adr/0002-second.md", "The second rule.\n")
    assert quotes.check([finding("docs/adr/0002-second.md", "The second rule.")], tmp_path)["found"]
    assert (
        quotes.check([finding("docs/adr/0002-second.md", "The first rule.")], tmp_path)["found"]
        == []
    )


def test_a_directory_with_no_text_file_or_none_at_all_holds_no_quote(tmp_path: Path) -> None:
    (tmp_path / "docs/adr").mkdir(parents=True)
    (tmp_path / "docs/adr/image.bin").write_bytes(b"\0\1\2")
    assert reasons(quotes.check([finding("docs/adr/", QUOTE)], tmp_path)) == [
        "the record `docs/adr/` does not hold the quote" + COPY
    ]
    assert reasons(quotes.check([finding("docs/adr/0009-x.md", QUOTE)], tmp_path)) == [
        "the record `docs/adr/0009-x.md` is absent"
    ]
    assert reasons(quotes.check([finding("docs/adr/image.bin", QUOTE)], tmp_path)) == [
        "the record `docs/adr/image.bin` does not hold the quote" + COPY
    ]


def test_a_quote_does_not_span_two_files(tmp_path: Path) -> None:
    put(tmp_path, "docs/adr/0001-a.md", "A blocker or a")
    put(tmp_path, "docs/adr/0002-b.md", "major quotes what it breaks.")
    assert quotes.check([finding("docs/adr/", QUOTE)], tmp_path)["found"] == []


def test_each_record_of_the_adr_is_read_from_its_place(tmp_path: Path) -> None:
    places = {
        "AGENTS.md": "AGENTS.md",
        "GLOSSARY.md": "GLOSSARY.md",
        "DESIGN.md": "DESIGN.md",
        "guards/manifest.toml": "guards/manifest.toml",
        "binding": "docs/process/binding.md",
        "docs/process/binding.md": "docs/process/binding.md",
        "docs/features/x-y/spec.md": "docs/features/x-y/spec.md",
    }
    for place in places.values():
        put(tmp_path, place, f"the text of {place}\n")
    found = quotes.check(
        [finding(name, f"the text of {place}") for name, place in places.items()], tmp_path
    )
    assert found["not_found"] == []
    assert len(found["found"]) == len(places)


def test_a_name_that_is_not_on_the_adrs_list_is_no_record(tmp_path: Path) -> None:
    put(tmp_path, "README.md", QUOTE)
    put(tmp_path, "secret.md", QUOTE)
    names = ["README.md", "ticket", "spec.md", "docs/adr/../../secret.md", "docs/features/spec.md"]
    verdict = quotes.check([finding(name, QUOTE) for name in names], tmp_path)
    assert verdict["found"] == []
    assert reasons(verdict) == [f"`{name}` is not a record the ADR lists" for name in names]


def test_a_record_given_by_text_replaces_its_place(tmp_path: Path) -> None:
    put(tmp_path, "AGENTS.md", "the file says one thing\n")
    given: quotes.Records = {
        "ticket": [quotes.Document("the ticket says so", markdown=True)],
        "AGENTS.md": [quotes.Document("given says another", markdown=True)],
    }
    findings = [
        finding("ticket", "the ticket says so"),
        finding("AGENTS.md", "given says another"),
        finding("AGENTS.md", "the file says one thing"),
    ]
    verdict = quotes.check(findings, tmp_path, given)
    assert [entry["index"] for entry in verdict["found"]] == [0, 1]
    assert [entry["index"] for entry in verdict["not_found"]] == [2]


def test_a_given_record_that_is_absent_is_absent(tmp_path: Path) -> None:
    verdict = quotes.check([finding("ticket", QUOTE)], tmp_path, {"ticket": None})
    assert reasons(verdict) == ["the record `ticket` is absent"]


def test_a_blocker_or_major_with_no_quote_is_not_found(tmp_path: Path) -> None:
    findings: list[object] = [
        {"severity": "blocker", "title": "no breaks"},
        {"severity": "major", "title": "empty", "breaks": {}},
        {"severity": "major", "title": "a number", "breaks": {"record": "AGENTS.md", "quote": 3}},
        {"severity": "major", "title": "text", "breaks": "AGENTS.md"},
    ]
    verdict = quotes.check(findings, tmp_path)
    assert verdict["found"] == []
    assert (
        reasons(verdict) == ["it carries no `breaks` with a `record` and a `quote`, both text"] * 4
    )
    assert [entry["title"] for entry in verdict["not_found"]] == [
        "no breaks",
        "empty",
        "a number",
        "text",
    ]
    assert verdict["not_found"][2]["record"] == "AGENTS.md"


def test_a_minor_is_checked_only_when_it_carries_breaks(tmp_path: Path) -> None:
    put(tmp_path, "AGENTS.md", "a rule\n")
    findings: list[object] = [
        {"severity": "minor", "title": "plain"},
        finding("AGENTS.md", "a rule", "minor"),
        finding("AGENTS.md", "an invented rule", "minor"),
    ]
    verdict = quotes.check(findings, tmp_path)
    assert [entry["index"] for entry in verdict["found"]] == [1]
    assert [entry["index"] for entry in verdict["not_found"]] == [2]


def test_a_finding_that_is_not_an_object_is_not_found(tmp_path: Path) -> None:
    verdict = quotes.check(["text"], tmp_path)
    assert reasons(verdict) == ["it is not an object"]


@pytest.fixture
def written(tmp_path: Path) -> Path:
    put(tmp_path, "AGENTS.md", f"{QUOTE}\n")
    put(tmp_path, "ticket.md", "The ticket promises it.\n")
    return tmp_path


def run(root: Path, text: str, *options: str) -> int:
    path = root.parent / f"{root.name}-findings.json"
    path.write_text(text, "utf-8")
    return quotes.main(["quotes.py", str(path), "--root", str(root), *options])


def test_the_cli_prints_what_it_found_and_exits_0(written: Path, capsys: Out) -> None:
    text = json.dumps([finding("AGENTS.md", QUOTE)])
    assert run(written, text) == 0
    assert json.loads(capsys.readouterr().out) == {
        "found": [{"index": 0, "title": "a finding", "record": "AGENTS.md", "quote": QUOTE}],
        "not_found": [],
    }


def test_the_cli_exits_1_and_says_why_when_a_quote_is_not_found(written: Path, capsys: Out) -> None:
    text = json.dumps({"findings": [finding("AGENTS.md", "invented")]})
    assert run(written, text) == 1
    printed = json.loads(capsys.readouterr().out)
    assert printed["found"] == []
    assert (
        printed["not_found"][0]["reason"] == "the record `AGENTS.md` does not hold the quote" + COPY
    )


def test_the_cli_reads_the_ticket_from_a_path_it_is_given(written: Path, capsys: Out) -> None:
    text = json.dumps([finding("ticket", "The ticket promises it.")])
    assert run(written, text, "--record", "ticket=ticket.md") == 0
    assert run(written, text, "--record", "ticket=missing.md") == 1
    assert run(written, text) == 1
    capsys.readouterr()


@pytest.mark.parametrize("text", ["not json", '{"findings": 3}', "3", "{}"])
def test_the_cli_refuses_findings_it_cannot_read(written: Path, capsys: Out, text: str) -> None:
    assert run(written, text) == 2
    assert "cannot be read" in capsys.readouterr().err


def test_the_cli_refuses_findings_that_are_absent(tmp_path: Path, capsys: Out) -> None:
    assert quotes.main(["quotes.py", str(tmp_path / "absent.json")]) == 2
    assert "there is no such file" in capsys.readouterr().err


@pytest.mark.parametrize("pair", ["ticket", "=x", "ticket="])
def test_the_cli_refuses_a_record_that_is_not_name_equals_path(
    written: Path, capsys: Out, pair: str
) -> None:
    assert run(written, "[]", "--record", pair) == 2
    assert f"`--record {pair}` is not NAME=PATH" in capsys.readouterr().err


def test_help_prints_the_whole_description(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        quotes.main(["quotes.py", "--help"])
    shown = capsys.readouterr().out
    assert "The records, by name:" in shown
