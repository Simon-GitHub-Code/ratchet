"""The publish check: red when a role committed, pushed or opened a pull request during the run."""

import hashlib
import json
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Protocol

import pytest

import feature_preflight as preflight
import publish_check
from tests.repos import branch, commit, git, repository, write
from tests.test_feature_preflight import gh_returning

Out = pytest.CaptureFixture[str]
COPY = ": copy the whole sentence, list item, cell or line as the record writes it"

BRANCH = "feature/4-publish"
NONE_OPEN = "[]"


def checkout(tmp_path: Path, *, forge: bool = False) -> Path:
    """A repository on the ticket's branch, with a bare repository as its forge when asked."""
    root = repository(tmp_path / "work")
    branch(root, BRANCH)
    commit(root, "feat(core): the branch's first commit")
    if forge:
        git(tmp_path, "init", "-q", "--bare", "remote.git")
        git(root, "remote", "add", "origin", str(tmp_path / "remote.git"))
    return root


def started(
    tmp_path: Path, root: Path, prs: str = NONE_OPEN, *, rebase_onto: str | None = None
) -> Path:
    """The start state, read as the check reads the present, in the preflight's whole report."""
    start = publish_check.start_of(root, BRANCH, gh_returning(prs), rebase_onto=rebase_onto)
    path = tmp_path / "start.json"
    path.write_text(json.dumps({"ok": True, "branch": BRANCH, "start": asdict(start)}), "utf-8")
    resulted(path, {})
    return path


def resulted(start: Path, fields: dict[str, object]) -> None:
    """The run's result beside the start, with no findings unless ``fields`` say otherwise."""
    result = {
        "criticFindingsByRound": [],
        "reviewIssuesByRound": [],
        "unresolvedBlockingFindings": [],
        "reviewIssues": [],
    }
    publish_check.result_path(start).write_text(json.dumps(result | fields), "utf-8")


class Check(Protocol):
    def __call__(self, path: Path, root: Path, prs: str | None = ...) -> tuple[int, list[str]]: ...


@pytest.fixture
def checked(capsys: Out, monkeypatch: pytest.MonkeyPatch) -> Check:
    """The check run as the orchestrator runs it, the forge answering ``prs``: exit and lines."""

    def run(path: Path, root: Path, prs: str | None = NONE_OPEN) -> tuple[int, list[str]]:
        monkeypatch.setattr(preflight, "run_gh", gh_returning(prs))
        code = publish_check.main(["publish_check.py", str(path), "--root", str(root)])
        return code, capsys.readouterr().out.splitlines()

    return run


def refs(root: Path) -> str:
    return git(root, "for-each-ref", "--format=%(refname) %(objectname)")


ORCHESTRATOR = ", or the orchestrator committed before this check"
GREEN = "publish check: green, nothing was committed, pushed or opened since the run started"
RED = (
    "publish check: red, a role published: the ticket ends as a role that published, never as an"
    " approval"
)


def test_nothing_moved_is_green_and_a_second_run_says_the_same(
    tmp_path: Path, checked: Check
) -> None:
    root = checkout(tmp_path)
    path = started(tmp_path, root)
    before = refs(root)
    assert checked(path, root) == (0, [GREEN])
    assert checked(path, root) == (0, [GREEN])
    assert refs(root) == before, "the check moves no ref"


def test_with_no_forge_the_start_says_so_and_no_forge_is_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = checkout(tmp_path)
    path = started(tmp_path, root)
    assert json.loads(path.read_text("utf-8"))["start"]["forge"] is None
    forge = gh_returning(None)
    monkeypatch.setattr(preflight, "run_gh", forge)
    assert publish_check.main(["publish_check.py", str(path), "--root", str(root)]) == 0
    assert forge.calls == []


def test_uncommitted_staged_and_untracked_changes_are_not_a_move(
    tmp_path: Path, checked: Check
) -> None:
    root = checkout(tmp_path)
    path = started(tmp_path, root)
    write(root, "README.md", "# Changed by a role\n")
    write(root, "src/new.txt", "staged\n")
    git(root, "add", "src/new.txt")
    write(root, "notes.txt", "untracked\n")
    assert checked(path, root) == (0, [GREEN])


def test_a_start_from_a_drain_note_names_the_final_check_as_the_step(tmp_path: Path) -> None:
    root = checkout(tmp_path)
    start = publish_check.read_start(started(tmp_path, root))
    commit(root, "feat(core): a role committed")
    moves, _, _ = publish_check.check(root, start, preflight.run_gh, drained=True)
    assert moves[0].endswith(
        "a role committed, or the orchestrator committed before `drain.py check <ticket> --final`"
    )


def test_a_commit_on_the_branch_is_a_role_that_published(tmp_path: Path, checked: Check) -> None:
    root = checkout(tmp_path)
    path = started(tmp_path, root)
    old = git(root, "rev-parse", "HEAD")
    new = commit(root, "feat(core): a role committed")
    code, lines = checked(path, root)
    assert code == 1
    assert lines == [
        (
            f"publish check: `{BRANCH}` moved from {old[:12]} to {new[:12]}, which holds it with"
            " commits on top: a role committed" + ORCHESTRATOR
        ),
        RED,
    ]


