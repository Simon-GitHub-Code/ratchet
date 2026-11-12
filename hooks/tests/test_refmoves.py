"""The refs the guard reads the default branch through: names, spellings, state and detail."""

import tomllib
from pathlib import Path

import pytest

from ratchet_guards import interpreters, refmoves
from ratchet_guards.manifest import GIT_STATE, GIT_STATE_GROUP, GIT_STATE_PATTERNS, Scope
from ratchet_guards.options import expand_long
from ratchet_guards.refs import protects, pushed
from tests.support import ROOT, UNGUARDED, FakeFilesystem, FakeRepo, details, make_guard, rules

TEMPLATE = Path(__file__).resolve().parents[2] / "skills/adopt/templates/guards/manifest.toml"
LONG = frozenset({"--delete", "--force", "--force-create", "--create", "--remotes"})


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("main", True),
        ("master", True),
        ("+main", True),
        ("heads/main", True),
        ("refs/heads/main", True),
        ("refs/heads/master", True),
        ("refs/remotes/origin/main", True),
        ("refs/remotes/origin/HEAD", True),
        ("refs/remotes/upstream/feature", True),
        ("remotes/origin/main", True),
        ("+remotes/origin/main", True),
        ("remotes/upstream/x", True),
        ("refs/replace/0123abcd", True),
        ("refs/tags/main", True),
        ("refs/tags/master", True),
        ("tags/main", True),
        ("refs/heads/*", True),
        ("heads/*", True),
        ("*", True),
        ("refs/remotes/*", True),
        ("remotes/*", True),
        ("refs/replace/*", True),
        ("refs/*", True),
        ("+refs/heads/*", True),
        ("feature/x", False),
        ("refs/heads/feature/x", False),
        ("feature/main", False),
        ("main-old", False),
        ("mainline", False),
        ("refs/tags/v1", False),
        ("tags/v1", False),
        ("refs/tags/feature/main", False),
        ("refs/stash", False),
        ("refs/main", False),
        ("origin/main", False),
        ("replace/x", False),
        ("refs/tags/*", False),
        ("refs/heads/feature/*", False),
        ("HEAD", False),
        ("", False),
    ],
)
def test_protected_names(ref: str, expected: bool) -> None:  # noqa: FBT001 # -- a parametrized test argument
    assert protects(ref) is expected


@pytest.mark.parametrize(
    ("destination", "expected"),
    [
        ("refs/heads/main", ("refs/heads/main",)),
        ("+refs/x", ("refs/x",)),
        ("main", ("refs/main", "refs/tags/main", "refs/heads/main")),
        ("heads/main", ("refs/heads/main", "refs/tags/heads/main", "refs/heads/heads/main")),
        (
            "origin/main",
            (
                "refs/origin/main",
                "refs/tags/origin/main",
                "refs/heads/origin/main",
                "refs/remotes/origin/main",
                "refs/remotes/origin/main/HEAD",
            ),
        ),
        (
            "origin",
            (
                "refs/origin",
                "refs/tags/origin",
                "refs/heads/origin",
                "refs/remotes/origin",
                "refs/remotes/origin/HEAD",
            ),
        ),
        ("originals/x", ("refs/originals/x", "refs/tags/originals/x", "refs/heads/originals/x")),
    ],
)
def test_a_push_destination_is_every_ref_git_may_match_it_to(
    destination: str, expected: tuple[str, ...]
) -> None:
    assert pushed(destination) == expected


