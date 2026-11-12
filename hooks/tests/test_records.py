"""Additive records: what an agent may add to the glossary and the ADRs, and what still asks.

``ADDITIVE`` and ``NOT_ADDITIVE`` are the corpus of the pure judge, one row per way a glossary
can change; ``ADR_ROWS`` is the same for decision records. ``EDITS`` drives the hook with the tool
calls an agent makes. Every row of the refused tables is an attempt to get a change to an
existing record past the check.
"""

import io
import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

import lowered
import prose
from ratchet_guards import diff, hook, records
from ratchet_guards.manifest import MANIFEST_PATH, parse
from ratchet_guards.records import Before, NotAdditiveError
from tests import repos
from tests.support import (
    HOME,
    MANIFEST_TEXT,
    PLUGIN_ROOT,
    ROOT,
    WORKTREE,
    FakeFilesystem,
    make_guard,
)
from tests.test_hook import run_shim

GLOSSARY = """# Glossary

The vocabulary of inventory. Use these words, and only these words.

## Language

### Links

**Link**:
The pairing of one short code with one target, created once and never edited.
_Avoid_: mapping, shortlink

**Short code**:
The seven-character string that names a link in a URL path.
It is issued by the service, never chosen by the caller.
_Avoid_: slug, token

### People

**Runbook author**:
The platform-team developer who creates links.
_Avoid_: customer
"""
CLICK = "\n**Click**:\nOne redirect served for a link.\n_Avoid_: hit, visit\n"
LINK = "**Link**:\nThe pairing of one short code with one target, created once and never edited.\n"
SHORT_CODE = (
    "**Short code**:\nThe seven-character string that names a link in a URL path.\n"
    "It is issued by the service, never chosen by the caller.\n_Avoid_: slug, token\n"
)
ADR = """---
status: accepted
---
# A link expires a year after its last redirect

Runbooks rot. We decided that a link nobody follows for a year is deleted.

## Refutation criterion

Wrong if an author asks for an expired link back more than once a quarter.
"""
ADRS = ("0001-adopt-the-ratchet-method.md", "0002-gates.md", "0003-codes.md", "README.md")


def change(old: str, new: str, text: str = GLOSSARY) -> str:
    assert text.count(old) == 1, old
    return text.replace(old, new)


ADDITIVE: list[tuple[str, str, list[str]]] = [
    ("nothing changed", GLOSSARY, []),
    ("a term at the end", GLOSSARY + CLICK, ["term Click (avoid: hit, visit)"]),
    (
        "a term between two terms of a cluster",
        change("\n**Short code**:", CLICK.removeprefix("\n") + "\n**Short code**:"),
        ["term Click (avoid: hit, visit)"],
    ),
    ("a term with no Avoid list", GLOSSARY + "\n**Click**:\nOne redirect.\n", ["term Click"]),
    ("a term defined on its own line", GLOSSARY + "\n**Click**: one redirect.\n", ["term Click"]),
    ("a term with the colon inside the bold", GLOSSARY + "\n**Click:**\nOne.\n", ["term Click"]),
    (
        "an Avoid item on an existing term",
        change("mapping, shortlink", "mapping, shortlink, alias"),
        ["avoid alias (under Link)"],
    ),
    (
        "a second Avoid line on an existing term",
        change("_Avoid_: slug, token\n", "_Avoid_: slug, token\n_Avoid_: key, id\n"),
        ["avoid key, id (under Short code)"],
    ),
    ("the Avoid items in another order", change("mapping, shortlink", "shortlink, mapping"), []),
    (
        "a new cluster with a term",
        GLOSSARY + "\n### Traffic\n" + CLICK,
        ["term Click (avoid: hit, visit)"],
    ),
    ("blank lines and trailing spaces", change("### Links\n", "### Links  \n\n\n") + "\n\n", []),
    ("Windows line endings", GLOSSARY.replace("\n", "\r\n"), []),
    (
        "a term and an Avoid item together",
        change("_Avoid_: customer", "_Avoid_: customer, user") + CLICK,
        ["term Click (avoid: hit, visit)", "avoid user (under Runbook author)"],
    ),
]


@pytest.mark.parametrize(("label", "after", "expected"), ADDITIVE, ids=[row[0] for row in ADDITIVE])
def test_additions_to_a_glossary_pass_and_are_listed(
    label: str, after: str, expected: list[str]
) -> None:
    del label
    assert records.additions("GLOSSARY.md", Before(GLOSSARY), after) == expected