def test_an_amended_head_is_a_role_that_published(tmp_path: Path, checked: Check) -> None:
    root = checkout(tmp_path)
    path = started(tmp_path, root)
    old = git(root, "rev-parse", "HEAD")
    git(root, "commit", "-q", "--amend", "--allow-empty", "-m", "feat(core): amended by a role")
    new = git(root, "rev-parse", "HEAD")
    code, lines = checked(path, root)
    assert code == 1
    assert lines[0] == (
        f"publish check: `{BRANCH}` is at {new[:12]}, which does not hold {old[:12]}, where it"
        " started: a role amended, reset or rebased it"
    )
    git(root, "reset", "-q", "--hard", "HEAD~1")
    code, lines = checked(path, root)
    assert code == 1
    assert "a role amended, reset or rebased it" in lines[0], "a branch reset back is moved too"


def test_head_off_the_branch_is_a_move_however_the_branch_stands(
    tmp_path: Path, checked: Check
) -> None:
    root = checkout(tmp_path)
    path = started(tmp_path, root)
    git(root, "switch", "-q", "main")
    code, lines = checked(path, root)
    assert (code, lines[:1]) == (
        1,
        [f"publish check: HEAD is on `main`, not on `{BRANCH}`, the branch the run started on"],
    )
    git(root, "switch", "-q", "--detach", BRANCH)
    code, lines = checked(path, root)
    assert (code, lines) == (
        1,
        [f"publish check: HEAD is detached, not on `{BRANCH}`, the branch the run started on", RED],
    )


def test_a_branch_that_is_gone_is_a_move(tmp_path: Path, checked: Check) -> None:
    root = checkout(tmp_path)
    path = started(tmp_path, root)
    old = git(root, "rev-parse", "HEAD")
    git(root, "switch", "-q", "main")
    git(root, "branch", "-q", "-D", BRANCH)
    code, lines = checked(path, root)
    assert code == 1
    assert lines[1] == (
        f"publish check: `{BRANCH}` is gone; it was at {old[:12]} when the run started"
    )


def test_a_tag_or_another_branch_a_role_made_is_not_seen(tmp_path: Path, checked: Check) -> None:
    # Why: the start state names one branch; the module docstring states this limit.
    root = checkout(tmp_path)
    path = started(tmp_path, root)
    git(root, "tag", "v1")
    git(root, "branch", "elsewhere")
    assert checked(path, root) == (0, [GREEN])


