from pathlib import Path

import pytest

import documents
from tests.repos import repository

CATALOGUE = """\
[[documents]]
name = "instructions"
paths = ["AGENTS.md", "**/AGENTS.md"]
lifetime = "standing"
prune = "never"

[[documents]]
name = "brief"
paths = ["docs/design-brief.md"]
lifetime = "standing"
prune = "pass"
keeps = ["## Purpose", "## Slice one"]

[[documents]]
name = "local feature"
paths = [".scratch/*/spec.md"]
lifetime = "steering"
prune = "none"

[[documents]]
name = "readme"
paths = ["README.md"]
lifetime = "standing"
prune = "pass"
"""
BINDING = """\
# Binding

## Documents

| Kind | Paths | Prune |
| --- | --- | --- |
| notes | `notes/*.md`, `NOTES.md` | `never` |
| guides | `docs/guides/**` | `pass` keeping `## Setup`, `## Limits` |
| readme again | `README.md` | `never` |
"""
PLACEHOLDER = "the table still has the template's placeholder row"


def binding_with(*rows: str) -> str:
    header = "| Kind | Paths | Prune |\n| --- | --- | --- |\n"
    return f"# Binding\n\n## Documents\n\n{header}" + "".join(f"{row}\n" for row in rows)


def problems(*rows: str) -> tuple[str, ...]:
    found = documents.declared(binding_with(*rows))
    assert found is not None
    return found.problems


def test_the_catalogue_names_each_kind_with_its_lifetime_and_keeps() -> None:
    kinds = {kind.name: kind for kind in documents.shipped(CATALOGUE)}
    assert list(kinds) == ["instructions", "brief", "local feature", "readme"]
    assert kinds["instructions"].paths == ("AGENTS.md", "**/AGENTS.md")
    assert kinds["brief"].keeps == ("## Purpose", "## Slice one")
    assert kinds["brief"].prune == "pass"
    assert kinds["local feature"].steering
    assert not kinds["readme"].steering


def test_a_catalogue_without_documents_names_no_kind() -> None:
    assert documents.shipped("[types]\n") == ()


def test_the_binding_rows_become_standing_kinds() -> None:
    found = documents.declared(BINDING)
    assert found is not None
    assert found.problems == ()
    notes, guides, again = found.kinds
    assert (notes.name, notes.paths, notes.prune, notes.lifetime) == (
        "notes",
        ("notes/*.md", "NOTES.md"),
        "never",
        "standing",
    )
    assert (guides.prune, guides.keeps) == ("pass", ("## Setup", "## Limits"))
    assert again.paths == ("README.md",)


@pytest.mark.parametrize("binding", [None, "# Binding\n\n## Gates\n"])
def test_a_binding_without_the_section_declares_nothing(binding: str | None) -> None:
    assert documents.declared(binding) is None


def test_a_section_without_its_table_is_a_problem() -> None:
    found = documents.declared("# Binding\n\n## Documents\n\nNone yet.\n")
    assert found is not None
    assert found.kinds == ()
    assert found.problems == ("the section lacks its table with the columns Kind | Paths | Prune",)


def test_a_section_with_no_rows_is_whole() -> None:
    found = documents.declared(binding_with())
    assert found is not None
    assert found.kinds == ()
    assert found.problems == ()


def test_the_templates_placeholder_row_is_a_problem() -> None:
    template = "| <kind> | <`globs` of its files> | <`never`, or `pass` keeping <`## heading`>> |"
    assert problems(template) == (PLACEHOLDER,)
    assert problems("| notes | `NOTES.md` | <never> |") == (PLACEHOLDER,)


def test_a_kind_listed_twice_is_a_problem() -> None:
    assert problems("| notes | `A.md` | `never` |", "| notes | `B.md` | `never` |") == (
        "the kind 'notes' is listed twice",
    )


def test_a_row_without_globs_is_a_problem() -> None:
    assert problems("| notes | none | `never` |") == (
        "'notes' names no paths: write the globs in backticks",
    )


@pytest.mark.parametrize("prune", ["sometimes", "pass keeping", "never!"])
def test_a_prune_that_is_neither_never_nor_pass_is_a_problem(prune: str) -> None:
    assert problems(f"| notes | `A.md` | {prune} |") == (
        "'notes' has a Prune of neither `never` nor `pass`",
    )


def test_only_a_pass_row_keeps_headings() -> None:
    assert problems("| notes | `A.md` | `never` keeping `## Why` |") == (
        "'notes' is `never` and keeps headings: only a `pass` row keeps any",
    )


def test_a_pass_row_keeping_a_heading_its_files_lack_is_whole() -> None:
    assert problems("| notes | `A.md` | `pass` keeping `## Nowhere` |") == ()


def test_each_kind_is_matched_by_its_paths() -> None:
    kinds = documents.all_kinds(BINDING, CATALOGUE)
    named = {
        "AGENTS.md": "instructions",
        "pkg/deep/AGENTS.md": "instructions",
        "docs/design-brief.md": "brief",
        ".scratch/slug/spec.md": "local feature",
        "NOTES.md": "notes",
        "notes/a.md": "notes",
        "docs/guides/x/y.md": "guides",
    }
    for path, name in named.items():
        found = documents.kind_of(path, kinds)
        assert found is not None
        assert found.name == name


def test_the_first_kind_that_matches_wins_and_the_catalogue_comes_first() -> None:
    kinds = documents.all_kinds(BINDING, CATALOGUE)
    found = documents.kind_of("README.md", kinds)
    assert found is not None
    assert found.name == "readme"


def test_the_bindings_rows_come_after_the_catalogues_in_order() -> None:
    kinds = documents.all_kinds(BINDING, CATALOGUE)
    assert [kind.name for kind in kinds] == [
        "instructions",
        "brief",
        "local feature",
        "readme",
        "notes",
        "guides",
        "readme again",
    ]


def test_a_path_of_no_kind_has_none() -> None:
    kinds = documents.all_kinds(BINDING, CATALOGUE)
    assert documents.kind_of("stray.md", kinds) is None


def test_unkinded_lists_only_markdown_of_no_kind_sorted() -> None:
    kinds = documents.all_kinds(BINDING, CATALOGUE)
    paths = ["z.md", "README.md", "src/app.py", "a/b.md", "docs/guides/g.md", "NOTES.md"]
    assert documents.unkinded(paths, kinds) == ["a/b.md", "z.md"]


def test_a_project_without_the_section_has_the_catalogues_kinds_only() -> None:
    kinds = documents.all_kinds(None, CATALOGUE)
    assert [kind.name for kind in kinds] == ["instructions", "brief", "local feature", "readme"]


def test_kinds_of_reads_the_projects_binding_after_the_shipped_catalogue(tmp_path: Path) -> None:
    root = repository(tmp_path, {"docs/process/binding.md": BINDING})
    kinds = documents.kinds_of(root)
    assert [kind.name for kind in kinds[-3:]] == ["notes", "guides", "readme again"]
    assert kinds[: len(documents.shipped())] == documents.shipped()