@pytest.mark.parametrize(
    ("refspecs", "expected"),
    [
        (["main"], ["main"]),
        (["a:b"], ["b"]),
        (["+a:b"], ["b"]),
        ([":b"], ["b"]),
        (["HEAD"], [None]),
        (["@"], [None]),
        (["a:HEAD"], [None]),
        (["tag", "v1"], ["refs/tags/v1"]),
        (["tag", "v1", "a:main"], ["refs/tags/v1", "main"]),
        (["tag"], ["refs/tags/"]),
        ([":"], ["refs/heads/*"]),
        (["+:"], ["refs/heads/*"]),
        ([], []),
    ],
)
def test_what_each_push_refspec_writes(refspecs: list[str], expected: list[str | None]) -> None:
    assert list(refmoves.push_targets(refspecs)) == expected


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["--del", "x"], ["--delete", "x"]),
        (["--delete", "x"], ["--delete", "x"]),
        (["--force", "x"], ["--force", "x"]),
        (["--force-c", "x"], ["--force-create", "x"]),
        (["--creat=x"], ["--create=x"]),
        (["--rem", "x"], ["--remotes", "x"]),
        (["--fo", "x"], ["--fo", "x"]),
        (["--zzz", "--del"], ["--zzz", "--delete"]),
        (["-d", "x"], ["-d", "x"]),
        (["--", "--del"], ["--", "--del"]),
        (["a", "--", "--del"], ["a", "--", "--del"]),
        (["--", "--del", "--del"], ["--", "--del", "--del"]),
        (["main"], ["main"]),
        (["--="], ["--="]),
    ],
)
def test_a_long_option_is_made_whole_when_one_name_starts_with_it(
    args: list[str], expected: list[str]
) -> None:
    assert expand_long(args, LONG) == expected


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("remote.origin.url", True),
        ("REMOTE.ORIGIN.FETCH", True),
        ("url.x.insteadOf", True),
        ("alias.p", True),
        ("include.path", True),
        ("includeIf.gitdir:x.path", True),
        ("extensions.worktreeConfig", True),
        ("core.useReplaceRefs", True),
        ("core.worktree", True),
        ("core.bare", True),
        ("core.hooksPath", False),
        ("core.editor", False),
        ("user.name", False),
        ("pull.ff", False),
        ("remotes.x", False),
    ],
)
def test_trusted_config_keys(key: str, expected: bool) -> None:  # noqa: FBT001 # -- a parametrized test argument
    assert refmoves.trusted_key(key) is expected


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        (frozenset({"-e"}), True),
        (frozenset({"--edit"}), True),
        (frozenset({"--ed"}), True),
        (frozenset({"--edi"}), True),
        (frozenset({"--e"}), False),
        (frozenset({"--expiry-date"}), False),
        (frozenset({"--edx"}), False),
        (frozenset({"--get"}), False),
        (frozenset(), False),
    ],
)
def test_config_opens_an_editor(flags: frozenset[str], expected: bool) -> None:  # noqa: FBT001 # -- a parametrized test argument
    assert refmoves.edits_config(flags) is expected


def test_details_name_the_offending_command() -> None:
    assert details("git branch -f main HEAD~3") == ["git branch -f main HEAD~3"]
    assert details("git update-ref --stdin") == ["git update-ref --stdin"]
    assert details("git replace a b") == ["git replace a b"]
    assert details("git remote remove origin") == ["git remote remove origin"]
    assert details("git config remote.origin.url x") == ["git config remote.origin.url"]
    assert details("git config -e") == ["git config --edit"]
    assert details("git -c remote.origin.url=x fetch") == ["git -c remote.origin.url"]
    assert details("git -c user.name=x -c alias.p=push p") == ["git -c alias.p"]
    on_main = make_guard(FakeRepo(branch="main"))
    assert details("git reset --soft HEAD~1", on_main) == ["git reset --soft HEAD~1 on main"]
    assert details("git rebase origin/main", on_main) == ["git rebase origin/main"]
    assert details("git push . x:refs/remotes/origin/main") == [
        "git push . x:refs/remotes/origin/main"
    ]


def test_git_state_is_asked_only_by_the_rules_that_need_it() -> None:
    repo = FakeRepo(branch="feat/x")
    guard = make_guard(repo)
    cases = (
        "git branch -f feature/x main",
        "git branch -m other",
        "git branch -m a b",
        "git update-ref refs/heads/feature/x HEAD",
        "git update-ref HEAD x",
        "git update-ref --no-deref HEAD x",
        "git fetch origin main:feature",
        "git rebase main",
        "git rebase main feature/x",
        "git reset HEAD~1",
        "git reset --soft HEAD",
        "git reset HEAD file",
        "git reset -- file",
        "git switch -c feature/x main",
    )
    for case in cases:
        repo.asked.clear()
        assert rules(case, guard) == []
        wanted = {
            "git branch -m other": 1,
            "git update-ref HEAD x": 1,
            "git rebase main": 1,
            "git rebase main feature/x": 1,
            "git reset HEAD~1": 1,
        }
        assert len(repo.asked) == wanted.get(case, 0), case


def test_a_rename_of_the_current_branch_is_judged_by_its_name() -> None:
    assert rules("git branch -m renamed", make_guard(FakeRepo(branch="main"))) == ["default-ref"]
    assert rules("git branch -m renamed", make_guard(FakeRepo(branch="feat/x"))) == []
    assert rules("git branch -m renamed", make_guard(FakeRepo(branch=None))) == []