def test_with_a_forge_nothing_moved_is_green_and_asks_once_for_the_pull_requests(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = checkout(tmp_path, forge=True)
    path = started(tmp_path, root)
    assert json.loads(path.read_text("utf-8"))["start"]["forge"] == {
        "remote": None,
        "pull_requests": [],
    }
    forge = gh_returning(NONE_OPEN)
    monkeypatch.setattr(preflight, "run_gh", forge)
    assert publish_check.main(["publish_check.py", str(path), "--root", str(root)]) == 0
    assert forge.calls == [["pr", "list", "--head", BRANCH, "--state", "open", "--json", "number"]]
    assert capsys.readouterr().out.splitlines() == [GREEN]


def test_a_branch_pushed_during_the_run_is_a_move(tmp_path: Path, checked: Check) -> None:
    root = checkout(tmp_path, forge=True)
    path = started(tmp_path, root)
    git(root, "push", "-q", "origin", BRANCH)
    head = git(root, "rev-parse", "HEAD")
    code, lines = checked(path, root)
    assert (code, lines) == (
        1,
        [
            f"publish check: the forge gained the branch `{BRANCH}` at {head[:12]}: a role pushed",
            RED,
        ],
    )


def test_a_forge_branch_that_moved_or_went_is_a_move(tmp_path: Path, checked: Check) -> None:
    root = checkout(tmp_path, forge=True)
    git(root, "push", "-q", "origin", BRANCH)
    path = started(tmp_path, root)
    old = git(root, "rev-parse", "HEAD")
    main = git(root, "rev-parse", "main")
    git(root, "push", "-q", "--force", "origin", f"main:refs/heads/{BRANCH}")
    code, lines = checked(path, root)
    assert (code, lines) == (
        1,
        [
            (
                f"publish check: the forge's branch `{BRANCH}` moved from {old[:12]} to"
                f" {main[:12]}: a role pushed"
            ),
            RED,
        ],
    )
    git(root, "push", "-q", "origin", f":refs/heads/{BRANCH}")
    code, lines = checked(path, root)
    assert (code, lines[0]) == (
        1,
        (
            f"publish check: the forge's branch `{BRANCH}` is gone; it was at {old[:12]}:"
            " a role deleted it"
        ),
    )


def test_a_pull_request_that_opened_or_closed_is_a_move(tmp_path: Path, checked: Check) -> None:
    root = checkout(tmp_path, forge=True)
    git(root, "push", "-q", "origin", BRANCH)
    path = started(tmp_path, root)
    code, lines = checked(path, root, prs='[{"number": 12}]')
    assert (code, lines) == (
        1,
        [
            (
                f"publish check: pull request #12 is open for `{BRANCH}` and was not when the run"
                " started: a role opened it"
            ),
            RED,
        ],
    )
    path = started(tmp_path, root, prs='[{"number": 7}]')
    assert checked(path, root, prs='[{"number": 7}]') == (0, [GREEN])
    code, lines = checked(path, root)
    assert (code, lines[0]) == (
        1,
        (
            f"publish check: pull request #7 for `{BRANCH}` was open when the run started and is"
            " not now: a role closed or merged it"
        ),
    )


def test_a_forge_that_cannot_be_reached_is_never_a_pass(tmp_path: Path, checked: Check) -> None:
    root = checkout(tmp_path, forge=True)
    path = started(tmp_path, root)
    unreadable = (
        "publish check: the present state cannot be read: `gh` did not list the open pull"
        f" requests for `{BRANCH}`; that is never a pass"
    )
    for answer in (None, "not json", '{"number": 1}', '[{"title": "x"}]', '[{"number": "1"}]'):
        assert checked(path, root, prs=answer) == (3, [unreadable]), answer
    git(root, "remote", "set-url", "origin", str(tmp_path / "nowhere.git"))
    code, lines = checked(path, root)
    assert (code, lines) == (
        3,
        [
            (
                "publish check: the present state cannot be read: `git ls-remote origin` failed, so"
                f" the forge's branch `{BRANCH}` cannot be read; that is never a pass"
            )
        ],
    )


def test_a_move_seen_locally_decides_even_when_the_forge_cannot_be_read(
    tmp_path: Path, checked: Check
) -> None:
    root = checkout(tmp_path, forge=True)
    path = started(tmp_path, root)
    commit(root, "feat(core): a role committed")
    code, lines = checked(path, root, prs=None)
    assert code == 1
    assert lines[1:] == [
        (
            f"publish check: the forge cannot be read either: `gh` did not list the open pull"
            f" requests for `{BRANCH}`"
        ),
        RED,
    ]


def test_a_directory_that_is_not_a_checkout_is_never_a_pass(tmp_path: Path, checked: Check) -> None:
    path = started(tmp_path, checkout(tmp_path))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    code, lines = checked(path, elsewhere)
    assert (code, lines) == (
        3,
        [
            (
                f"publish check: the present state cannot be read: {elsewhere} is not a git"
                " checkout with a commit; that is never a pass"
            )
        ],
    )


def test_the_start_cannot_be_read_from_a_branch_that_does_not_exist(tmp_path: Path) -> None:
    root = checkout(tmp_path)
    with pytest.raises(publish_check.UnreadableError, match="there is no branch `nope`"):
        publish_check.start_of(root, "nope", gh_returning(NONE_OPEN))


HEAD = "a" * 40
START = {"branch": BRANCH, "head": HEAD, "forge": {"remote": None, "pull_requests": []}}


@pytest.mark.parametrize(
    "case",
    [
        ("{", "it is not JSON"),
        ("[]", "it is not a JSON object with a `start` object"),
        ("{}", "it is not a JSON object with a `start` object"),
        ('{"start": []}', "it is not a JSON object with a `start` object"),
        (json.dumps({"start": START | {"branch": ""}}), "`start.branch` is not a branch's name"),
        (json.dumps({"start": START | {"branch": 4}}), "`start.branch` is not a branch's name"),
        (json.dumps({"start": {"head": HEAD, "forge": None}}), "`start.branch` is missing"),
        (json.dumps({"start": {"branch": BRANCH, "forge": None}}), "`start.head` is missing"),
        (json.dumps({"start": START | {"head": None}}), "`start.head` is not a commit"),
        (json.dumps({"start": START | {"head": "HEAD"}}), "`start.head` is not a commit"),
        (json.dumps({"start": START | {"head": "A" * 40}}), "`start.head` is not a commit"),
        (json.dumps({"start": START | {"head": "a" * 41}}), "`start.head` is not a commit"),
        (json.dumps({"start": {"branch": BRANCH, "head": HEAD}}), "`start.forge` is missing"),
        (json.dumps({"start": START | {"forge": True}}), "`start.forge` is neither null nor"),
        (
            json.dumps({"start": START | {"forge": {"pull_requests": []}}}),
            "`start.forge.remote` is missing",
        ),
        (
            json.dumps({"start": START | {"forge": {"remote": "x", "pull_requests": []}}}),
            "`start.forge.remote` is not a commit",
        ),
        (
            json.dumps({"start": START | {"forge": {"remote": None}}}),
            "`start.forge.pull_requests` is missing",
        ),
        *(
            (
                json.dumps({"start": START | {"forge": {"remote": None, "pull_requests": prs}}}),
                "`start.forge.pull_requests` is not a list of pull request numbers",
            )
            for prs in (None, 12, [True], [0], ["12"])
        ),
    ],
)
def test_a_start_state_missing_a_member_or_malformed_is_refused(
    tmp_path: Path, checked: Check, case: tuple[str, str]
) -> None:
    text, why = case
    path = tmp_path / "start.json"
    path.write_text(text, "utf-8")
    code, lines = checked(path, tmp_path)
    assert code == 2
    assert lines[0].startswith(f"publish check: the start state in {path} cannot be read: {why}")
    assert lines[0].endswith("; that is never a pass")


def test_a_start_file_that_is_missing_is_refused(tmp_path: Path, checked: Check) -> None:
    path = tmp_path / "absent.json"
    assert checked(path, tmp_path) == (
        2,
        [
            (
                f"publish check: the start state in {path} cannot be read: there is no such file,"
                " or it is not text; that is never a pass"
            )
        ],
    )


def test_a_start_with_a_forge_and_its_open_pull_requests_is_read_whole() -> None:
    other = "b" * 64
    text = json.dumps(
        {
            "start": {
                "branch": BRANCH,
                "head": HEAD,
                "forge": {"remote": other, "pull_requests": [9, 3, 9]},
            }
        }
    )
    assert publish_check.parse(text) == publish_check.Start(
        BRANCH, HEAD, publish_check.Forge(other, (3, 9))
    )


WHEN = "2026-11-01T09:00:00+02:00"
LATER = "2026-11-02T09:00:00+02:00"
README_EDIT = "feat(core): the branch edits the readme\n\nWith a body the rebase keeps."
HALTED = (
    "publish check: a rebase is under way in the checkout and was not finished: a role stopped"
    " halfway, which is not publishing, and a branch half rebased cannot be judged; that is never"
    " a pass"
)


def rejudged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, forge: bool = False) -> Path:
    """A ticket's branch whose rebase onto `main` conflicts, left as the drain leaves it."""
    monkeypatch.setenv("GIT_AUTHOR_DATE", WHEN)
    monkeypatch.setenv("GIT_EDITOR", "true")
    root = checkout(tmp_path, forge=forge)
    write(root, "README.md", "# From the branch\n")
    commit(root, README_EDIT)
    write(root, "notes.txt", "notes\n")
    commit(root, "docs(core): the branch adds notes")
    git(root, "switch", "-q", "main")
    write(root, "README.md", "# From main\n")
    commit(root, "feat(core): main edits the readme")
    git(root, "switch", "-q", BRANCH)
    return root