NOT_ADDITIVE: list[tuple[str, str, str]] = [
    (
        "a term renamed",
        change("**Link**:", "**Short link**:"),
        "the term 'Link' is gone, changed or moved",
    ),
    (
        "a term's case changed",
        change("**Short code**:", "**Short Code**:"),
        "the term 'Short code' is gone, changed or moved",
    ),
    (
        "a term deleted",
        change(LINK + "_Avoid_: mapping, shortlink\n\n", ""),
        "the term 'Link' is gone, changed or moved",
    ),
    (
        "a definition edited",
        change("created once and never edited", "created once"),
        "it changes the definition of Link",
    ),
    (
        "a definition edit hidden by reordering",
        change(SHORT_CODE, "")
        + "\n"
        + SHORT_CODE.replace("never chosen by the caller", "or chosen by the caller"),
        "the term 'Short code' is gone, changed or moved",
    ),
    (
        "two terms swapped, nothing else",
        change(SHORT_CODE, "").replace("**Link**:", SHORT_CODE + "\n**Link**:"),
        "the term 'Short code' is gone, changed or moved",
    ),
    (
        "a second space inside a definition",
        change("one short code", "one  short code"),
        "it changes the definition of Link",
    ),
    (
        "a sentence added to a definition",
        change("never edited.\n", "never edited.\nUnless an admin edits it.\n"),
        "it changes the definition of Link",
    ),
    (
        "a blank line splitting a definition",
        change("URL path.\nIt is", "URL path.\n\nIt is"),
        "it changes the definition of Short code",
    ),
    (
        "a definition pulled up beside its term",
        change("**Link**:\nThe pairing", "**Link**: The pairing"),
        "it changes the definition of Link",
    ),
    (
        "the colon moved inside the bold",
        change("**Link**:", "**Link:**"),
        "it changes the definition of Link",
    ),
    (
        "an Avoid item removed",
        change("mapping, shortlink", "mapping"),
        "it drops shortlink from the _Avoid_ list of Link",
    ),
    (
        "an Avoid list removed",
        change("_Avoid_: slug, token\n", ""),
        "it drops slug, token from the _Avoid_ list of Short code",
    ),
    (
        "an Avoid item's case changed",
        change("slug, token", "Slug, token"),
        "it drops slug from the _Avoid_ list of Short code",
    ),
    (
        "an Avoid item moved to another term",
        change("mapping, shortlink", "mapping").replace("slug, token", "slug, token, shortlink"),
        "it drops shortlink from the _Avoid_ list of Link",
    ),
    (
        "an Avoid item turned into a placeholder",
        change("slug, token", "<slug>, token"),
        "it drops slug from the _Avoid_ list of Short code",
    ),
    (
        "an Avoid item wrapped in backticks",
        change("slug, token", "`slug`, token"),
        "it drops slug from the _Avoid_ list of Short code",
    ),
    (
        "an Avoid line indented out of a strict reader's sight",
        change("_Avoid_: slug, token", "  _Avoid_: slug, token"),
        "line 16 is an indented heading, term or _Avoid_ line",
    ),
    (
        "an Avoid line respelled",
        change("_Avoid_: slug, token", "_avoid_: slug, token"),
        "it changes the definition of Short code",
    ),
    (
        "a term line indented",
        change("**Link**:", "    **Link**:"),
        "line 9 is an indented heading, term or _Avoid_ line",
    ),
    (
        "a heading indented into a code block",
        change("### People", "    ### People"),
        "line 18 is an indented heading, term or _Avoid_ line",
    ),
    (
        "a second entry that shadows a term",
        GLOSSARY + "\n**Link**:\nAny URL at all.\n",
        "it defines Link twice",
    ),
    (
        "a shadow in another case",
        GLOSSARY + "\n**link**:\nAny URL at all.\n",
        "it defines link twice",
    ),
    (
        "a shadow with a zero-width space",
        GLOSSARY + "\n**Li\u200bnk**:\nAny URL at all.\n",
        "it defines Li\u200bnk twice",
    ),
    (
        "a shadow with the colon inside the bold",
        GLOSSARY + "\n**Link:**\nAny URL at all.\n",
        "it defines Link twice",
    ),
    (
        "a shadow with doubled spaces",
        GLOSSARY + "\n**Short  code**:\nAny string.\n",
        "it defines Short  code twice",
    ),
    (
        "a term the glossary tells you to avoid",
        GLOSSARY + "\n**Slug**:\nThe short code.\n",
        "it adds the term Slug, which Short code lists under _Avoid_",
    ),
    (
        "an Avoid item that bans an existing term",
        change("slug, token", "slug, token, link"),
        "it puts link under _Avoid_ for Short code, and Link is a term",
    ),
    (
        "a new term whose Avoid list bans an existing term",
        GLOSSARY + "\n**Click**:\nOne redirect.\n_Avoid_: `Target`, Link\n",
        "it puts Link under _Avoid_ for Click, and Link is a term",
    ),
    (
        "a term moved under a new heading",
        change("**Short code**:", "### Deprecated\n\n**Short code**:"),
        "the term 'Short code' is gone, changed or moved",
    ),
    (
        "a heading removed",
        change("### People\n", ""),
        "the heading '### People' is gone, changed or moved",
    ),
    (
        "a heading renamed",
        change("### People", "### Roles"),
        "the heading '### People' is gone, changed or moved",
    ),
    (
        "the introduction rewritten",
        change("and only these words.", "where you like."),
        (
            "the text outside the entries 'The vocabulary of inventory. Use these words, and "
            "only these words.' is gone, changed or moved"
        ),
    ),
    (
        "a sentence added to the introduction",
        change("## Language", "The Avoid lists are advice.\n\n## Language"),
        "it adds text outside any term's entry: 'The Avoid lists are advice.'",
    ),
    (
        "an Avoid line added outside any entry",
        change("## Language\n", "## Language\n\n_Avoid_: link\n"),
        "it adds text outside any term's entry: '_Avoid_: link'",
    ),
    (
        "a new section",
        GLOSSARY + "\n## Exceptions\n",
        "it adds the heading '## Exceptions', which is not a cluster heading (### or deeper)",
    ),
    (
        "a new title",
        "# Old glossary\n\n" + GLOSSARY,
        "it adds the heading '# Old glossary', which is not a cluster heading (### or deeper)",
    ),
    (
        "a code fence that hides the terms after it",
        change("### People", "**Note**:\nSee below.\n```\n\n### People") + "\n**End**:\n```\n",
        "the new term Note holds a code fence or an HTML comment",
    ),
    (
        "a comment opened in a new term's Avoid list",
        change("### People", "**Note**:\nSee below.\n_Avoid_: <!--\n\n### People"),
        "the new term Note holds a code fence or an HTML comment",
    ),
    (
        "a comment opened in an existing term's Avoid list",
        change("mapping, shortlink", "mapping, shortlink, <!--"),
        "a new _Avoid_ item of Link holds a code fence or an HTML comment",
    ),
    (
        "a comment opened in a cluster heading",
        GLOSSARY + "\n### Old <!--\n",
        "it adds the heading '### Old <!--', which is not a cluster heading (### or deeper)",
    ),
    ("a Write that truncates", "", "the heading '# Glossary' is gone, changed or moved"),
    (
        "a Write that keeps only the head",
        GLOSSARY[: GLOSSARY.index("**Link**")],
        "the term 'Link' is gone, changed or moved",
    ),
    (
        "a vertical tab where a newline was",
        change("**Link**:\nThe pairing", "**Link**:\x0bThe pairing"),
        "line 9 holds a line break that is not a newline",
    ),
    (
        "a line separator where a newline was",
        change("**Link**:\nThe pairing", "**Link**:\u2028The pairing"),
        "line 9 holds a line break that is not a newline",
    ),
]


@pytest.mark.parametrize(
    ("label", "after", "reason"), NOT_ADDITIVE, ids=[row[0] for row in NOT_ADDITIVE]
)
def test_a_change_to_what_the_glossary_holds_is_not_additive(
    label: str, after: str, reason: str
) -> None:
    del label
    with pytest.raises(NotAdditiveError) as raised:
        records.additions("GLOSSARY.md", Before(GLOSSARY), after)
    assert str(raised.value) == reason