def test_the_guard_judges_a_command_by_the_directory_it_runs_in() -> None:
    repo = FakeRepo(branch="main")
    assert rules("git -C sub rebase x", make_guard(repo)) == ["default-ref"]
    assert repo.asked == ["branch /repo/sub"]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/repo/.git/refs/heads/main", True),
        ("/repo/.git/refs/remotes/origin/main", True),
        ("/repo/.git/refs/replace/abc", True),
        ("/repo/.git/refs", True),
        ("/repo/.git/packed-refs", True),
        ("/repo/.git/HEAD", True),
        ("/repo/.git/info/grafts", True),
        ("/repo/.git/shallow", True),
        ("/repo/.git/objects/info/alternates", True),
        ("/repo/.git/objects/info", True),
        ("/elsewhere/.git/refs/heads/main", True),
        ("/elsewhere/.git/packed-refs", True),
        ("/repo/.claude/worktrees/agent-1/.git/refs/heads/main", True),
        ("/repo/.git/info/exclude", False),
        ("/repo/.git/COMMIT_EDITMSG", False),
        ("/repo/.git/objects/pack/x.pack", False),
        ("/repo/.git/worktrees/agent-1/HEAD", False),
        ("/repo/src/.git-not/refs", False),
        ("/repo", False),
        ("/elsewhere", False),
        ("/repo/app/core/money.py", False),
    ],
)
def test_the_git_state_is_guarded_in_every_checkout(path: str, expected: bool) -> None:  # noqa: FBT001 # -- a parametrized test argument
    guard = make_guard()
    hit = guard.locator.classify(Path(path), destructive=False)
    assert (hit is not None and hit.group.name == GIT_STATE_GROUP) is expected
    if hit is not None and expected:
        assert hit.group.locked


def test_removing_a_checkout_or_its_git_directory() -> None:
    guard = make_guard()
    for path, group in (
        ("/elsewhere/.git", GIT_STATE_GROUP),
        ("/elsewhere/.git/refs", GIT_STATE_GROUP),
        ("/elsewhere/.git/objects", GIT_STATE_GROUP),
    ):
        hit = guard.locator.classify(Path(path), destructive=True)
        assert hit is not None
        assert hit.group.name == group
    assert guard.locator.classify(UNGUARDED, destructive=True) is None
    removed = guard.locator.classify(ROOT, destructive=True)
    assert removed is not None
    assert removed.group.name != GIT_STATE_GROUP


def test_the_git_state_is_guarded_with_no_committed_manifest() -> None:
    bare = make_guard(filesystem=FakeFilesystem(guarded=(), unguarded=(ROOT,)))
    assert rules("echo x > .git/refs/heads/main", bare, cwd=ROOT) == ["guard-path-write"]
    assert rules("echo x > CLAUDE.md", bare, cwd=ROOT) == []


def test_the_template_manifest_lists_every_always_on_path() -> None:
    manifest = tomllib.loads(TEMPLATE.read_text(encoding="utf-8"))
    groups = {group["name"]: group for group in manifest["group"]}
    assert set(GIT_STATE_PATTERNS) <= set(groups["local-git"]["paths"])
    assert groups["local-git"]["edit"] == "deny"
    assert GIT_STATE.group_of(".git/refs/heads/main", scope=Scope.REPOSITORY) is not None


@pytest.mark.parametrize(
    ("program", "expected"),
    [
        ("subprocess.run(['git', 'branch', '-f', 'main'])", ["git branch -f main"]),
        ('subprocess.run(["/usr/bin/git", "status"])', ["git status"]),
        ("subprocess.run(['git', '-C', repo, 'branch'])", ["git -C $ratchet_unknown branch"]),
        ("subprocess.run(['git', 'log', f(1)])", ["git log $ratchet_unknown"]),
        ("os.system('git branch -f main')", ["git branch -f main"]),
        ("os.system('cd x && git reset --hard')", ["cd x && git reset --hard"]),
        ("spawnSync('git', ['replace', 'a', 'b'], {cwd: d})", ["git replace a b"]),
        ("system('git', 'branch', '-f', 'main')", ["git branch -f main"]),
        ("new Deno.Command('git', {args: ['branch', 'x']})", ["git branch x"]),
        ("subprocess.run(['git', 'commit', '-m', 'a b'])", ["git commit -m 'a b'"]),
        ("print('digit is not a command')", []),
        ("print('git')", []),
        ("subprocess.run(['gitk'])", []),
        ("subprocess.run(['echo'])", []),
        ("", []),
    ],
)
def test_a_program_that_runs_git_yields_the_command_it_runs(
    program: str, expected: list[str]
) -> None:
    assert interpreters.git_commands(program) == expected