def stopped(root: Path) -> None:
    """Start the rebase onto `main`: it stops on the readme both sides edited."""
    with pytest.raises(subprocess.CalledProcessError):
        git(root, "rebase", "main")


def finished(root: Path) -> None:
    """Finish the rebase as the Reconcile role is told to: resolve, add, continue."""
    stopped(root)
    write(root, "README.md", "# From main and the branch\n")
    git(root, "add", "README.md")
    git(root, "rebase", "--continue")


def not_matched(root: Path, old: str, *, then: int, now: int) -> list[str]:
    return [
        (
            f"publish check: `{BRANCH}` is at {git(root, 'rev-parse', 'HEAD')[:12]}, which does"
            f" not hold {old[:12]}, where it started, and the commits ahead of `main` are not the"
            f" ones it had, by author date and message in the same order ({then} then, {now} now):"
            " a role amended, reset or rebased it"
        ),
        RED,
    ]


def not_held(root: Path, old: str, *, dropped: int) -> list[str]:
    return [
        (
            f"publish check: `{BRANCH}` is at {git(root, 'rev-parse', 'HEAD')[:12]}, which does"
            f" not hold {old[:12]}, where it started, and {dropped} of the commits it had ahead"
            " of `main` were dropped, which `main` does not hold by author date and message: a"
            " role amended, reset or rebased it"
        ),
        RED,
    ]


def landed_on_main(
    root: Path, monkeypatch: pytest.MonkeyPatch, *, message: str | None = None, date: str = WHEN
) -> None:
    """Main takes the branch's notes change as its own commit, as a merge that landed it did."""
    notes = git(root, "rev-parse", "HEAD")
    git(root, "switch", "-q", "main")
    monkeypatch.setenv("GIT_AUTHOR_DATE", date)
    git(root, "cherry-pick", "-n", notes)
    git(root, "commit", "-q", "-m", message or "docs(core): the branch adds notes")
    monkeypatch.setenv("GIT_AUTHOR_DATE", WHEN)
    git(root, "switch", "-q", BRANCH)


def test_a_reconcile_that_finishes_the_rebase_is_not_publishing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, checked: Check
) -> None:
    root = rejudged(tmp_path, monkeypatch)
    path = started(tmp_path, root, rebase_onto="main")
    old = git(root, "rev-parse", "HEAD")
    readme = hashlib.sha256(f"{README_EDIT}\n".encode()).hexdigest()
    rebase = json.loads(path.read_text("utf-8"))["start"]["rebase"]
    assert rebase["base"] == "main"
    assert [entry["author_date"] for entry in rebase["commits"]] == [WHEN] * 3
    assert rebase["commits"][1]["message_sha256"] == readme
    finished(root)
    assert git(root, "merge-base", "--is-ancestor", "main", BRANCH) == ""
    assert git(root, "rev-parse", "HEAD") != old
    assert checked(path, root) == (0, [GREEN])


def test_the_rebase_member_on_a_branch_that_did_not_move_is_green(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, checked: Check
) -> None:
    root = rejudged(tmp_path, monkeypatch)
    path = started(tmp_path, root, rebase_onto="main")
    assert checked(path, root) == (0, [GREEN])