def test_a_glossary_created_deleted_or_unreadable_at_the_merge_base_is_not_additive() -> None:
    with pytest.raises(NotAdditiveError, match=r"^it is deleted$"):
        records.additions("GLOSSARY.md", Before(GLOSSARY), None)
    with pytest.raises(NotAdditiveError, match=r"^it creates a glossary, and a new vocabulary"):
        records.additions("src/billing/GLOSSARY.md", Before(None), GLOSSARY)
    twice = GLOSSARY + "\n**Link**:\nAgain.\n"
    with pytest.raises(
        NotAdditiveError, match=r"^the merge base's copy cannot be read: it defines Link twice$"
    ):
        records.additions("GLOSSARY.md", Before(twice), twice + CLICK)


def test_a_glossary_in_any_directory_is_read_as_one() -> None:
    assert records.additions("src/billing/GLOSSARY.md", Before(GLOSSARY), GLOSSARY + CLICK) == [
        "term Click (avoid: hit, visit)"
    ]


def test_the_parsed_glossary_keeps_order_headings_and_loose_lines() -> None:
    parsed = records.glossary(GLOSSARY)
    assert [(item.kind.name, item.text, item.under) for item in parsed.outline] == [
        ("HEADING", "# Glossary", ""),
        (
            "LOOSE",
            "The vocabulary of inventory. Use these words, and only these words.",
            "# Glossary",
        ),
        ("HEADING", "## Language", ""),
        ("HEADING", "### Links", ""),
        ("ENTRY", "Link", "### Links"),
        ("ENTRY", "Short code", "### Links"),
        ("HEADING", "### People", ""),
        ("ENTRY", "Runbook author", "### People"),
    ]
    assert parsed.entries["Short code"] == records.Entry(
        "Short code",
        (
            "**Short code**:",
            "The seven-character string that names a link in a URL path.",
            "It is issued by the service, never chosen by the caller.",
        ),
        ("slug", "token"),
    )
    loose = records.glossary("intro\n\n**A**:\none\n\n\ntwo\n\n")
    assert loose.outline == (
        records.Item(records.Kind.LOOSE, "intro", ""),
        records.Item(records.Kind.ENTRY, "A", ""),
    )
    assert loose.entries["A"].lines == ("**A**:", "one", "", "", "two")
    assert records.glossary("#hashtag\n**Never** chosen\n").entries == {}


@pytest.mark.parametrize(
    ("name", "folded"),
    [
        ("Short  Code", "short code"),
        ("`slug`", "slug"),
        ("_Link_", "link"),
        ("Li\u200bnk", "link"),
        ("\uff2c\uff49\uff4e\uff4b", "link"),
        ("STRASSE", "strasse"),
        ("Stra\u00dfe", "strasse"),
    ],
)
def test_fold_reads_a_name_as_a_reader_would(name: str, folded: str) -> None:
    assert records.fold(name) == folded


ADR_ROWS: list[tuple[str, str, str | None, str, str]] = [
    (
        "the next number",
        "docs/adr/0004-links-expire.md",
        None,
        ADR,
        "ADR 0004 A link expires a year after its last redirect",
    ),
    (
        "a later number (the conformance gate owns gaps)",
        "docs/adr/0006-links-expire.md",
        None,
        ADR,
        "ADR 0006 A link expires a year after its last redirect",
    ),
    (
        "no front matter",
        "docs/adr/0004-x.md",
        None,
        "# We keep it plain\n\nBecause.\n",
        "ADR 0004 We keep it plain",
    ),
    (
        "a context's own records",
        "src/billing/docs/adr/0004-x.md",
        None,
        "# Invoices are immutable\n",
        "ADR 0004 Invoices are immutable",
    ),
    (
        "an edit of a record the default branch holds",
        "docs/adr/0003-codes.md",
        ADR,
        ADR + "\nAnd one more thing.\n",
        "!it changes a decision record the default branch already holds",
    ),
    (
        "an unchanged existing record",
        "docs/adr/0003-codes.md",
        ADR,
        ADR,
        "!it changes a decision record the default branch already holds",
    ),
    (
        "an existing number under a new file name",
        "docs/adr/0003-codes-may-change.md",
        None,
        ADR,
        "!its number 0003 is not above 0003, the highest at the merge base",
    ),
    (
        "a number below the highest",
        "docs/adr/0002-b.md",
        None,
        ADR,
        "!its number 0002 is not above 0003, the highest at the merge base",
    ),
    ("number zero", "docs/adr/0000-x.md", None, ADR, "!its number 0000 is not above 0003"),
    (
        "an underscore name",
        "docs/adr/0004_x.md",
        None,
        ADR,
        "!0004_x.md is not named NNNN-<slug>.md",
    ),
    (
        "an upper-case name",
        "docs/adr/0004-X.md",
        None,
        ADR,
        "!0004-X.md is not named NNNN-<slug>.md",
    ),
    ("a short number", "docs/adr/4-x.md", None, ADR, "!4-x.md is not named NNNN-<slug>.md"),
    ("a long number", "docs/adr/00004-x.md", None, ADR, "!00004-x.md is not named NNNN-<slug>.md"),
    ("another extension", "docs/adr/0004-x.txt", None, ADR, "!0004-x.txt is not named"),
    ("a file that is no record", "docs/adr/notes.md", None, ADR, "!notes.md is not named"),
    (
        "a Relaxes line",
        "docs/adr/0004-x.md",
        None,
        ADR + "\nRelaxes: python.coverage.core.line from 90 to 80\n",
        "!it carries a Relaxes: line, and relaxing a ratchet is the owner's",
    ),
    (
        "a Relaxes line in a list",
        "docs/adr/0004-x.md",
        None,
        ADR + "\n- Relaxes: ceilings.seconds.test from 120 to 300\n",
        "!it carries a Relaxes: line",
    ),
    (
        "a Relaxes line after a form feed",
        "docs/adr/0004-x.md",
        None,
        ADR + "\x0cRelaxes: a.b from 1 to 0",
        "!it carries a Relaxes: line",
    ),
    (
        "a Relaxes line in another case",
        "docs/adr/0004-x.md",
        None,
        ADR + "\nRELAXES: a.b from 1 to 0\n",
        "!it carries a Relaxes: line",
    ),
    (
        "a record that supersedes another",
        "docs/adr/0004-x.md",
        None,
        ADR + "\nThis supersedes ADR 0003.\n",
        "!it speaks of superseding, and superseding a decision record is the owner's",
    ),
    (
        "a record born superseded",
        "docs/adr/0004-x.md",
        None,
        ADR.replace("status: accepted", "status: superseded by ADR-0002"),
        "!it speaks of superseding",
    ),
    (
        "a superseded-by line",
        "docs/adr/0004-x.md",
        None,
        ADR.replace("status: accepted", "status: accepted\nSuperseded-By: 0002"),
        "!it speaks of superseding",
    ),
    (
        "a misspelt superseding",
        "docs/adr/0004-x.md",
        None,
        ADR + "\nSupercedes 0002.\n",
        "!it speaks of superseding",
    ),
    (
        "digits of another script",
        "docs/adr/\u0660\u0660\u0660\u0664-x.md",
        None,
        ADR,
        "!\u0660\u0660\u0660\u0664-x.md is not named",
    ),
    ("no title", "docs/adr/0004-x.md", None, "## Context\n\n#No space\n", "!it has no '# ' title"),
    ("an empty file", "docs/adr/0004-x.md", None, "", "!it has no '# ' title"),
    (
        "a nested directory",
        "docs/adr/drafts/0004-x.md",
        None,
        ADR,
        "!0004-x.md is neither a GLOSSARY.md nor a record under docs/adr/",
    ),
    (
        "a directory that only ends like the records'",
        "mydocs/adr/0004-x.md",
        None,
        ADR,
        "!0004-x.md is neither a GLOSSARY.md nor a record under docs/adr/",
    ),
    (
        "another guard path",
        "docs/process/binding.md",
        "# Binding\n",
        "# Binding\n\nMore.\n",
        "!binding.md is neither a GLOSSARY.md nor a record under docs/adr/",
    ),
]


