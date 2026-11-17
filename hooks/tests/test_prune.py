import shutil
from pathlib import Path

import pytest

import documents
import merge_ready
import owed
import route
from tests.repos import commit, git, repository, skeleton, write
from tests.test_merge_ready import BASE, VERDICT, judged, stamp
from tests.test_route import ADOPTED, BINDING, hooks_are_installed

__all__ = ["hooks_are_installed"]

PAGE = "# A\n\nThe tool is `src/a.py`, and `src/a.py:run` is its entry.\n"
COMMENTS = (
    '[[owed]]\nrule = "comments"\npath = "src/a.py"\nproblem = "narrates the loop"\n'
    'since = "0.12.0"\n\n[[owed]]\nrule = "comments"\npath = "src/b.py"\n'
    'problem = "claims an order"\nsince = "0.12.0"\n'
)
ARCHITECTURE = (
    '[[owed]]\nrule = "architecture"\npath = "docs/process/binding.md"\n'
    'problem = "no part declared"\nsince = "0.12.0"\n'
)
CARRY_ON = f"Or carry on with {route.SPEC}: nothing is red for it"
LINE = (
    "prunable lines: {kinds} (a count, not a gate;"
    " /ratchet:prune pages cuts them when a pass is due)"
)


def binding(after: str = "") -> str:
    held = f"- Prune after: {after}\n" if after else ""
    return f"{BINDING}\n## Documents\n\n{held}"


def adopted(tmp_path: Path, after: str = "3", **files: str) -> Path:
    base = {
        **ADOPTED,
        "docs/process/binding.md": binding(after),
        "README.md": PAGE,
        ".gitignore": ".ratchet/\n",
    }
    return repository(tmp_path, {**base, "src/a.py": "def run():\n    pass\n", **files})


def land(root: Path, count: int, subject: str = "feat: more") -> None:
    for _ in range(count):
        number = git(root, "rev-list", "--count", "HEAD")
        write(root, f"src/more_{number}.py", "x = 1\n")
        commit(root, subject)


def change_cited(root: Path) -> None:
    write(root, "src/a.py", "def run():\n    return 1\n")
    commit(root, "fix: the tool returns")


def stale_world(tmp_path: Path, after: str = "3") -> Path:
    root = adopted(tmp_path, after)
    change_cited(root)
    land(root, 3)
    return root


def test_owed_comments_come_before_the_ticket_step_and_offer_it_beside_them(
    tmp_path: Path,
) -> None:
    root = adopted(tmp_path, **{"quality/owed.toml": COMMENTS})
    step = route.decide(root, None)
    assert step == route.Step(
        "/ratchet:prune comments",
        "2 comment(s) are owed in quality/owed.toml, and a batch deletes or encodes as many as"
        f" the change-size ceiling allows. {CARRY_ON}",
        route.SPEC,
        "prune",
    )


def test_a_ledger_with_no_comments_names_no_prune_step(tmp_path: Path) -> None:
    root = adopted(tmp_path, **{"quality/owed.toml": ARCHITECTURE.replace("architecture", "bites")})
    assert route.decide(root, None).command == route.SPEC


def test_the_owed_architecture_comes_before_the_prune_step(tmp_path: Path) -> None:
    root = adopted(tmp_path, **{"quality/owed.toml": COMMENTS + "\n" + ARCHITECTURE})
    step = route.decide(root, None)
    assert (step.command, step.then, step.stage) == (
        route.ARCHITECTURE,
        "/ratchet:prune comments",
        "architecture",
    )
    assert step.why.endswith("Or carry on with /ratchet:prune comments: nothing is red for it")


def test_the_prune_step_stands_before_the_step_it_offers(tmp_path: Path) -> None:
    root = adopted(tmp_path, **{"quality/owed.toml": COMMENTS})
    offered = route.decide(root, None).then
    (root / "quality/owed.toml").unlink()
    commit(root, "chore: the ledger is empty")
    assert route.decide(root, None).command == offered


def test_comments_outrank_a_due_pages_pass(tmp_path: Path) -> None:
    root = stale_world(tmp_path)
    write(root, "quality/owed.toml", COMMENTS)
    commit(root, "chore: owed")
    assert route.decide(root, None).command == "/ratchet:prune comments"