def test_a_rebase_that_skips_the_conflicted_commit_drops_it_and_is_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, checked: Check
) -> None:
    root = rejudged(tmp_path, monkeypatch)
    path = started(tmp_path, root, rebase_onto="main")
    old = git(root, "rev-parse", "HEAD")
    stopped(root)
    git(root, "rebase", "--skip")
    assert checked(path, root) == (1, not_held(root, old, dropped=1))


def test_a_rebase_that_drops_a_commit_the_base_already_holds_is_not_publishing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, checked: Check
) -> None:
    root = rejudged(tmp_path, monkeypatch)
    landed_on_main(root, monkeypatch)
    path = started(tmp_path, root, rebase_onto="main")
    finished(root)
    assert git(root, "rev-list", "--count", "main..HEAD") == "2"
    assert checked(path, root) == (0, [GREEN])


@pytest.mark.parametrize(
    ("message", "date"),
    [
        ("docs(core): the notes, reworded", WHEN),
        (None, LATER),
    ],
    ids=["another-message", "another-author-date"],
)
def test_a_dropped_commit_whose_twin_on_the_base_differs_is_red(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    checked: Check,
    *,
    message: str | None,
    date: str,
) -> None:
    root = rejudged(tmp_path, monkeypatch)
    landed_on_main(root, monkeypatch, message=message, date=date)
    path = started(tmp_path, root, rebase_onto="main")
    old = git(root, "rev-parse", "HEAD")
    finished(root)
    assert checked(path, root) == (1, not_held(root, old, dropped=1))


def test_each_dropped_commit_needs_a_twin_of_its_own_on_the_base() -> None:
    twin = publish_check.Commit(WHEN, "a" * 64)
    other = publish_check.Commit(LATER, "b" * 64)
    assert publish_check.unmatched([twin], (twin,)) == []
    assert publish_check.unmatched([twin, twin], (twin,)) == [twin]
    assert publish_check.unmatched([twin, twin], (other, twin, twin)) == []
    assert publish_check.unmatched([other], (twin,)) == [other]


def test_a_rebase_finished_with_a_commit_added_is_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, checked: Check
) -> None:
    root = rejudged(tmp_path, monkeypatch)
    path = started(tmp_path, root, rebase_onto="main")
    old = git(root, "rev-parse", "HEAD")
    finished(root)
    commit(root, "fix(core): the reconcile commits its fix")
    assert checked(path, root) == (1, not_matched(root, old, then=3, now=4))


def test_a_rebase_that_rewords_a_message_is_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, checked: Check
) -> None:
    root = rejudged(tmp_path, monkeypatch)
    path = started(tmp_path, root, rebase_onto="main")
    old = git(root, "rev-parse", "HEAD")
    finished(root)
    git(root, "commit", "-q", "--amend", "-m", "docs(core): the notes, reworded")
    assert checked(path, root) == (1, not_matched(root, old, then=3, now=3))


def test_a_rebase_that_reorders_the_commits_is_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, checked: Check
) -> None:
    root = rejudged(tmp_path, monkeypatch)
    path = started(tmp_path, root, rebase_onto="main")
    old = git(root, "rev-parse", "HEAD")
    finished(root)
    readme, notes = git(root, "rev-parse", "HEAD~1"), git(root, "rev-parse", "HEAD")
    git(root, "reset", "-q", "--hard", "HEAD~2")
    git(root, "cherry-pick", notes)
    git(root, "cherry-pick", readme)
    assert checked(path, root) == (1, not_matched(root, old, then=3, now=3))


def test_a_rebase_that_changes_an_author_date_is_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, checked: Check
) -> None:
    root = rejudged(tmp_path, monkeypatch)
    path = started(tmp_path, root, rebase_onto="main")
    old = git(root, "rev-parse", "HEAD")
    finished(root)
    monkeypatch.setenv("GIT_AUTHOR_DATE", LATER)
    git(root, "commit", "-q", "--amend", "--no-edit", "--reset-author")
    assert git(root, "log", "-1", "--format=%aI") == LATER
    assert checked(path, root) == (1, not_matched(root, old, then=3, now=3))


def test_a_rebase_onto_a_base_that_is_gone_is_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, checked: Check
) -> None:
    root = rejudged(tmp_path, monkeypatch)
    path = started(tmp_path, root, rebase_onto="main")
    old = git(root, "rev-parse", "HEAD")
    finished(root)
    git(root, "branch", "-q", "-D", "main")
    new = git(root, "rev-parse", "HEAD")
    assert checked(path, root) == (
        1,
        [
            (
                f"publish check: `{BRANCH}` is at {new[:12]}, which does not hold {old[:12]},"
                " where it started, and `main`, the base it was to be rebased onto, names no"
                " commit: a role amended, reset or rebased it"
            ),
            RED,
        ],
    )


def test_with_the_rebase_member_commits_on_top_of_the_start_are_still_a_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, checked: Check
) -> None:
    root = rejudged(tmp_path, monkeypatch)
    path = started(tmp_path, root, rebase_onto="main")
    old = git(root, "rev-parse", "HEAD")
    new = commit(root, "feat(core): a role committed")
    assert checked(path, root) == (
        1,
        [
            (
                f"publish check: `{BRANCH}` moved from {old[:12]} to {new[:12]}, which holds it"
                " with commits on top: a role committed" + ORCHESTRATOR
            ),
            RED,
        ],
    )