@pytest.mark.parametrize(
    ("label", "path", "existing", "after", "expected"), ADR_ROWS, ids=[row[0] for row in ADR_ROWS]
)
def test_decision_records(
    *, label: str, path: str, existing: str | None, after: str, expected: str
) -> None:
    del label
    before = Before(existing, ADRS)
    if not expected.startswith("!"):
        assert records.additions(path, before, after) == [expected]
        return
    with pytest.raises(NotAdditiveError) as raised:
        records.additions(path, before, after)
    assert str(raised.value).startswith(expected.removeprefix("!"))


def test_a_deleted_record_and_an_oddly_numbered_neighbour() -> None:
    with pytest.raises(NotAdditiveError, match=r"^it is deleted$"):
        records.additions("docs/adr/0003-codes.md", Before(ADR, ADRS), None)
    odd = Before(None, ("0009_legacy.md", "12.md", "notes.md"))
    with pytest.raises(NotAdditiveError, match=r"^its number 0012 is not above 0012"):
        records.additions("docs/adr/0012-x.md", odd, ADR)
    assert records.additions("docs/adr/0013-x.md", odd, ADR) == [
        "ADR 0013 A link expires a year after its last redirect"
    ]


def test_the_note_says_what_was_added_or_why_not() -> None:
    assert records.note("GLOSSARY.md", Before(GLOSSARY), GLOSSARY + CLICK) == (
        "additions: term Click (avoid: hit, visit)"
    )
    assert records.note("GLOSSARY.md", Before(GLOSSARY), GLOSSARY) == "additions: none"
    assert records.note("GLOSSARY.md", Before(GLOSSARY), None) == "not additive: it is deleted"
    assert records.note("GLOSSARY.md", None, GLOSSARY) == (
        "not additive: the merge base cannot be read"
    )


WRITTEN: list[tuple[str, str, dict[str, object], str | None, str | None]] = [
    ("a Write is its content", "Write", {"content": "new"}, "old", "new"),
    ("a Write of a new file", "Write", {"content": "new"}, None, "new"),
    ("a Write with no content", "Write", {"file_path": "x"}, "old", None),
    ("a Write whose content is no string", "Write", {"content": 3}, "old", None),
    ("an Edit of one occurrence", "Edit", {"old_string": "a", "new_string": "b"}, "xay", "xby"),
    ("an Edit that does not match", "Edit", {"old_string": "q", "new_string": "b"}, "xay", None),
    ("an Edit that matches twice", "Edit", {"old_string": "a", "new_string": "b"}, "aa", None),
    (
        "an Edit of every occurrence",
        "Edit",
        {"old_string": "a", "new_string": "b", "replace_all": True},
        "aa",
        "bb",
    ),
    ("an Edit that creates a file", "Edit", {"old_string": "", "new_string": "b"}, None, "b"),
    ("an empty old string on a file", "Edit", {"old_string": "", "new_string": "b"}, "x", None),
    ("an Edit with other keys", "Edit", {"old_str": "a", "new_str": "b"}, "xay", None),
    ("an Edit with no new string", "Edit", {"old_string": "a"}, "xay", None),
    (
        "an Edit whose replace_all is no boolean",
        "Edit",
        {"old_string": "a", "new_string": "b", "replace_all": "yes"},
        "xay",
        None,
    ),
    (
        "a MultiEdit runs its edits in order",
        "MultiEdit",
        {
            "edits": [
                {"old_string": "a", "new_string": "bb"},
                {"old_string": "b", "new_string": "c", "replace_all": True},
            ]
        },
        "xay",
        "xccy",
    ),
    (
        "a MultiEdit whose edits cancel",
        "MultiEdit",
        {"edits": [{"old_string": "a", "new_string": "b"}, {"old_string": "b", "new_string": "a"}]},
        "xay",
        "xay",
    ),
    (
        "a MultiEdit with one edit that does not apply",
        "MultiEdit",
        {"edits": [{"old_string": "a", "new_string": "b"}, {"old_string": "q", "new_string": "a"}]},
        "xay",
        None,
    ),
    ("a MultiEdit with no edits", "MultiEdit", {"edits": []}, "xay", None),
    ("a MultiEdit whose edits are no list", "MultiEdit", {"edits": "a"}, "xay", None),
    ("a MultiEdit holding a non-edit", "MultiEdit", {"edits": ["a"]}, "xay", None),
    ("Grok's tool name", "search_replace", {"old_string": "a", "new_string": "b"}, "xay", None),
    ("a notebook edit", "NotebookEdit", {"new_source": "b"}, "xay", None),
]


@pytest.mark.parametrize(
    ("label", "tool", "tool_input", "existing", "expected"),
    WRITTEN,
    ids=[row[0] for row in WRITTEN],
)
def test_the_text_a_call_would_write(
    *,
    label: str,
    tool: str,
    tool_input: dict[str, object],
    existing: str | None,
    expected: str | None,
) -> None:
    del label
    assert records.written(tool, tool_input, existing) == expected