def test_a_page_that_cites_a_path_changed_after_it_makes_a_pass_due(tmp_path: Path) -> None:
    root = stale_world(tmp_path)
    count = git(root, "rev-list", "--first-parent", "--count", "main")
    step = route.decide(root, None)
    assert step == route.Step(
        "/ratchet:prune pages",
        f"{count} merges have landed since the last prune pass, and README.md cites"
        " `src/a.py`, which changed after it: a pass cuts what the code now says."
        f" {CARRY_ON}",
        route.SPEC,
        "prune",
    )


def test_too_few_merges_since_the_start_is_not_due(tmp_path: Path) -> None:
    root = stale_world(tmp_path, after="50")
    assert route.decide(root, None).command == route.SPEC


def test_the_default_is_ten_merges(tmp_path: Path) -> None:
    root = adopted(tmp_path, after="")
    change_cited(root)
    land(root, 7)
    assert git(root, "rev-list", "--first-parent", "--count", "main") == "9"
    assert route.decide(root, None).command == route.SPEC
    land(root, 1)
    assert route.decide(root, None).command == "/ratchet:prune pages"


def test_a_page_written_after_the_path_it_cites_is_not_stale(tmp_path: Path) -> None:
    root = stale_world(tmp_path)
    write(root, "README.md", PAGE + "\nA line.\n")
    commit(root, "docs: the page follows the tool")
    land(root, 3)
    assert route.decide(root, None).command == route.SPEC


def test_a_page_of_a_kind_marked_never_does_not_count(tmp_path: Path) -> None:
    root = adopted(
        tmp_path, **{"docs/adr/0001-a.md": "# A\n\nSee `src/a.py`.\n", "README.md": "# A\n"}
    )
    change_cited(root)
    land(root, 3)
    assert route.decide(root, None).command == route.SPEC


def test_a_citation_of_a_path_the_tree_lacks_does_not_count(tmp_path: Path) -> None:
    root = adopted(tmp_path, **{"README.md": "# A\n\nSee `src/gone.py`.\n"})
    land(root, 4)
    assert route.decide(root, None).command == route.SPEC


def test_a_page_not_yet_committed_does_not_count(tmp_path: Path) -> None:
    root = adopted(tmp_path, **{"README.md": "# A\n"})
    change_cited(root)
    land(root, 3)
    write(root, "docs/README.md", "# Docs\n\nSee `src/a.py`.\n")
    assert documents.stale(root) == []


def test_a_pass_commit_on_the_line_restarts_the_count(tmp_path: Path) -> None:
    root = stale_world(tmp_path)
    commit(root, "docs(prune): the pages that repeat the code")
    assert documents.landed_since_pass(root, "main") == 0
    assert route.decide(root, None).command == route.SPEC
    land(root, 2)
    assert documents.landed_since_pass(root, "main") == 2


def test_a_pass_merged_from_its_branch_restarts_the_count(tmp_path: Path) -> None:
    root = stale_world(tmp_path)
    git(root, "switch", "-q", "-c", "chore/prune-pages-1")
    commit(root, "docs(prune): the pages that repeat the code")
    commit(root, "docs: a second commit")
    git(root, "switch", "-q", "main")
    git(root, "merge", "-q", "--no-ff", "chore/prune-pages-1", "-m", "Merge branch 'prune'")
    assert documents.landed_since_pass(root, "main") == 0
    land(root, 2)
    assert documents.landed_since_pass(root, "main") == 2
    assert route.decide(root, None).command == route.SPEC


def test_a_commit_whose_body_names_a_pass_does_not_mark_one(tmp_path: Path) -> None:
    root = stale_world(tmp_path)
    before = documents.landed_since_pass(root, "main")
    commit(root, "feat: a thing\n\ndocs(prune): cut")
    assert before is not None
    assert documents.landed_since_pass(root, "main") == before + 1
    assert route.decide(root, None).command == "/ratchet:prune pages"


def test_a_pass_recorded_where_nothing_ignores_the_state_leaves_the_tree_clean(
    tmp_path: Path,
) -> None:
    root = stale_world(tmp_path)
    (root / ".gitignore").write_text("", encoding="utf-8")
    git(root, "commit", "-q", "-am", "chore: no ignores")
    assert documents.main(["documents.py", "passed", "--root", str(root)]) == 0
    assert git(root, "status", "--porcelain") == ""