def test_a_rebase_left_unfinished_is_never_a_pass_and_is_not_called_publishing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, checked: Check
) -> None:
    root = rejudged(tmp_path, monkeypatch)
    path = started(tmp_path, root, rebase_onto="main")
    stopped(root)
    assert checked(path, root) == (3, [HALTED])


def test_a_rebase_left_unfinished_does_not_hide_a_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, checked: Check
) -> None:
    root = rejudged(tmp_path, monkeypatch, forge=True)
    path = started(tmp_path, root, rebase_onto="main")
    head = git(root, "rev-parse", "HEAD")
    git(root, "push", "-q", "origin", BRANCH)
    stopped(root)
    assert checked(path, root) == (
        1,
        [
            f"publish check: the forge gained the branch `{BRANCH}` at {head[:12]}: a role pushed",
            HALTED,
            RED,
        ],
    )


def test_without_the_rebase_member_a_rebase_left_unfinished_is_head_detached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, checked: Check
) -> None:
    root = rejudged(tmp_path, monkeypatch)
    path = started(tmp_path, root)
    stopped(root)
    assert checked(path, root) == (
        1,
        [f"publish check: HEAD is detached, not on `{BRANCH}`, the branch the run started on", RED],
    )


def test_a_finished_rebase_that_was_then_pushed_is_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, checked: Check
) -> None:
    root = rejudged(tmp_path, monkeypatch, forge=True)
    path = started(tmp_path, root, rebase_onto="main")
    finished(root)
    git(root, "push", "-q", "origin", BRANCH)
    head = git(root, "rev-parse", "HEAD")
    assert checked(path, root) == (
        1,
        [
            f"publish check: the forge gained the branch `{BRANCH}` at {head[:12]}: a role pushed",
            RED,
        ],
    )


def test_the_start_of_a_rebase_cannot_be_read_onto_a_base_that_does_not_exist(
    tmp_path: Path,
) -> None:
    root = checkout(tmp_path)
    with pytest.raises(publish_check.UnreadableError, match="there is no base `nope`"):
        publish_check.start_of(root, BRANCH, gh_returning(NONE_OPEN), rebase_onto="nope")


DIGEST = "c" * 64
AHEAD = {"author_date": WHEN, "message_sha256": DIGEST}


@pytest.mark.parametrize(
    "case",
    [
        ({"rebase": True}, "`start.rebase` is neither null nor an object"),
        ({"rebase": {"commits": []}}, "`start.rebase.base` is missing"),
        ({"rebase": {"base": "", "commits": []}}, "`start.rebase.base` is not a ref's name"),
        ({"rebase": {"base": ["main"], "commits": []}}, "`start.rebase.base` is not a ref's name"),
        ({"rebase": {"base": "main"}}, "`start.rebase.commits` is missing"),
        ({"rebase": {"base": "main", "commits": {}}}, "`start.rebase.commits` is not a list"),
        *(
            (
                {"rebase": {"base": "main", "commits": [AHEAD, entry]}},
                (
                    "`start.rebase.commits[1]` is not an object with an ISO 8601 `author_date`"
                    " and a SHA-256 `message_sha256`"
                ),
            )
            for entry in (
                DIGEST,
                {"message_sha256": DIGEST},
                {"author_date": WHEN},
                AHEAD | {"author_date": "yesterday"},
                AHEAD | {"author_date": 1762000000},
                AHEAD | {"message_sha256": "C" * 64},
                AHEAD | {"message_sha256": "c" * 40},
            )
        ),
    ],
)
def test_a_rebase_member_missing_a_part_or_malformed_is_refused(
    tmp_path: Path, checked: Check, case: tuple[dict[str, object], str]
) -> None:
    member, why = case
    path = tmp_path / "start.json"
    path.write_text(json.dumps({"start": START | member}), "utf-8")
    code, lines = checked(path, tmp_path)
    assert code == 2
    assert lines == [
        f"publish check: the start state in {path} cannot be read: {why}; that is never a pass"
    ]


def test_a_start_with_a_rebase_is_read_whole_and_a_null_one_is_none() -> None:
    later = {"author_date": "2026-11-15T10:13:20Z", "message_sha256": "d" * 64}
    rebase = {"base": "origin/main", "commits": [AHEAD, later]}
    assert publish_check.parse(json.dumps({"start": START | {"rebase": rebase}})).rebase == (
        publish_check.Rebase(
            "origin/main",
            (
                publish_check.Commit(WHEN, DIGEST),
                publish_check.Commit("2026-11-15T10:13:20Z", "d" * 64),
            ),
        )
    )
    assert publish_check.parse(json.dumps({"start": START | {"rebase": None}})).rebase is None


QUOTE = "A blocker or a major quotes what it breaks."
QUOTES_RED = (
    "publish check: red, a finding the run treated as owed quotes what its record does not hold:"
    " the ticket is not approved"
)
NOTHING = (
    "publish check: nothing may be published on this check; it authorises a resume and nothing else"
)
NO_BREAKS = "it carries no `breaks` with a `record` and a `quote`, both text"