MERGED = {
    "GLOSSARY.md": GLOSSARY,
    "docs/adr/0001-adopt-the-ratchet-method.md": ADR,
    "docs/adr/0002-gates.md": ADR,
    "docs/adr/0003-codes.md": ADR,
}
NEW_ADR = "/repo/docs/adr/0004-links-expire.md"
ON_DISK = {"/repo/GLOSSARY.md": GLOSSARY, "/repo/docs/adr/0003-codes.md": ADR}


def edit(old: str, new: str) -> dict[str, object]:
    return {"old_string": old, "new_string": new}


# Each row: the tool, its input, and the reason the hook gives when it asks (None: it passes).
EDITS: list[tuple[str, str, dict[str, object], str | None]] = [
    (
        "an Edit adds a term",
        "Edit",
        {"file_path": "GLOSSARY.md", **edit("\n### People", CLICK + "\n### People")},
        None,
    ),
    (
        "a Write adds a term",
        "Write",
        {"file_path": "/repo/GLOSSARY.md", "content": GLOSSARY + CLICK},
        None,
    ),
    (
        "a MultiEdit adds a term and an Avoid item",
        "MultiEdit",
        {
            "file_path": "GLOSSARY.md",
            "edits": [
                edit("slug, token", "slug, token, key"),
                edit("\n### People", CLICK + "\n### People"),
            ],
        },
        None,
    ),
    ("a Write adds a decision record", "Write", {"file_path": NEW_ADR, "content": ADR}, None),
    ("an Edit creates a decision record", "Edit", {"file_path": NEW_ADR, **edit("", ADR)}, None),
    (
        "an Edit changes a definition",
        "Edit",
        {"file_path": "GLOSSARY.md", **edit("created once and never edited", "created once")},
        "it changes the definition of Link",
    ),
    (
        "an Edit removes an Avoid item",
        "Edit",
        {"file_path": "GLOSSARY.md", **edit("mapping, shortlink", "mapping")},
        "it drops shortlink from the _Avoid_ list of Link",
    ),
    (
        "a Write truncates the glossary",
        "Write",
        {"file_path": "GLOSSARY.md", "content": "# Glossary\n"},
        (
            "the text outside the entries 'The vocabulary of inventory. Use these words, and "
            "only these words.' is gone, changed or moved"
        ),
    ),
    (
        "a MultiEdit adds a term and drops an Avoid item behind it",
        "MultiEdit",
        {
            "file_path": "GLOSSARY.md",
            "edits": [edit("\n### People", CLICK + "\n### People"), edit("slug, token", "slug")],
        },
        "it drops token from the _Avoid_ list of Short code",
    ),
    (
        "a MultiEdit whose edits cancel changes nothing",
        "MultiEdit",
        {
            "file_path": "GLOSSARY.md",
            "edits": [
                edit("slug, token", "slug"),
                edit("_Avoid_: slug\n", "_Avoid_: slug, token\n"),
            ],
        },
        None,
    ),
    (
        "an Edit of a merged decision record",
        "Edit",
        {"file_path": "docs/adr/0003-codes.md", **edit("Runbooks rot.", "Runbooks last.")},
        "it changes a decision record the default branch already holds",
    ),
    (
        "a Write to an existing number under a new name",
        "Write",
        {"file_path": "docs/adr/0003-codes-may-change.md", "content": ADR},
        "its number 0003 is not above 0003, the highest at the merge base",
    ),
    (
        "a Write of a record that relaxes a ratchet",
        "Write",
        {"file_path": NEW_ADR, "content": ADR + "\nRelaxes: a.b from 2 to 1\n"},
        "it carries a Relaxes: line, and relaxing a ratchet is the owner's",
    ),
    (
        "a Write of a record that supersedes another",
        "Write",
        {"file_path": NEW_ADR, "content": ADR + "\nSupersedes ADR 0002.\n"},
        "it speaks of superseding, and superseding a decision record is the owner's",
    ),
    (
        "a file that is no record in the records directory",
        "Write",
        {"file_path": "docs/adr/README.md", "content": "# Records\n"},
        "README.md is not named NNNN-<slug>.md",
    ),
    (
        "an Edit that does not apply",
        "Edit",
        {"file_path": "GLOSSARY.md", **edit("no such text", "x")},
        "the guard cannot tell what this Edit call would write",
    ),
    (
        "a notebook edit of the glossary",
        "NotebookEdit",
        {"notebook_path": "GLOSSARY.md", "new_source": GLOSSARY + CLICK},
        "the guard cannot tell what this NotebookEdit call would write",
    ),
    (
        "Grok's edit, whose input the guard has not verified",
        "search_replace",
        {"file_path": "GLOSSARY.md", **edit("\n### People", CLICK + "\n### People")},
        "the guard cannot tell what this search_replace call would write",
    ),
    (
        "pi's adapter, which sends the path alone",
        "Write",
        {"file_path": "GLOSSARY.md"},
        "the guard cannot tell what this Write call would write",
    ),
]


def decide(
    tool: str, tool_input: dict[str, object], filesystem: FakeFilesystem | None = None
) -> str | None:
    """The detail of the ask on one edit made in ``/repo``, or ``None`` when the hook passes it."""
    payload = {"tool_name": tool, "tool_input": tool_input, "cwd": str(ROOT)}
    filesystem = filesystem or FakeFilesystem(contents=dict(ON_DISK), merged=dict(MERGED))
    verdict = hook.decide(payload, make_guard(filesystem=filesystem))
    if verdict is None:
        return None
    assert verdict.rule == "guard-path-edit"
    return verdict.detail


@pytest.mark.parametrize(
    ("label", "tool", "tool_input", "reason"), EDITS, ids=[row[0] for row in EDITS]
)
def test_the_hook_passes_an_addition_and_asks_on_anything_else(
    *, label: str, tool: str, tool_input: dict[str, object], reason: str | None
) -> None:
    del label
    detail = decide(tool, tool_input)
    if reason is None:
        assert detail is None
    else:
        assert detail is not None
        assert detail.endswith(f" This edit is not an addition: {reason}")


def test_the_ask_names_the_group_its_reason_and_what_the_edit_changes() -> None:
    asked = decide("Edit", {"file_path": "GLOSSARY.md", **edit("mapping, shortlink", "mapping")})
    assert asked == (
        "GLOSSARY.md is in guard group 'records'. Decision records and the vocabulary: an agent "
        "adds to them, the owner changes them. This edit is not an addition: it drops shortlink "
        "from the _Avoid_ list of Link"
    )


