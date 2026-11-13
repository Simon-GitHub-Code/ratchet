"""Markdown edits: a row or a section of the template in a file that has it or lacks it."""

import re
from collections.abc import Callable

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

import markdown_text
from tests.support import PLUGIN_ROOT

ENTRY = "## Entry points"
ORDER = ("tier0", "tier0-tree", "pre-commit", "hooks-installed", "check", "ratchet")
PROJECT_ROWS = ("deploy", "docs-site")
HEADER = ("Entry point", "Command")
SECTIONS = (
    "## Asking the owner",
    "## What this is",
    "## Agent skills",
    "## Layers",
    "## Non-negotiables",
)
PROJECT_SECTIONS = ("## Deploys", "## Notes")
WORDS = st.text(alphabet="abcdefgh .,`-", max_size=24).filter(
    lambda t: not t.startswith(("|", "#"))
)


def row_of(key: str, command: str | None = None) -> str:
    return f"| {key} | `{command or 'just ' + key}` |"


def compact(rows: list[tuple[str, str]]) -> list[str]:
    return [
        "| Entry point | Command |",
        "| --- | --- |",
        *(row_of(key, command) for key, command in rows),
    ]


def padded(rows: list[tuple[str, str]], aligns: tuple[str, str] = ("none", "none")) -> list[str]:
    """A table as a formatter writes it, built here without the code under test."""
    cells = [list(HEADER), *([key, f"`{command}`"] for key, command in rows)]
    widths = [max(3, *(len(row[i]) for row in cells)) for i in range(2)]

    def fit(text: str, i: int) -> str:
        spare = widths[i] - len(text)
        match aligns[i]:
            case "right":
                return " " * spare + text
            case "center":
                return " " * (spare // 2) + text + " " * (spare - spare // 2)
            case _:
                return text + " " * spare

    def rule(i: int) -> str:
        left = ":" if aligns[i] in {"left", "center"} else ""
        right = ":" if aligns[i] in {"right", "center"} else ""
        return left + "-" * (widths[i] - len(left) - len(right)) + right

    def line(row: list[str]) -> str:
        return "| " + " | ".join(fit(text, i) for i, text in enumerate(row)) + " |"

    return [
        line(cells[0]),
        "| " + " | ".join(rule(i) for i in range(2)) + " |",
        *(line(row) for row in cells[1:]),
    ]


def binding(table: list[str], *, newline: bool = True) -> str:
    text = "\n".join(
        ["# Binding", "", "Some words.", "", ENTRY, "", *table, "", "## Gates", "", "| a | b |"]
    )
    return text + "\n" if newline else text


@st.composite
def entry_points(draw: st.DrawFn) -> tuple[str, bool]:
    """A binding with an Entry points table, padded or not, and whether it was padded."""
    keys = draw(st.lists(st.sampled_from((*ORDER, *PROJECT_ROWS)), unique=True))
    rows = [(key, draw(st.sampled_from(["just " + key, "make " + key]))) for key in keys]
    is_padded = draw(st.booleans())
    table = padded(rows) if is_padded else compact([(k, c) for k, c in rows])
    return binding(table, newline=draw(st.booleans())), is_padded


def shape(line: str) -> object:
    """A line with its table cells trimmed and its rule dashes alike: padding is not content."""
    if not line.startswith("|"):
        return line
    return tuple(
        "-" if re.fullmatch(r":?-+:?", cell) else cell
        for cell in (part.strip() for part in line.strip("|").split("|"))
    )


def kept(old: str, new: str) -> bool:
    """Every line of ``old``, as ``shape`` sees it, is in ``new`` in the same order."""
    later = iter(shape(line) for line in new.split("\n"))
    return all(
        any(line == candidate for candidate in later) for line in map(shape, old.split("\n"))
    )


def keys_of(text: str) -> list[str]:
    found = markdown_text.table(text, ENTRY)
    assert found is not None
    return [row[0] for row in found.rows]


@given(entry_points(), st.sampled_from(ORDER))
def test_a_row_is_added_once_and_keeps_every_line(case: tuple[str, bool], key: str) -> None:
    text, _ = case
    want = markdown_text.with_row(ENTRY, row_of(key), ORDER)
    once = want(text)
    assert want(once) == once
    assert kept(text, once)
    assert keys_of(once).count(key) == 1


@given(entry_points(), st.sampled_from(ORDER), st.sampled_from(ORDER))
def test_two_rows_added_in_either_order_give_the_same_text(
    case: tuple[str, bool], first: str, second: str
) -> None:
    text, _ = case
    one = markdown_text.with_row(ENTRY, row_of(first), ORDER)
    two = markdown_text.with_row(ENTRY, row_of(second), ORDER)
    assert one(two(text)) == two(one(text))


@given(entry_points(), st.sampled_from(ORDER))
def test_a_padded_table_stays_padded_and_a_written_one_gets_the_template_line(
    case: tuple[str, bool], key: str
) -> None:
    text, was_padded = case
    once = markdown_text.with_row(ENTRY, row_of(key), ORDER)(text)
    found = markdown_text.table(once, ENTRY)
    assert found is not None
    if was_padded:
        assert found.padded
        assert len({len(line) for line in found.lines}) == 1
    elif key not in keys_of(text):
        before = set(text.split("\n"))
        assert set(once.split("\n")) - before == {row_of(key)}
        assert before <= set(once.split("\n"))
    else:
        assert once == text


@given(
    st.lists(st.sampled_from(ORDER), unique=True).map(lambda keys: sorted(keys, key=ORDER.index)),
    st.lists(st.sampled_from(PROJECT_ROWS), unique=True),
    st.sampled_from(ORDER),
)
def test_a_row_lands_in_the_templates_order_among_the_rows_the_template_ranks(
    ranked: list[str], mine: list[str], key: str
) -> None:
    rows = [*mine, *ranked] if len(ranked) % 2 else [*ranked, *mine]
    text = binding(compact([(k, "just " + k) for k in rows]))
    once = markdown_text.with_row(ENTRY, row_of(key), ORDER)(text)
    in_order = [k for k in keys_of(once) if k in ORDER]
    assert in_order == sorted(in_order, key=ORDER.index)
    assert [k for k in keys_of(once) if k in PROJECT_ROWS] == mine


def test_a_row_goes_after_the_nearest_earlier_template_row_present() -> None:
    text = binding(compact([("tier0", "just tier0"), ("ratchet", "just ratchet")]))
    once = markdown_text.with_row(ENTRY, row_of("check"), ORDER)(text)
    assert keys_of(once) == ["tier0", "check", "ratchet"]


def test_a_row_goes_before_the_nearest_later_row_when_none_is_earlier() -> None:
    text = binding(compact([("deploy", "just deploy"), ("check", "just check")]))
    once = markdown_text.with_row(ENTRY, row_of("tier0"), ORDER)(text)
    assert keys_of(once) == ["deploy", "tier0", "check"]


def test_a_row_goes_first_when_the_template_ranks_no_row_there() -> None:
    text = binding(compact([("deploy", "just deploy")]))
    once = markdown_text.with_row(ENTRY, row_of("tier0"), ORDER)(text)
    assert keys_of(once) == ["tier0", "deploy"]


def test_a_padded_table_is_padded_again_at_the_new_widths() -> None:
    rows = [("tier0", "just tier0"), ("ratchet", "just ratchet")]
    once = markdown_text.with_row(ENTRY, row_of("hooks-installed"), ORDER)(binding(padded(rows)))
    expected = padded(
        [("tier0", "just tier0"), ("hooks-installed", "just hooks-installed"), rows[1]]
    )
    assert once == binding(expected)


@pytest.mark.parametrize("aligns", [("left", "right"), ("center", "none"), ("right", "center")])
def test_an_aligned_padded_table_is_padded_in_its_alignment(aligns: tuple[str, str]) -> None:
    rows = [("tier0", "just tier0"), ("ratchet", "just ratchet")]
    text = binding(padded(rows, aligns))
    assert markdown_text.table(text, ENTRY) is not None
    once = markdown_text.with_row(ENTRY, row_of("check"), ORDER)(text)
    expected = padded([rows[0], ("check", "just check"), rows[1]], aligns)
    assert once == binding(expected)


def test_a_table_written_by_hand_keeps_its_own_spacing() -> None:
    table = ["| Entry point | Command |", "|---|---|", "| tier0   | `just tier0` |", "| x | y |"]
    once = markdown_text.with_row(ENTRY, row_of("check"), ORDER)(binding(table))
    assert once == binding([*table[:3], row_of("check"), table[3]])


def test_a_row_the_table_already_has_is_left_as_the_project_wrote_it() -> None:
    text = binding(compact([("tier0", "make all")]))
    assert markdown_text.with_row(ENTRY, row_of("tier0"), ORDER)(text) == text


@pytest.mark.parametrize(
    "table",
    [
        [],
        ["| Entry point | Command |"],
        ["| Entry point | Command |", "| nope | nope |", "| tier0 | x |"],
        ["| Entry point | Command |", "| --- | --- |", "| tier0 | x | y |"],
        ["| Entry point | Command |", "| --- | --- |", "| tier0 | x"],
        ["| Entry point | Command |", "| --- | --- |", "| tier0 | x \\|"],
    ],
)
def test_a_table_that_does_not_split_cleanly_is_left_alone(table: list[str]) -> None:
    text = binding(table)
    assert markdown_text.table(text, ENTRY) is None
    assert markdown_text.with_row(ENTRY, row_of("check"), ORDER)(text) == text


def test_there_is_no_table_without_the_heading_or_under_it() -> None:
    assert markdown_text.table("# Binding\n\n| a | b |\n| - | - |\n", ENTRY) is None
    assert (
        markdown_text.table(f"{ENTRY}\n\nNo table here.\n\n## Gates\n\n| a | b |\n", ENTRY) is None
    )
    absent = markdown_text.with_row(ENTRY, row_of("check"), ORDER)
    assert absent(None) == ""
    assert absent("# Binding\n") == "# Binding\n"


def test_only_the_first_table_under_the_heading_is_read() -> None:
    first = compact([("tier0", "just tier0")])
    text = binding([*first, "", "Another:", "", *compact([("check", "just check")])])
    found = markdown_text.table(text, ENTRY)
    assert found is not None
    assert [row[0] for row in found.rows] == ["tier0"]


def test_a_row_must_be_in_the_templates_order() -> None:
    with pytest.raises(ValueError, match="not in the template's order"):
        markdown_text.with_row(ENTRY, row_of("elsewhere"), ORDER)


def test_a_row_with_other_columns_than_the_table_is_left_alone() -> None:
    text = binding(compact([("tier0", "just tier0")]))
    assert markdown_text.with_row(ENTRY, "| check |", ORDER)(text) == text


def test_every_row_of_the_templates_entry_points_returns_when_it_is_deleted() -> None:
    template = (PLUGIN_ROOT / "skills/adopt/templates/docs/process/binding.md").read_text(
        encoding="utf-8"
    )
    found = markdown_text.table(template, ENTRY)
    assert found is not None
    order = tuple(row[0] for row in found.rows)
    for line in found.lines[2:]:
        deleted = template.replace(line + "\n", "", 1)
        assert markdown_text.with_row(ENTRY, line, order)(deleted) == template


def block_of(heading: str, lines: tuple[str, ...] = ("Body.",)) -> str:
    return "\n".join([heading, "", *lines]) + "\n"


@st.composite
def documents(draw: st.DrawFn) -> str:
    """A Markdown file of ``## `` sections, template ones and the project's, in any order."""
    headings = draw(st.lists(st.sampled_from((*SECTIONS, *PROJECT_SECTIONS)), unique=True))
    parts = []
    for heading in headings:
        body = draw(st.lists(WORDS, max_size=3))
        sub = draw(st.sampled_from([[], ["### Sub", "", "text"]]))
        parts.append("\n".join([heading, "", *body, *sub]).rstrip() + "\n")
    title = draw(st.sampled_from(["", "# Title\n\nIntro.\n\n"]))
    text = title + "\n".join(parts)
    return text if draw(st.booleans()) else text.rstrip("\n")


def section_wants(heading: str) -> Callable[[str | None], str]:
    return markdown_text.with_section(heading, block_of(heading), SECTIONS)


@given(documents(), st.sampled_from(SECTIONS))
def test_a_section_is_added_once_and_keeps_every_line(text: str, heading: str) -> None:
    want = section_wants(heading)
    once = want(text)
    assert want(once) == once
    assert kept(text, once) or not text.strip()
    assert once.split("\n").count(heading) == 1
    assert once.endswith("\n") or not text.endswith("\n")


@given(documents(), st.sampled_from(SECTIONS), st.sampled_from(SECTIONS))
def test_two_sections_added_in_either_order_give_the_same_text(
    text: str, first: str, second: str
) -> None:
    one, two = section_wants(first), section_wants(second)
    assert one(two(text)) == two(one(text))


@given(documents(), st.sampled_from(SECTIONS))
def test_a_section_stands_apart_from_its_neighbours_and_in_the_templates_order(
    text: str, heading: str
) -> None:
    assume(heading not in text.split("\n"))
    once = section_wants(heading)(text)
    lines = once.split("\n")
    at = lines.index(heading)
    assert at == 0 or lines[at - 1] == ""
    assert lines[at + 2] == "Body."
    assert lines[at + 3 :][:1] in ([], [""])
    ranked = [line for line in lines if line in SECTIONS]
    before = [line for line in text.split("\n") if line in SECTIONS]
    if before == sorted(before, key=SECTIONS.index):
        assert ranked == sorted(ranked, key=SECTIONS.index)


def test_a_section_goes_after_the_section_the_template_puts_before_it() -> None:
    text = "# T\n\n## What this is\n\nWords.\n\n### Sub\n\nMore.\n\n## Layers\n\nL.\n"
    once = section_wants("## Agent skills")(text)
    assert once == (
        "# T\n\n## What this is\n\nWords.\n\n### Sub\n\nMore.\n\n"
        "## Agent skills\n\nBody.\n\n## Layers\n\nL.\n"
    )


def test_a_section_goes_before_the_section_the_template_puts_after_it() -> None:
    text = "## Notes\n\nN.\n\n## Layers\n\nL.\n"
    assert section_wants("## What this is")(text) == (
        "## Notes\n\nN.\n\n## What this is\n\nBody.\n\n## Layers\n\nL.\n"
    )
    assert section_wants("## Asking the owner")("## Layers\n\nL.\n") == (
        "## Asking the owner\n\nBody.\n\n## Layers\n\nL.\n"
    )
    assert section_wants("## Asking the owner")("Intro\n## Layers\n\nL.") == (
        "Intro\n\n## Asking the owner\n\nBody.\n\n## Layers\n\nL."
    )


def test_a_section_goes_last_when_the_template_ranks_no_section_there() -> None:
    assert section_wants("## Layers")("# T\n\n## Notes\n\nN.\n") == (
        "# T\n\n## Notes\n\nN.\n\n## Layers\n\nBody.\n"
    )
    assert section_wants("## Layers")("# T\n\n## Notes\n\nN.\n\n\n") == (
        "# T\n\n## Notes\n\nN.\n\n## Layers\n\nBody.\n\n\n"
    )


def test_a_file_with_nothing_in_it_becomes_the_section() -> None:
    assert section_wants("## Layers")("") == "## Layers\n\nBody.\n"
    assert section_wants("## Layers")(None) == "## Layers\n\nBody.\n"
    assert section_wants("## Layers")("\n\n") == "## Layers\n\nBody.\n"


def test_a_section_the_file_has_is_left_as_the_project_wrote_it() -> None:
    text = "## Layers\n\nMy own words.\n"
    assert section_wants("## Layers")(text) == text


def test_a_section_needs_a_place_in_the_order_and_a_block_that_starts_with_its_heading() -> None:
    with pytest.raises(ValueError, match="not in the template's order"):
        markdown_text.with_section("## Elsewhere", block_of("## Elsewhere"), SECTIONS)
    with pytest.raises(ValueError, match="must start with its heading"):
        markdown_text.with_section("## Layers", "Body.\n", SECTIONS)


def test_every_section_of_the_agents_template_returns_when_it_is_deleted() -> None:
    template = (PLUGIN_ROOT / "skills/init/templates/AGENTS.md").read_text(encoding="utf-8")
    parts = re.split(r"(?m)^(?=## )", template)
    order = tuple(part.split("\n", 1)[0] for part in parts[1:])
    for index, part in enumerate(parts[1:], start=1):
        deleted = "".join(parts[:index] + parts[index + 1 :])
        want = markdown_text.with_section(part.split("\n", 1)[0], part, order)
        assert want(deleted.rstrip("\n") + "\n") == template.rstrip("\n") + "\n"


def test_the_readers_see_sections_and_tables_as_the_conformance_gate_does() -> None:
    text = "## Gates\n\nWords here and enough of them.\n\n## Next\n\nTBD\n"
    assert markdown_text.section(text, "## Gates") == "\n\nWords here and enough of them.\n"
    assert markdown_text.section(text, "## Last") == ""
    assert markdown_text.section("## Gates\nend", "## Gates") == "\nend"
    assert not markdown_text.empty_body(text, "## Gates")
    assert markdown_text.empty_body(text, "## Next")
    assert markdown_text.empty_body(text, "## Last")
    cells = markdown_text.table_cells("| A | B |\n| - | - |\n| x | y |\n| z |  |\nplain\n")
    assert cells == [("x", "y"), ("z", "")]


def test_the_templates_own_entry_points_table_splits_and_is_written_by_hand() -> None:
    template = PLUGIN_ROOT / "skills/adopt/templates/docs/process/binding.md"
    found = markdown_text.table(template.read_text(encoding="utf-8"), ENTRY)
    assert found is not None
    assert found.header == HEADER
    assert not found.padded
    assert len(found.rows) == len(found.lines) - 2


def test_a_body_with_two_tables_gives_the_rows_of_both_and_neither_header() -> None:
    body = "| A | B |\n| - | - |\n| x | y |\n\nBetween.\n\n| C | D |\n| :-- | --: |\n| z | w |\n"
    assert markdown_text.table_cells(body) == [("x", "y"), ("z", "w")]