def breaking(record: str, quote: str, severity: str = "major", *, title: str = "a finding") -> dict:
    return {"severity": severity, "title": title, "breaks": {"record": record, "quote": quote}}


def unquoted(record: str, quote: str, *, title: str = "a finding", why: str | None = None) -> str:
    reason = why or f"the record `{record}` does not hold the quote" + COPY
    return f'publish check: the finding `{title}` quotes `{record}` with "{quote}" and {reason}'


def test_a_result_file_that_is_absent_is_refused(tmp_path: Path, checked: Check) -> None:
    root = checkout(tmp_path)
    path = started(tmp_path, root)
    publish_check.result_path(path).unlink()
    code, lines = checked(path, root)
    assert code == 2
    assert lines == [
        (
            f"publish check: the run's result in {path}.result.json cannot be read: there is no"
            " such file, or it is not text; that is never a pass"
        )
    ]


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("not json", "it is not JSON"),
        ("[]", "it is not a JSON object"),
        (
            '{"criticFindingsByRound": [], "reviewIssuesByRound": [], "reviewIssues": []}',
            "`unresolvedBlockingFindings` is",
        ),
        (
            '{"criticFindingsByRound": [], "unresolvedBlockingFindings": [], "reviewIssues": []}',
            "`reviewIssuesByRound` is missing or is not a list",
        ),
        (
            (
                '{"criticFindingsByRound": {}, "reviewIssuesByRound": [],'
                ' "unresolvedBlockingFindings": [], "reviewIssues": []}'
            ),
            "`criticFindingsByRound` is missing or is not a list",
        ),
    ],
)
def test_a_result_that_is_not_a_result_is_refused(
    tmp_path: Path, checked: Check, *, text: str, why: str
) -> None:
    root = checkout(tmp_path)
    path = started(tmp_path, root)
    publish_check.result_path(path).write_text(text, "utf-8")
    code, lines = checked(path, root)
    assert code == 2
    assert lines[0].startswith(f"publish check: the run's result in {path}.result.json cannot be")
    assert why in lines[0]
    assert lines[0].endswith("; that is never a pass")
    assert len(lines) == 1


