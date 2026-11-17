from pathlib import Path

import documents
from tests.repos import branch, commit, git, repository, write
from tests.test_merge_ready import BASE, BINDING, VERDICT, judged, stamp

BRIEF = documents.BRIEF
PRUNE_RECORD = {**VERDICT, "slug": "prune-pages"}
FULL = (
    "# Brief\n\n## Purpose\n\nA purpose.\n\n## In the owner's words\n\nWords.\n\n"
    "## Slice one\n\nOne.\n\n## Extra\n\nMore text.\n"
)
LOSES = (
    "document: `docs/design-brief.md` loses `## Slice one`, which a brief page keeps, so the"
    " merge is the owner's"
)
PRODUCT_CUT = (
    "prune: `PRODUCT.md` is a product and design page, which a prune pass does not cut,"
    " so the merge is the owner's"
)
DELETED = (
    "document: `docs/design-brief.md` is deleted, and a brief page keeps `## Purpose`, so the"
    " merge is the owner's"
)
MOVED_LOSES = (
    "document: `notes/b.md` loses `## Purpose`, which a notes page keeps, so the merge is the"
    " owner's"
)
NOTES_TABLE = (
    "## Documents\n\n| Kind | Paths | Prune |\n| --- | --- | --- |\n"
    "| notes | `notes/*.md` | pass keeping `## Purpose` |\n\n"
)
SPEC = "docs/features/thing/spec.md"
LOCAL = ".scratch/f/spec.md"


def held(tmp_path: Path, **extra: str) -> Path:
    repository(tmp_path, {**BASE, BRIEF: FULL, **extra})
    branch(tmp_path, "feature/4-thing")
    return tmp_path


def test_a_page_that_loses_a_kept_heading_is_the_owners_with_no_prune_record(
    tmp_path: Path,
) -> None:
    root = held(tmp_path)
    write(root, BRIEF, FULL.replace("## Slice one\n\nOne.\n\n", ""))
    commit(root, "feat: the change")
    stamp(root)
    assert judged(root) == [LOSES]


def test_a_prune_record_adds_the_page_a_pass_does_not_cut(tmp_path: Path) -> None:
    root = held(tmp_path, **{"PRODUCT.md": "# Product\n\nLonger text.\n"})
    write(root, "PRODUCT.md", "# Product\n\nShorter.\n")
    commit(root, "docs: cut")
    stamp(root, PRUNE_RECORD)
    assert judged(root) == [PRODUCT_CUT]


def test_a_prune_subject_alone_adds_the_page_a_pass_does_not_cut(tmp_path: Path) -> None:
    root = held(tmp_path, **{"PRODUCT.md": "# Product\n\nLonger text.\n"})
    write(root, "PRODUCT.md", "# Product\n\nShorter.\n")
    commit(root, "docs(prune)!: cut a product page")
    stamp(root)
    assert judged(root) == [PRODUCT_CUT]


def test_a_page_cut_in_a_prune_pass_keeping_its_headings_is_the_agents(tmp_path: Path) -> None:
    root = held(tmp_path)
    write(root, BRIEF, FULL.replace("More text.", "Less."))
    commit(root, "docs(prune): cut the brief")
    stamp(root, PRUNE_RECORD)
    assert judged(root) == []


def test_an_expired_unit_deleted_whole_is_still_the_agents(tmp_path: Path) -> None:
    root = held(tmp_path, **{SPEC: "# Thing\n\n## Purpose\n\nBody.\n"})
    (root / SPEC).unlink()
    commit(root, "docs: expire")
    stamp(root)
    assert judged(root) == []


def test_a_feature_editing_the_body_of_its_own_spec_is_still_the_agents(tmp_path: Path) -> None:
    root = held(tmp_path, **{LOCAL: "# Thing\n\n## Purpose\n\nBody.\n"})
    write(root, LOCAL, "# Thing\n\n## Purpose\n\nNew body.\n")
    commit(root, "feat: the change")
    stamp(root)
    assert judged(root) == []


def test_a_brief_deleted_whole_is_the_owners(tmp_path: Path) -> None:
    root = held(tmp_path)
    (root / BRIEF).unlink()
    commit(root, "feat: the change")
    stamp(root)
    assert judged(root) == [DELETED]


def test_a_page_moved_to_a_path_of_its_kind_is_read_as_a_move(tmp_path: Path) -> None:
    binding = BASE[BINDING].replace("## Dependencies", NOTES_TABLE + "## Dependencies", 1)
    page = "# Notes\n\n## Purpose\n\nA purpose.\n\n## Detail\n\n" + "Detail line.\n" * 20
    repository(
        tmp_path, {**BASE, BINDING: binding, "notes/a.md": page, "notes/z.md": page + "Z.\n"}
    )
    branch(tmp_path, "feature/4-thing")
    git(tmp_path, "mv", "notes/a.md", "notes/b.md")
    git(tmp_path, "mv", "notes/z.md", "notes/y.md")
    commit(tmp_path, "feat: the change")
    stamp(tmp_path)
    assert judged(tmp_path) == []


def test_a_page_moved_and_stripped_of_a_kept_heading_loses_it_under_the_target(
    tmp_path: Path,
) -> None:
    binding = BASE[BINDING].replace("## Dependencies", NOTES_TABLE + "## Dependencies", 1)
    page = "# Notes\n\n## Purpose\n\nA purpose.\n\n## Detail\n\n" + "Detail line.\n" * 20
    repository(tmp_path, {**BASE, BINDING: binding, "notes/a.md": page})
    branch(tmp_path, "feature/4-thing")
    git(tmp_path, "mv", "notes/a.md", "notes/b.md")
    write(tmp_path, "notes/b.md", page.replace("## Purpose\n\nA purpose.\n\n", ""))
    commit(tmp_path, "feat: the change")
    stamp(tmp_path)
    assert judged(tmp_path) == [MOVED_LOSES]


def test_a_kept_heading_moved_within_the_page_is_not_a_loss(tmp_path: Path) -> None:
    root = held(tmp_path)
    write(
        root,
        BRIEF,
        "# Brief\n\n## Slice one\n\nOne.\n\n## Purpose\n\nA purpose.\n\n"
        "## In the owner's words\n\nWords.\n\n## Extra\n\nMore text.\n",
    )
    commit(root, "feat: the change")
    stamp(root)
    assert judged(root) == []