def test_a_branch_may_rework_what_it_added_and_not_what_the_owner_merged() -> None:
    # Why: the merge base is the standard, not the work tree. The conformance gate may send an
    # agent back to its own new record; a record the owner merged stays the owner's.
    drafted = FakeFilesystem(
        contents={**ON_DISK, NEW_ADR: "# A draft\n", "/repo/GLOSSARY.md": GLOSSARY + CLICK},
        merged=dict(MERGED),
    )
    assert decide("Edit", {"file_path": NEW_ADR, **edit("# A draft\n", ADR)}, drafted) is None
    reworded = {"file_path": "GLOSSARY.md", **edit("One redirect served", "One visit served")}
    assert decide("Edit", reworded, drafted) is None
    changed = FakeFilesystem(
        contents={"/repo/GLOSSARY.md": GLOSSARY.replace("mapping, shortlink", "mapping")},
        merged=dict(MERGED),
    )
    asked = decide(
        "Edit", {"file_path": "GLOSSARY.md", **edit("slug, token", "slug, token, key")}, changed
    )
    assert asked is not None
    assert asked.endswith("it drops shortlink from the _Avoid_ list of Link")


def test_a_record_is_judged_in_the_checkout_that_holds_it() -> None:
    filesystem = FakeFilesystem(contents={f"{WORKTREE}/GLOSSARY.md": GLOSSARY}, merged=dict(MERGED))
    path = f"{WORKTREE}/GLOSSARY.md"
    assert decide("Write", {"file_path": path, "content": GLOSSARY + CLICK}, filesystem) is None
    asked = decide("Write", {"file_path": path, "content": ""}, filesystem)
    assert asked is not None
    assert "is not an addition" in asked


def test_without_a_merge_base_or_a_checkout_the_hook_asks() -> None:
    call = {"file_path": "GLOSSARY.md", "content": GLOSSARY + CLICK}
    unborn = FakeFilesystem(contents=dict(ON_DISK), merged=None)
    asked = decide("Write", call, unborn)
    assert asked is not None
    assert asked.endswith("there is no merge base with the default branch to compare it with")
    at_home = parse(
        '[[group]]\nname = "notes"\nreason = "r"\nedit = "append"\npaths = ["~/GLOSSARY.md"]\n'
    )
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": str(HOME / "GLOSSARY.md"), "content": GLOSSARY},
        "cwd": str(ROOT),
    }
    verdict = hook.decide(payload, make_guard(manifest=at_home))
    assert verdict is not None
    assert verdict.detail.endswith("the guard cannot tell what this Write call would write")


def test_a_codex_patch_on_a_record_still_asks() -> None:
    patch = (
        "*** Begin Patch\n*** Add File: docs/adr/0004-links-expire.md\n+# A title\n*** End Patch"
    )
    payload = {"tool_name": "apply_patch", "tool_input": {"command": patch}, "cwd": str(ROOT)}
    verdict = hook.decide(payload, make_guard())
    assert verdict is not None
    assert verdict.rule == "guard-path-edit"
    assert verdict.detail.startswith("docs/adr/0004-links-expire.md is in guard group 'records'.")


def fake_git(answers: dict[tuple[str, ...], str | None]) -> records.Git:
    def git(args: Sequence[str]) -> str | None:
        return answers[tuple(args)]

    return git


def test_before_reads_the_file_and_its_neighbours_at_the_merge_base() -> None:
    listing = ("ls-tree", "--full-tree", "-z", "--name-only", "abc")
    git = fake_git(
        {
            (*listing, "docs/adr/"): "docs/adr/0001-a.md\0docs/adr/0002-b.md\0",
            ("show", "abc:docs/adr/0002-b.md"): "# B\n",
            listing: "GLOSSARY.md\0README.md\0docs\0",
            ("show", "abc:GLOSSARY.md"): None,
            (*listing, "none/"): "",
            (*listing, "broken/"): None,
        }
    )
    names = ("0001-a.md", "0002-b.md")
    assert records.before(git, "abc", "docs/adr/0002-b.md") == Before("# B\n", names)
    assert records.before(git, "abc", "docs/adr/0003-c.md") == Before(None, names)
    assert records.before(git, "abc", "none/GLOSSARY.md") == Before(None, ())
    assert records.before(git, "abc", "broken/GLOSSARY.md") is None
    assert records.before(git, "abc", "GLOSSARY.md") is None


MANIFEST = (
    '[[group]]\nname = "records"\nreason = "An agent adds, the owner changes."\n'
    'edit = "append"\npaths = ["docs/adr/**", "GLOSSARY.md"]\n\n'
    '[[group]]\nname = "decisions"\nreason = "The standards."\npaths = ["AGENTS.md"]\n'
)


@pytest.fixture
def adopted(tmp_path: Path) -> Path:
    """A repository with the manifest, a glossary and three records on ``main``, on a branch."""
    files = {
        MANIFEST_PATH: MANIFEST,
        "AGENTS.md": "# Agents\n",
        "GLOSSARY.md": GLOSSARY,
        **{f"docs/adr/{name}": ADR for name in ADRS[:3]},
    }
    (tmp_path / "adopted").mkdir()
    root = repos.repository(tmp_path / "adopted", files)
    repos.branch(root)
    return root


def answer(root: Path, tool: str, tool_input: dict[str, object]) -> str:
    """The decision ``hook.main`` prints for one call in ``root``, or ``"pass"``."""
    stdout = io.StringIO()
    case = {"tool_name": tool, "tool_input": tool_input, "cwd": str(root)}
    assert hook.main(root, io.StringIO(json.dumps(case)), stdout) == 0
    if not stdout.getvalue():
        return "pass"
    return str(json.loads(stdout.getvalue())["hookSpecificOutput"]["permissionDecision"])


def guard_diff(root: Path, *flags: str) -> tuple[int, str, str]:
    """The exit code, stdout and summary of ``guard-diff`` run in ``root``."""
    stdout = io.StringIO()
    summary = root.parent / "summary.md"
    summary.unlink(missing_ok=True)
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr("sys.stdout", stdout)
        code = diff.main(["--root", str(root), "--summary", str(summary), *flags])
    return code, stdout.getvalue(), summary.read_text(encoding="utf-8")


