"""The publish check: red when a role committed, pushed or opened a pull request during the run."""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Protocol

import pytest

import feature_preflight as preflight
import publish_check
from tests.repos import branch, commit, git, repository, write
from tests.test_feature_preflight import gh_returning

Out = pytest.CaptureFixture[str]
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


def started(tmp_path: Path, root: Path, prs: str = NONE_OPEN) -> Path:
    """The start state, read as the check reads the present, in the preflight's whole report."""
    start = publish_check.start_of(root, BRANCH, gh_returning(prs))
    path = tmp_path / "start.json"
    path.write_text(json.dumps({"ok": True, "branch": BRANCH, "start": asdict(start)}), "utf-8")
    return path


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
            " commits on top: a role committed"
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