def test_a_run_that_errored_has_no_result_and_is_checked_for_moves_alone(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = checkout(tmp_path)
    path = started(tmp_path, root)
    publish_check.result_path(path).unlink()
    monkeypatch.setattr(preflight, "run_gh", gh_returning(NONE_OPEN))
    argv = ["publish_check.py", str(path), "--root", str(root), "--before-resume"]
    assert publish_check.main(argv) == 0
    assert capsys.readouterr().out.splitlines() == [GREEN, NOTHING]
    commit(root, "feat(core): a role committed")
    assert publish_check.main(argv) == 1
    assert capsys.readouterr().out.splitlines()[-2:] == [RED, NOTHING]


def test_before_resume_with_a_result_file_present_is_refused(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = checkout(tmp_path)
    path = started(tmp_path, root)
    resulted(path, {"reviewIssues": [breaking("AGENTS.md", "invented")]})
    monkeypatch.setattr(preflight, "run_gh", gh_returning(NONE_OPEN))
    argv = ["publish_check.py", str(path), "--root", str(root), "--before-resume"]
    assert publish_check.main(argv) == 2
    assert capsys.readouterr().out.splitlines() == [
        (
            f"publish check: `--before-resume` was given, but the run's result is in"
            f" {path}.result.json; a run that returned is checked without it"
        )
    ]


def test_the_result_is_beside_the_start_file() -> None:
    assert publish_check.result_path(Path("/x/report.json")) == Path("/x/report.json.result.json")


def test_an_invented_quote_on_a_fixed_major_is_red(tmp_path: Path, checked: Check) -> None:
    root = checkout(tmp_path)
    write(root, "AGENTS.md", f"{QUOTE}\n")
    path = started(tmp_path, root)
    invented = breaking("AGENTS.md", "Every finding is fixed.")
    resulted(path, {"criticFindingsByRound": [{"round": 1, "findings": [invented]}]})
    assert checked(path, root) == (
        1,
        [unquoted("AGENTS.md", "Every finding is fixed."), QUOTES_RED],
    )


def test_a_found_quote_is_green(tmp_path: Path, checked: Check) -> None:
    root = checkout(tmp_path)
    write(root, "AGENTS.md", f"{QUOTE}\n")
    path = started(tmp_path, root)
    held = breaking("AGENTS.md", QUOTE, "blocker")
    resulted(
        path,
        {
            "criticFindingsByRound": [{"round": 1, "findings": [held]}],
            "unresolvedBlockingFindings": [held],
            "reviewIssues": [breaking("AGENTS.md", QUOTE)],
        },
    )
    assert checked(path, root) == (0, [GREEN])


def test_a_minor_with_a_quote_its_record_does_not_hold_does_not_block(
    tmp_path: Path, checked: Check
) -> None:
    root = checkout(tmp_path)
    path = started(tmp_path, root)
    minor = breaking("AGENTS.md", "x", "minor")
    resulted(path, {"reviewIssues": [minor, {"severity": "minor", "title": "no breaks"}]})
    assert checked(path, root) == (0, [GREEN])


def test_a_blocker_or_major_with_no_breaks_is_red(tmp_path: Path, checked: Check) -> None:
    root = checkout(tmp_path)
    path = started(tmp_path, root)
    resulted(path, {"unresolvedBlockingFindings": [{"severity": "blocker", "title": "bare"}]})
    assert checked(path, root) == (
        1,
        [f'publish check: the finding `bare` quotes `` with "" and {NO_BREAKS}', QUOTES_RED],
    )


def test_a_finding_in_two_lists_is_said_once(tmp_path: Path, checked: Check) -> None:
    root = checkout(tmp_path)
    path = started(tmp_path, root)
    invented = breaking("AGENTS.md", "invented")
    resulted(
        path,
        {
            "criticFindingsByRound": [{"round": 1, "findings": [invented]}, {"round": 2}],
            "unresolvedBlockingFindings": [invented],
        },
    )
    assert checked(path, root) == (
        1,
        [unquoted("AGENTS.md", "invented", why="the record `AGENTS.md` is absent"), QUOTES_RED],
    )


def test_a_blocker_of_a_review_round_that_sent_a_fix_pass_is_checked(
    tmp_path: Path, checked: Check
) -> None:
    root = checkout(tmp_path)
    path = started(tmp_path, root)
    invented = breaking("AGENTS.md", "invented")
    resulted(path, {"reviewIssuesByRound": [{"round": 0, "findings": [invented]}]})
    assert checked(path, root) == (
        1,
        [unquoted("AGENTS.md", "invented", why="the record `AGENTS.md` is absent"), QUOTES_RED],
    )


def test_a_finding_that_is_not_an_object_is_red(tmp_path: Path, checked: Check) -> None:
    root = checkout(tmp_path)
    path = started(tmp_path, root)
    resulted(path, {"reviewIssues": ["a finding as text"]})
    assert checked(path, root) == (
        1,
        ['publish check: the finding `` quotes `` with "" and it is not an object', QUOTES_RED],
    )


def test_a_quote_of_the_spec_is_read_from_the_specs_path(tmp_path: Path, checked: Check) -> None:
    root = checkout(tmp_path)
    write(root, "docs/features/x/spec.md", "- [ ] It does the thing.\n")
    path = started(tmp_path, root)
    findings = [breaking("spec.md", "It does the thing."), breaking("spec.md", "It does more.")]
    resulted(path, {"reviewIssues": findings, "specPath": "docs/features/x/spec.md"})
    assert checked(path, root) == (1, [unquoted("spec.md", "It does more."), QUOTES_RED])


def test_a_quote_of_the_spec_with_no_spec_path_is_red(tmp_path: Path, checked: Check) -> None:
    root = checkout(tmp_path)
    path = started(tmp_path, root)
    resulted(path, {"reviewIssues": [breaking("spec.md", "It does the thing.")]})
    assert checked(path, root)[1][0] == unquoted(
        "spec.md", "It does the thing.", why="the record `spec.md` is absent"
    )


def test_a_quote_of_the_ticket_is_read_from_the_ticket_the_result_names(
    tmp_path: Path, checked: Check
) -> None:
    root = checkout(tmp_path)
    write(root, ".scratch/x/issues/01-x.md", "# T\n\n- [ ] The tool lists every file.\n")
    path = started(tmp_path, root)
    findings = [
        breaking("ticket", "The tool lists every file."),
        breaking("ticket", "The tool lists no file."),
    ]
    resulted(path, {"reviewIssues": findings, "ref": ".scratch/x/issues/01-x.md"})
    assert checked(path, root) == (1, [unquoted("ticket", "The tool lists no file."), QUOTES_RED])


@pytest.mark.parametrize("ref", [None, 4, "not-a-ticket"])
def test_a_quote_of_a_ticket_that_cannot_be_read_is_red(
    tmp_path: Path, checked: Check, ref: object
) -> None:
    root = checkout(tmp_path)
    path = started(tmp_path, root)
    resulted(path, {"reviewIssues": [breaking("ticket", "The tool lists.")], "ref": ref})
    assert checked(path, root)[1][0] == unquoted(
        "ticket", "The tool lists.", why="the record `ticket` is absent"
    )


def test_a_moved_branch_and_an_invented_quote_are_both_said(tmp_path: Path, checked: Check) -> None:
    root = checkout(tmp_path)
    path = started(tmp_path, root)
    resulted(path, {"reviewIssues": [breaking("DESIGN.md", "invented")]})
    commit(root, "feat(core): a role committed")
    code, lines = checked(path, root)
    assert code == 1
    assert lines[1] == RED
    assert lines[2:] == [
        unquoted("DESIGN.md", "invented", why="the record `DESIGN.md` is absent"),
        QUOTES_RED,
    ]


def test_help_prints_the_whole_description(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        publish_check.main(["publish_check.py", "--help"])
    shown = capsys.readouterr().out
    assert '{"branch": "feature/4-x"' in shown