def test_the_hook_and_guard_diff_agree_in_a_real_repository(adopted: Path) -> None:
    glossary, record = adopted / "GLOSSARY.md", adopted / "docs/adr/0004-links-expire.md"
    assert (
        answer(adopted, "Write", {"file_path": str(glossary), "content": GLOSSARY + CLICK})
        == "pass"
    )
    assert answer(adopted, "Write", {"file_path": str(record), "content": ADR}) == "pass"
    glossary.write_text(GLOSSARY + CLICK, encoding="utf-8")
    record.write_text(ADR, encoding="utf-8")
    code, printed, summary = guard_diff(adopted, "--fail-unless-additive")
    assert code == 0
    assert printed == (
        "records\tGLOSSARY.md\n  additions: term Click (avoid: hit, visit)\n"
        "records\tdocs/adr/0004-links-expire.md\n"
        "  additions: ADR 0004 A link expires a year after its last redirect\n"
    )
    assert "## :warning: 2 guard path(s) changed" in summary
    assert "| `GLOSSARY.md` | records | additions: term Click (avoid: hit, visit) |" in summary
    assert guard_diff(adopted, "--fail-if-present")[0] == 1

    shorter = (GLOSSARY + CLICK).replace("mapping, shortlink", "mapping")
    merged = adopted / "docs/adr/0003-codes.md"
    assert answer(adopted, "Write", {"file_path": str(glossary), "content": shorter}) == "ask"
    assert (
        answer(adopted, "Edit", {"file_path": str(merged), **edit("Runbooks rot.", "No.")}) == "ask"
    )
    glossary.write_text(shorter, encoding="utf-8")
    merged.write_text(ADR.replace("Runbooks rot.", "No."), encoding="utf-8")
    code, printed, _ = guard_diff(adopted, "--fail-unless-additive")
    assert code == 1
    assert printed == (
        "records\tGLOSSARY.md\n"
        "  not additive: it drops shortlink from the _Avoid_ list of Link\n"
        "records\tdocs/adr/0003-codes.md\n"
        "  not additive: it changes a decision record the default branch already holds\n"
        "records\tdocs/adr/0004-links-expire.md\n"
        "  additions: ADR 0004 A link expires a year after its last redirect\n"
    )
    merged.unlink()
    assert "  not additive: it is deleted\n" in guard_diff(adopted)[1]


def test_another_guard_path_fails_the_additive_exit_and_committed_additions_pass(
    adopted: Path,
) -> None:
    (adopted / "GLOSSARY.md").write_text(GLOSSARY + CLICK, encoding="utf-8")
    repos.commit(adopted, "docs(glossary): add click")
    assert guard_diff(adopted, "--fail-unless-additive")[0] == 0
    assert guard_diff(adopted)[0] == 0
    (adopted / "AGENTS.md").write_text("# Agents\n\nDo as you like.\n", encoding="utf-8")
    code, printed, _ = guard_diff(adopted, "--fail-unless-additive")
    assert code == 1
    assert printed == (
        "decisions\tAGENTS.md\nrecords\tGLOSSARY.md\n  additions: term Click (avoid: hit, visit)\n"
    )


def test_the_merge_base_is_the_standard_when_the_default_branch_moves_on(adopted: Path) -> None:
    # Why: in a queue of parallel branches the default branch gains terms a branch has not seen;
    # judged against its tip, every later addition on that branch would look like a removal.
    repos.git(adopted, "checkout", "-q", "main")
    (adopted / "GLOSSARY.md").write_text(GLOSSARY + CLICK, encoding="utf-8")
    repos.commit(adopted, "docs(glossary): add click")
    repos.git(adopted, "checkout", "-q", "change")
    more = GLOSSARY + "\n**Expiry**:\nThe day a link stops redirecting.\n"
    target = str(adopted / "GLOSSARY.md")
    assert answer(adopted, "Write", {"file_path": target, "content": more}) == "pass"
    assert answer(adopted, "Write", {"file_path": target, "content": "# Glossary\n"}) == "ask"


def test_a_checkout_with_no_default_branch_or_no_commit_has_no_merge_base(tmp_path: Path) -> None:
    filesystem = hook.LocalFilesystem()
    unborn = tmp_path / "unborn"
    unborn.mkdir()
    repos.git(unborn, "init", "-q", "-b", "main")
    assert filesystem.merge_base(unborn, "GLOSSARY.md") is None
    trunk = tmp_path / "trunk"
    trunk.mkdir()
    repos.git(trunk, "init", "-q", "-b", "trunk")
    repos.git(
        trunk,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "x",
    )
    assert filesystem.merge_base(trunk, "GLOSSARY.md") is None
    (tmp_path / "orphan").mkdir()
    orphan = repos.repository(tmp_path / "orphan", {"GLOSSARY.md": GLOSSARY})
    repos.git(orphan, "checkout", "-q", "--orphan", "island")
    repos.commit(orphan, "feat(core): an island")
    assert filesystem.merge_base(orphan, "GLOSSARY.md") is None
    repos.git(orphan, "checkout", "-q", "main")
    assert filesystem.merge_base(orphan, "GLOSSARY.md") == Before(GLOSSARY, ("GLOSSARY.md",))


def test_the_shim_passes_an_addition_and_asks_on_a_change(adopted: Path) -> None:
    def shim(content: str) -> str:
        tool_input = {"file_path": str(adopted / "GLOSSARY.md"), "content": content}
        done = run_shim(
            PLUGIN_ROOT / "hooks/guard.py",
            {"tool_name": "Write", "tool_input": tool_input, "cwd": str(adopted)},
        )
        assert done.returncode == 0, done.stderr
        return done.stdout

    assert shim(GLOSSARY + CLICK) == ""
    asked = json.loads(shim(GLOSSARY.replace("slug, token", "slug")))["hookSpecificOutput"]
    assert asked["permissionDecision"] == "ask"
    assert asked["permissionDecisionReason"].startswith(
        "Ratchet guard: this tool call edits a guard path; only the owner approves a gate change: "
        "GLOSSARY.md is in guard group 'records'. An agent adds, the owner changes. This edit is "
        "not an addition: it drops token from the _Avoid_ list of Short code. Guard paths come "
    )


def test_record_notes_cover_only_groups_marked_append(tmp_path: Path) -> None:
    manifest = parse(MANIFEST_TEXT)
    groups = {group.name: group for group in manifest.groups}
    (tmp_path / "GLOSSARY.md").write_text(GLOSSARY + CLICK, encoding="utf-8")
    listing = ("ls-tree", "--full-tree", "-z", "--name-only", "abc")
    git = fake_git(
        {
            ("merge-base", "main", "HEAD"): "abc\n",
            listing: "GLOSSARY.md\0",
            ("show", "abc:GLOSSARY.md"): GLOSSARY,
            (*listing, "docs/adr/"): None,
        }
    )
    matches = [
        ("CLAUDE.md", groups["decisions"]),
        ("GLOSSARY.md", groups["records"]),
        ("docs/adr/0004-x.md", groups["records"]),
    ]
    notes = diff.record_notes(git, "main", tmp_path, matches=matches)
    assert notes == {
        "GLOSSARY.md": "additions: term Click (avoid: hit, visit)",
        "docs/adr/0004-x.md": "not additive: the merge base cannot be read",
    }
    assert [diff.additive(notes.get(path)) for path, _ in matches] == [False, True, False]