def test_a_pass_that_cut_nothing_is_recorded_and_restarts_the_count(tmp_path: Path) -> None:
    root = stale_world(tmp_path)
    assert route.decide(root, None).command == "/ratchet:prune pages"
    assert documents.main(["documents.py", "passed", "--root", str(root)]) == 0
    assert documents.landed_since_pass(root, "main") == 0
    assert route.decide(root, None).command == route.SPEC
    land(root, 3)
    assert route.decide(root, None).command == "/ratchet:prune pages"


def test_a_record_of_a_commit_the_history_lacks_is_ignored(tmp_path: Path) -> None:
    root = stale_world(tmp_path)
    write(root, ".ratchet/prune-pass", "0" * 40 + "\n")
    assert documents.landed_since_pass(root, "main") == int(
        git(root, "rev-list", "--first-parent", "--count", "main")
    )


def test_an_unreadable_default_branch_is_never_due(tmp_path: Path) -> None:
    root = stale_world(tmp_path)
    assert documents.landed_since_pass(root, "nowhere") is None
    assert documents.due(root, "nowhere") is None


def test_passed_with_no_commit_to_count_from_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shutil.copytree(skeleton(), tmp_path / ".git")
    assert documents.main(["documents.py", "passed", "--root", str(tmp_path)]) == 1
    assert capsys.readouterr().out == "passed: the default branch has no commit to count from\n"