def test_the_template_marks_the_glossary_and_the_adrs_and_nothing_else_additive() -> None:
    templates = PLUGIN_ROOT / "skills/adopt/templates"
    manifest = parse((templates / MANIFEST_PATH).read_text(encoding="utf-8"))
    additive = [group for group in manifest.groups if group.additive]
    assert [(group.name, group.patterns) for group in additive] == [
        ("records", ("docs/adr/**", "GLOSSARY.md"))
    ]
    assert manifest.group_of("docs/adr/0002-x.md") is additive[0]
    assert manifest.group_of("GLOSSARY.md") is additive[0]
    for path in ("GLOSSARY-MAP.md", "AGENTS.md", "docs/process/binding.md", "DESIGN.md"):
        group = manifest.group_of(path)
        assert group is not None, path
        assert (group.name, group.additive, group.locked) == ("decisions", False, False)
    glossary = (templates / "GLOSSARY.md").read_text(encoding="utf-8")
    assert records.additions("GLOSSARY.md", Before(glossary), glossary + CLICK) == [
        "term Click (avoid: hit, visit)"
    ]


WORDS = st.sampled_from(["alias", "key", "hit", "visit", "pointer", "handle", "ref"])
NAMES = st.sampled_from(["Click", "Expiry", "Owner", "Visit count", "Dead link"])


@given(
    terms=st.lists(
        st.tuples(NAMES, st.lists(WORDS, max_size=3, unique=True)),
        max_size=3,
        unique_by=lambda t: t[0],
    ),
    gained=st.lists(WORDS, max_size=3, unique=True),
)
def test_every_built_addition_passes_and_is_listed(
    terms: list[tuple[str, list[str]]], gained: list[str]
) -> None:
    after = GLOSSARY.replace("mapping, shortlink", ", ".join(["mapping", "shortlink", *gained]))
    expected = []
    for name, avoid in terms:
        after += f"\n**{name}**:\nA thing.\n" + (f"_Avoid_: {', '.join(avoid)}\n" if avoid else "")
        expected.append(f"term {name}" + (f" (avoid: {', '.join(avoid)})" if avoid else ""))
    if gained:
        expected.append(f"avoid {', '.join(gained)} (under Link)")
    assert records.additions("GLOSSARY.md", Before(GLOSSARY), after) == expected


INSERTS = [
    "",
    "**Click**:",
    "**Link**:",
    "One redirect.",
    "_Avoid_: hit",
    "_Avoid_: link, slug",
    "  _Avoid_: hit",
    "### Traffic",
    "## More",
    "```",
    "<!--",
    "The Avoid lists are advice.",
]
LINES = GLOSSARY.split("\n")
MUTATIONS = st.one_of(
    st.tuples(st.just("delete"), st.integers(0, len(LINES) - 1), st.just("")),
    st.tuples(st.just("insert"), st.integers(0, len(LINES)), st.sampled_from(INSERTS)),
    st.tuples(st.just("replace"), st.integers(0, len(LINES) - 1), st.sampled_from(INSERTS)),
    st.tuples(st.just("swap"), st.integers(0, len(LINES) - 2), st.just("")),
    st.tuples(st.just("indent"), st.integers(0, len(LINES) - 1), st.just("")),
    st.tuples(st.just("cut"), st.integers(0, len(LINES) - 1), st.just("")),
)


def mutate(lines: list[str], mutation: tuple[str, int, str]) -> list[str]:
    kind, at, text = mutation
    at = min(at, len(lines) - 1) if lines else 0
    if not lines or kind == "insert":
        return [*lines[:at], text, *lines[at:]]
    if kind == "delete":
        return lines[:at] + lines[at + 1 :]
    if kind == "replace":
        return [*lines[:at], text, *lines[at + 1 :]]
    if kind == "swap":
        return [*lines[:at], *reversed(lines[at : at + 2]), *lines[at + 2 :]]
    if kind == "indent":
        return [*lines[:at], "  " + lines[at], *lines[at + 1 :]]
    return [*lines[:at], lines[at][: len(lines[at]) // 2], *lines[at + 1 :]]


def in_order(needles: list[str], haystack: list[str]) -> bool:
    rest = iter(haystack)
    return all(needle in rest for needle in needles)


@given(mutations=st.lists(MUTATIONS, min_size=1, max_size=4))
def test_no_accepted_change_loses_a_line_or_a_banned_word(
    mutations: list[tuple[str, int, str]],
) -> None:
    # Why: two oracles that share no code with the judge. The prose gate's own reader says which
    # words are banned, and a plain line comparison says which lines are still there, in order.
    lines = LINES
    for mutation in mutations:
        lines = mutate(lines, mutation)
    after = "\n".join(lines)
    try:
        records.additions("GLOSSARY.md", Before(GLOSSARY), after)
    except NotAdditiveError:
        return
    assert prose.avoided(GLOSSARY).items() <= prose.avoided(after).items()
    kept = [line for line in LINES if line and not line.startswith("_Avoid_:")]
    assert in_order(kept, [line.rstrip() for line in lines])


@given(
    lead=st.sampled_from(["", "- ", "> ", "  ", "\t", "* ", "\x0c"]),
    key=st.sampled_from(["python.coverage.core.line", "ceilings.seconds.test", "g.absolute"]),
    old=st.sampled_from(["90", "120.5", "true", "-1"]),
    new=st.sampled_from(["80", "300", "false", "removed"]),
    body=st.sampled_from([ADR, "# T\n", ADR + "\n## More\n"]),
    tail=st.sampled_from(["", "\n", "\nAnd so on.\n"]),
)
def test_a_record_the_lowered_gate_would_read_as_a_relaxation_never_passes(  # noqa: PLR0913 # -- one argument per drawn part of the line
    *, lead: str, key: str, old: str, new: str, body: str, tail: str
) -> None:
    line = f"{lead}Relaxes: {key} from {old} to {new}"
    assert lowered.RELAXES.match(line.splitlines()[-1])
    with pytest.raises(NotAdditiveError, match="Relaxes"):
        records.additions("docs/adr/0004-x.md", Before(None, ADRS), f"{body}\n{line}{tail}")