def test_passed_names_the_commit_merges_are_counted_from(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = adopted(tmp_path)
    assert documents.main(["documents.py", "passed", "--root", str(root)]) == 0
    assert (
        capsys.readouterr().out
        == f"passed: merges are counted from {git(root, 'rev-parse', 'HEAD')[:12]}\n"
    )


def test_stale_lists_each_page_and_the_path_that_changed_after_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = stale_world(tmp_path)
    assert documents.main(["documents.py", "stale", "--root", str(root)]) == 0
    assert capsys.readouterr().out == "README.md\tsrc/a.py\n"


def test_reports_prints_the_prunable_lines_of_each_kind_marked_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    notes = f"{BINDING}\n## Documents\n\n| Kind | Paths | Prune |\n| --- | --- | --- |\n"
    notes += "| notes | `docs/notes/*.md` | `pass` |\n| keepsake | `docs/keep/*.md` | `never` |\n"
    root = repository(
        tmp_path,
        {
            **ADOPTED,
            "docs/process/binding.md": notes,
            "docs/design-brief.md": (
                "# Brief\n\n## Purpose\n\nx\n\n## Slice one\n\ny\n\n## Other\n\nz\n"
            ),
            "README.md": "# A\n\nOne.\n\nTwo.\n",
            "docs/notes/n.md": "# N\n\nfirst\nsecond\n",
            "docs/keep/k.md": "# K\n\nnever counted\n",
        },
    )
    assert documents.main(["documents.py", "prunable", "--root", str(root)]) == 0
    assert capsys.readouterr().out == LINE.format(kinds="brief 3, readme 3, notes 3") + "\n"


def test_a_binding_without_documents_gives_the_catalogues_kinds_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = repository(tmp_path, {"README.md": "# A\n\nOne.\n"})
    assert documents.main(["documents.py", "prunable", "--root", str(root)]) == 0
    assert capsys.readouterr().out == LINE.format(kinds="brief 0, readme 2") + "\n"


def test_the_count_is_not_a_gate(tmp_path: Path) -> None:
    root = repository(tmp_path, {"README.md": "# A\n" + "line\n" * 500})
    assert documents.main(["documents.py", "prunable", "--root", str(root)]) == 0


@pytest.mark.parametrize("value", ["", "0", "ten", "-3"])
def test_a_prune_after_that_is_no_count_is_a_problem_of_the_binding(value: str) -> None:
    text = f"{BINDING}\n## Documents\n\n- Prune after: {value}\n"
    found = documents.declared(text)
    assert found is not None
    assert found.problems[-1] == "`- Prune after:` is not a whole number of merges, one or more"
    assert documents.prune_after(text) == 10


def test_a_prune_after_that_is_a_count_is_read() -> None:
    text = f"{BINDING}\n## Documents\n\n- Prune after: 4\n"
    found = documents.declared(text)
    assert found is not None
    assert not [problem for problem in found.problems if "Prune after" in problem]
    assert (documents.prune_after(text), documents.prune_after(None)) == (4, 10)


def test_cited_is_red_for_a_path_the_tree_lacks_and_a_symbol_the_file_lacks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = adopted(tmp_path)
    write(root, "src/words.py", "def runner():\n    return 1\n")
    write(
        root,
        ".scratch/change.md",
        "- README.md: the loop: `src/a.py:run`\n- gone: `src/gone.py`\n"
        "- near: `src/words.py:run`\n- dir: `src`/x `src/` `README.md:run`\n"
        "- link: [a](../src/a.py) [b](../src/gone.md) [c](https://x.y/z) [d](#top)"
        " [e](mailto:a@b.c)\n"
        "- up: [f](../../out.md) [g](.) [h](/abs.md)\n",
    )
    assert documents.main(["documents.py", "cited", "--root", str(root), ".scratch/change.md"]) == 1
    assert capsys.readouterr().out.splitlines() == [
        "cited: .scratch/change.md: `src/gone.py` is not in the tree",
        "cited: .scratch/change.md: `src/words.py` does not hold `run` as a whole word",
        "cited: .scratch/change.md: `src/gone.md` is not in the tree",
    ]


def test_cited_is_green_when_every_citation_holds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = adopted(tmp_path)
    write(root, "docs/dir/x.md", "# X\n")
    write(root, ".scratch/change.md", "- `src/a.py:run` `docs/dir` [x](../docs/dir/x.md)\n")
    assert documents.main(["documents.py", "cited", "--root", str(root), ".scratch/change.md"]) == 0
    assert capsys.readouterr().out == ""


def test_cited_reads_a_page_whose_links_resolve_from_its_own_folder(tmp_path: Path) -> None:
    root = adopted(
        tmp_path, **{"docs/guide/a.md": "# A\n\n[b](b.md) [c](../c.md) [d](../../src/a.py)\n"}
    )
    write(root, "docs/guide/b.md", "# B\n")
    assert documents.cited(root, ["docs/guide/a.md"]) == [
        "cited: docs/guide/a.md: `docs/c.md` is not in the tree"
    ]


def test_cited_names_a_symbol_as_a_whole_word_alone(tmp_path: Path) -> None:
    root = adopted(tmp_path)
    write(root, "src/w.py", "run_all = 1\nxrun = 2\nthe.run(3)\n")
    assert documents.cited(root, ["README.md"]) == []
    write(root, ".scratch/d.md", "`src/w.py:run` `src/w.py:run_all` `src/w.py:xrun`\n")
    assert documents.cited(root, [".scratch/d.md"]) == []
    write(root, ".scratch/d.md", "`src/w.py:ru` `src/w.py:un`\n")
    assert len(documents.cited(root, [".scratch/d.md"])) == 2


def test_cited_asks_for_the_files_it_reads(tmp_path: Path) -> None:
    root = adopted(tmp_path)
    with pytest.raises(SystemExit) as stopped:
        documents.main(["documents.py", "cited", "--root", str(root)])
    assert stopped.value.code == 2


def test_the_comments_listing_is_the_owed_entries_by_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = adopted(tmp_path, **{"quality/owed.toml": COMMENTS + "\n" + ARCHITECTURE})
    assert owed.main(["owed.py", "--comments", "--root", str(root)]) == 0
    assert capsys.readouterr().out == "src/a.py\tnarrates the loop\nsrc/b.py\tclaims an order\n"


RECORD = "docs/features/prune-pages/verdict.json"
PRUNE_RECORD = {**VERDICT, "slug": "prune-pages"}


def test_a_prune_change_with_its_review_verdict_is_the_agents_to_merge(tmp_path: Path) -> None:
    repository(tmp_path, {**BASE, "README.md": PAGE})
    git(tmp_path, "switch", "-q", "-c", "chore/prune-pages-1")
    write(tmp_path, "README.md", "# A\n")
    commit(tmp_path, "docs(prune): the readme")
    stamp(tmp_path, PRUNE_RECORD)
    assert (tmp_path / RECORD).is_file()
    assert judged(tmp_path) == []


def test_a_prune_change_without_the_record_is_the_owners(tmp_path: Path) -> None:
    repository(tmp_path, {**BASE, "README.md": PAGE})
    git(tmp_path, "switch", "-q", "-c", "chore/prune-pages-1")
    write(tmp_path, "README.md", "# A\n")
    commit(tmp_path, "docs(prune): the readme")
    assert [reason.code for reason in merge_ready.judge(tmp_path, "main", "HEAD")] == ["record"]
