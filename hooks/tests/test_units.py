"""Unit tests for the guard's lexer, option scanner, manifest, locator and rule catalogue."""

import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from ratchet_guards import bash, gh, git, gitcall
from ratchet_guards.context import Context
from ratchet_guards.manifest import (
    DoorRule,
    EditRule,
    Group,
    ManifestError,
    Scope,
    parse,
    with_plugin_group,
)
from ratchet_guards.options import NO_VALUES, Spec, scan
from ratchet_guards.paths import Hit, Locator
from ratchet_guards.refs import MAIN_BRANCHES
from ratchet_guards.shell import HEREDOC_MARK, PLACEHOLDER, SimpleCommand, lex, unquote
from ratchet_guards.shell import parse as parse_shell
from ratchet_guards.verdict import DISPUTE, RULES, Decision, Verdict, strictest
from ratchet_guards.writes import Touched, touched
from tests.support import (
    HOME,
    LABELS_RECORD,
    MANIFEST,
    ROOT,
    UNGUARDED,
    WORKTREE,
    FakeFilesystem,
    FakeRepo,
    details,
    make_guard,
    rules,
    verdicts,
)

ON_MAIN = make_guard(FakeRepo(branch="main"))
DEFAULT_REF_BECAUSE = (
    "moves, deletes or re-points the default branch, or a ref or setting it is read through"
)


def test_the_rule_catalogue_is_pinned() -> None:
    catalogue = {rule: (spec.decision.name, spec.because) for rule, spec in RULES.items()}
    assert catalogue == {
        "guard-path-default": (
            "DENY",
            (
                "changes a guard path where the commit lands on the default branch, which no "
                "merge judge reads"
            ),
        ),
        "guard-path-locked": (
            "DENY",
            "edits a guard path that only the owner changes, by hand, outside Claude Code",
        ),
        "guard-path-write": (
            "DENY",
            "writes, moves or deletes, from the shell, a guard path that only the owner changes",
        ),
        "guard-path-unseen-switch": (
            "DENY",
            (
                "changes a guard path after a command that may switch branches in a way the guard "
                "cannot read, so the change may land on the default branch"
            ),
        ),
        "no-verify": ("DENY", "skips the git hooks"),
        "skip-hooks": ("DENY", "skips or uninstalls the pre-commit hooks"),
        "hooks-path": ("DENY", "redirects git hooks or overrides git config"),
        "force-push": ("DENY", "force-pushes, which rewrites published history"),
        "push-main": (
            "DENY",
            (
                "pushes to the default branch directly, where the method lands a change only "
                "through a merged pull request"
            ),
        ),
        "reset-main": ("DENY", "moves main back or sideways with a reset"),
        "default-ref": ("DENY", DEFAULT_REF_BECAUSE),
        "amend-pushed": ("DENY", "amends a commit that is already pushed"),
        "rewrite-history": ("DENY", "rewrites history"),
        "unresolved-directory": (
            "DENY",
            (
                "writes a relative path after a cd the guard cannot follow; it may be a guard "
                "path that only the owner changes, or one on the default branch"
            ),
        ),
        "merge-pr": ("ASK", "merges a pull request"),
        "merge-default": ("ASK", "merges another branch into the default branch"),
        "branch-protection": ("DENY", "reads or changes branch protection or rulesets"),
        "repo-settings": ("DENY", "changes repository, secret, variable or workflow settings"),
        "labels": ("DENY", "manages labels"),
        "agent-ready-label": (
            "ASK",
            "This marks a ticket ready for agents to build without you, which is yours to decide",
        ),
        "workflow-runs": ("DENY", "deletes or cancels workflow runs"),
        "nested-settings": (
            "DENY",
            "starts Claude Code with other settings sources or with hooks or plugins off",
        ),
        "plugin-removal": (
            "DENY",
            "disables or removes a harness plugin, package or marketplace",
        ),
        "workflow-write": (
            "ASK",
            "This changes what GitHub runs with this repository's secrets, before any merge",
        ),
        "instructions-write": (
            "ASK",
            "This changes the instructions agents follow in this project, starting now",
        ),
        "unreadable": ("ASK", "The guard could not check this tool call"),
    }


def test_only_a_rule_with_a_way_out_of_its_own_points_somewhere_other_than_a_dispute() -> None:
    elsewhere = {rule for rule, spec in RULES.items() if spec.way_out != DISPUTE}
    allow = {"workflow-write", "instructions-write", "agent-ready-label"}
    assert elsewhere == {
        "guard-path-default",
        "guard-path-unseen-switch",
        "unresolved-directory",
        "push-main",
        "merge-pr",
        "merge-default",
        "unreadable",
        *allow,
    }
    assert len({RULES[rule].way_out for rule in elsewhere - allow}) == len(elsewhere - allow)
    assert {RULES[rule].way_out for rule in allow} == {"Allow it?"}
    assert "git switch -c" in RULES["guard-path-default"].way_out


def test_deny_outranks_ask_and_the_first_of_equals_wins() -> None:
    assert Decision.DENY > Decision.ASK
    ask, deny, deny2 = Verdict("merge-pr", "a"), Verdict("labels", "b"), Verdict("labels", "c")
    assert strictest([ask, deny, deny2]) is deny
    assert strictest([ask]) is ask
    assert strictest([]) is None
    assert deny.because == "manages labels"


def test_scan_separates_flags_values_and_operands() -> None:
    options = scan(
        ["-am", "msg", "-ofoo", "--x=1", "--repo", "r", "-", "a", "--", "-b"],
        Spec("mo", frozenset({"--repo"})),
    )
    assert options.flags == frozenset({"-a", "-m", "-o", "--x", "--repo"})
    assert options.values == {"-m": "msg", "-o": "foo", "--x": "1", "--repo": "r"}
    assert options.operands == ("-", "a", "-b")


def test_scan_long_option_without_a_declared_value_takes_none() -> None:
    options = scan(["--force", "origin"], NO_VALUES)
    assert options.flags == frozenset({"--force"})
    assert options.values == {}
    assert options.operands == ("origin",)


def test_lexer_lifts_substitutions_and_heredocs() -> None:
    tokens, nested, heredocs = lex("echo $(a) `b` \"$(c)\" <<- 'END'\n\tbody\n\tEND\nnext")
    words = [token.text for token in tokens if not token.operator]
    assert words == [
        "echo",
        PLACEHOLDER,
        PLACEHOLDER,
        f'"{PLACEHOLDER}"',
        f"{HEREDOC_MARK}0",
        "next",
    ]
    assert nested == ("a", "b", "c")
    assert heredocs == ("\tbody",)


def words_of(text: str) -> list[str]:
    return [token.text for token in lex(text)[0] if not token.operator]


def test_lexer_quoting_and_continuations() -> None:
    assert words_of('echo "a\\"b" c') == ["echo", '"a\\"b"', "c"]
    assert words_of("ec\\\nho x") == ["echo", "x"]
    assert lex("echo `a` `b` `` x")[1] == ("a", "b", "")
    assert lex("echo $() && rm x")[1] == ("",)
    assert "rm" in words_of("echo $() && rm x")
    assert lex("echo $( (a) ; b ) c")[1] == (" (a) ; b ",)
    assert words_of("echo $( (a) ; b ) c")[-1] == "c"


def test_a_case_pattern_does_not_close_a_substitution() -> None:
    assert lex("echo $(case a in b) x;; esac) c")[1] == ("case a in b) x;; esac",)
    assert words_of("echo $(case a in b) x;; esac) c")[-1] == "c"
    assert lex("echo $(case a in (b) x;; esac; ls) $(pwd)")[1] == (
        "case a in (b) x;; esac; ls",
        "pwd",
    )


def test_lexer_reads_every_heredoc_body() -> None:
    assert lex("cat <<A <<B\na\nA\nb\nB")[2] == ("a", "b")
    assert lex("cat <<-E\n  E\nXE\n\tE\nafter")[2] == ("  E\nXE",)
    assert lex("cat <<E\n\tE\nE")[2] == ("\tE",)
    assert lex("x $(cat <<EOF) y")[2] == ("",)


def test_lexer_survives_unterminated_constructs() -> None:
    assert lex("echo 'open")[0][1].text == "'open\n"
    assert lex("echo `open")[1] == ("open\n",)
    assert lex("echo $(ls")[1] == ("ls\n",)
    assert lex('echo "open')[0][1].text == '"open\n"'
    assert lex("cat <<EOF\nnever closed")[2] == ("never closed",)
    assert lex("cat <<")[0][-2].text == f"{HEREDOC_MARK}0"


def test_comments_start_only_at_a_word() -> None:
    assert [c.words for c in parse_shell("echo a#b # c\nls").commands] == [("echo", "a#b"), ("ls",)]
    assert parse_shell("# only a comment").commands == ()


def test_redirects_and_file_descriptors() -> None:
    commands = parse_shell("cmd 2>err >out 2>&1 >&- <in &>both 1>>more <<<here 12>fd x").commands
    writes = ("err", "out", "both", "more", "fd")
    assert commands == (SimpleCommand(("cmd", "x"), writes, herestrings=("here",), then="\n"),)
    assert parse_shell("echo 1&& ls").commands[0].words == ("echo", "1")
    assert words_of("a ) b") == ["a", "b"]
    assert parse_shell("a | b |& c; d").commands == (
        SimpleCommand(("a",), piped=True, then="|"),
        SimpleCommand(("b",), piped=True, then="|&"),
        SimpleCommand(("c",), then=";"),
        SimpleCommand(("d",), then="\n"),
    )
    ends = [command.then for command in parse_shell("a && b || c & (d) ; e").commands]
    assert ends == ["&&", "||", "&", ")", "\n"]
    assert [c.then for c in parse_shell("f() { g; }").commands] == ["(", ";", "\n"]


def test_unquote_falls_back_to_dropping_quotes() -> None:
    assert unquote("'a b'") == "a b"
    assert unquote('a"b c"d') == "ab cd"
    assert unquote("a\\") == "a\\"
    assert unquote("'x") == "x"
    assert unquote('""') == ""


GOOD = """
[[group]]
name = "g"
reason = "because"
paths = ["a/**", "b.txt", "~/.x/*.json"]
"""


def test_exclude_carves_paths_out_of_a_group() -> None:
    carved = parse(GOOD.replace('reason = "because"', 'reason = "because"\nexclude = ["a/wt/**"]'))
    assert carved.groups[0].excluded == ("a/wt/**",)
    assert carved.group_of("a/wt/x/b.txt") is None
    assert carved.group_of("a/other") is carved.groups[0]
    assert carved.group_below("a/wt/x") is None
    assert parse(GOOD).groups[0].excluded == ()
    with pytest.raises(ManifestError, match=r"^group 'exclude' must be a list$"):
        parse(GOOD.replace('reason = "because"', 'reason = "because"\nexclude = "a/wt/**"'))
    with pytest.raises(ManifestError, match=r"^pattern '/abs' must be"):
        parse(GOOD.replace('reason = "because"', 'reason = "because"\nexclude = ["/abs"]'))


def test_a_group_with_no_paths_yet_matches_nothing() -> None:
    # Why: the adopt template ships `paths = []` behind `# bind:` comments until adopt fills
    # them; a manifest that failed to parse would turn every tool call into an ask meanwhile.
    unbound = parse(GOOD.replace('paths = ["a/**", "b.txt", "~/.x/*.json"]', "paths = []"))
    assert unbound.groups[0].patterns == ()
    assert unbound.group_of("a/x") is None
    assert unbound.group_below("a") is None


def test_agent_worktrees_are_not_guard_paths_of_the_main_checkout() -> None:
    assert MANIFEST.group_of(".claude/worktrees/agent-x/app/src/a.py") is None
    assert MANIFEST.group_of(".claude/worktrees/agent-x/.claude/settings.json") is None
    assert MANIFEST.group_of(".claude/worktrees") is None
    harness = MANIFEST.group_of(".claude/settings.json")
    assert harness is not None
    assert MANIFEST.group_below(".claude") is harness


def test_edit_decides_whether_a_group_is_locked() -> None:
    locked = parse(GOOD.replace('reason = "because"', 'reason = "because"\nedit = "deny"'))
    assert locked.groups[0].locked is True
    asked = parse(GOOD.replace('reason = "because"', 'reason = "because"\nedit = "ask"'))
    assert asked.groups[0].locked is False
    assert parse(GOOD).groups[0].locked is False
    with pytest.raises(
        ManifestError,
        match="group 'edit' must be one of \\['append', 'ask', 'deny'\\], not 'never'",
    ):
        parse(GOOD.replace('reason = "because"', 'reason = "because"\nedit = "never"'))
    with pytest.raises(ManifestError, match=r"group 'edit' must be one of .*, not True"):
        parse(GOOD.replace('reason = "because"', 'reason = "because"\nedit = true'))


def test_edit_append_marks_a_group_additive_and_not_locked() -> None:
    group = parse(GOOD.replace('reason = "because"', 'reason = "because"\nedit = "append"')).groups[
        0
    ]
    assert (group.additive, group.locked, group.edit) == (True, False, EditRule.APPEND)
    assert parse(GOOD).groups[0].additive is False
    assert [group.name for group in MANIFEST.groups if group.additive] == ["records"]


def test_manifest_matches_repository_and_home_patterns_separately() -> None:
    manifest = parse(GOOD)
    group = manifest.groups[0]
    assert group == Group("g", "because", ("a/**", "b.txt", "~/.x/*.json"))
    assert manifest.group_of("a/z/y") is group
    assert manifest.group_of("b.txt") is group
    assert manifest.group_of(".x/s.json") is None
    assert manifest.group_of(".x/s.json", scope=Scope.HOME) is group
    assert manifest.group_of("b.txt", scope=Scope.HOME) is None
    assert manifest.patterns() == ["a/**", "b.txt", "~/.x/*.json"]


@pytest.mark.parametrize(
    ("directory", "home", "found"),
    [
        (".", False, True),
        ("a", False, True),
        ("a/", False, True),
        ("a/deep", False, True),
        ("b.txt", False, True),
        ("c", False, False),
        ("ab", False, False),
        ("aX", False, False),
        (".x/s.json", True, True),
        (".", True, True),
        (".x", True, True),
        ("a", True, False),
    ],
)
def test_group_below(directory: str, home: bool, found: bool) -> None:  # noqa: FBT001 # -- parametrized
    scope = Scope.HOME if home else Scope.REPOSITORY
    assert (parse(GOOD).group_below(directory, scope=scope) is not None) is found


def test_group_below_reads_repository_patterns_by_default() -> None:
    assert parse(GOOD).group_below(".x") is None


LAYERED = """
[[group]]
name = "open"
reason = "because"
paths = ["d/x.toml", "e/g/h.toml", "k/a.toml"]

[[group]]
name = "shut"
reason = "because"
edit = "deny"
paths = ["d/y.json", "e/**"]

[[group]]
name = "late"
reason = "because"
paths = ["d/z.md", "k/b.md"]
"""


@pytest.mark.parametrize(
    ("directory", "group"),
    [
        # Why: removing a directory removes every path in it, and on a branch only a locked
        # group is refused, so the locked one is the verdict wherever it appears (ADR 0033).
        (".", "shut"),
        ("d", "shut"),
        ("e/g", "shut"),
        ("e/f", "shut"),
        ("k", "open"),
        ("d/x.toml", "open"),
        ("d/z.md", "late"),
    ],
)
def test_group_below_prefers_a_locked_group(directory: str, group: str) -> None:
    found = parse(LAYERED).group_below(directory)
    assert found is not None
    assert found.name == group


PATTERN = "pattern {} must be a non-empty relative glob without '..'"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("not toml [", "Expected '=' after a key in a key/value pair (at line 1, column 5)"),
        ("x = 1", "the manifest needs at least one [[group]]"),
        ("group = []", "the manifest needs at least one [[group]]"),
        ("group = [1]", "every [[group]] must be a table"),
        ('[[group]]\nname = "n"\nreason = "r"', "group 'paths' must be a list"),
        ('[[group]]\nname = "n"\nreason = "r"\npaths = "a"', "group 'paths' must be a list"),
        (
            '[[group]]\nname = " "\nreason = "r"\npaths = ["a"]',
            "group 'name' must be a non-empty string",
        ),
        ('[[group]]\nname = "n"\npaths = ["a"]', "group 'reason' must be a non-empty string"),
        ('[[group]]\nname = "n"\nreason = "r"\npaths = [1]', PATTERN.format("1")),
        ('[[group]]\nname = "n"\nreason = "r"\npaths = [""]', PATTERN.format("''")),
        ('[[group]]\nname = "n"\nreason = "r"\npaths = ["/abs"]', PATTERN.format("'/abs'")),
        ('[[group]]\nname = "n"\nreason = "r"\npaths = ["a/../b"]', PATTERN.format("'a/../b'")),
    ],
)
def test_malformed_manifests_are_rejected(text: str, message: str) -> None:
    with pytest.raises(ManifestError, match=f"^{re.escape(message)}$"):
        parse(text)


DOORS = """
[[door]]
name = "one-way"
reason = "A migration cannot be taken back."
paths = ["migrations/**", "infra/*.tf"]

[[door]]
name = "stored-data"
reason = "Old data must still load."
paths = ["tests/data/**"]
on = "rewrite"

[[door]]
name = "contracts"
reason = "A consumer depends on every line."
paths = []
on = "removal"
"""


def test_one_way_doors_are_read_beside_the_groups_and_guard_nothing() -> None:
    manifest = parse(GOOD + DOORS)
    assert [(door.name, door.on) for door in manifest.doors] == [
        ("one-way", DoorRule.CHANGE),
        ("stored-data", DoorRule.REWRITE),
        ("contracts", DoorRule.REMOVAL),
    ]
    assert [door.name for door in manifest.doors_of("migrations/0002_more.sql")] == ["one-way"]
    assert manifest.doors_of("infra/main.tf")[0].reason == "A migration cannot be taken back."
    assert manifest.doors_of("src/app.py") == []
    assert manifest.doors_of("tests/contract/api.json") == [], "a door with no paths yet"
    assert manifest.group_of("migrations/0002_more.sql") is None, "a door is no guard group"
    assert parse(GOOD).doors == ()
    assert with_plugin_group(manifest, Path("/plugin")).doors == manifest.doors


@pytest.mark.parametrize(
    ("door", "message"),
    [
        ('door = "migrations"', "'door' must be an array of tables; write [[door]]"),
        ("door = [1]", "every [[door]] must be a table"),
        ('[[door]]\nname = "d"\nreason = "r"', "door 'paths' must be a list"),
        ('[[door]]\nname = "d"\npaths = []', "door 'reason' must be a non-empty string"),
        ('[[door]]\nreason = "r"\npaths = []', "door 'name' must be a non-empty string"),
        (
            '[[door]]\nname = "d"\nreason = "r"\npaths = []\non = "never"',
            "door 'on' must be one of ['change', 'removal', 'rewrite'], not 'never'",
        ),
        (
            '[[door]]\nname = "d"\nreason = "r"\npaths = ["/abs"]',
            "pattern '/abs' must be a non-empty relative glob without '..'",
        ),
    ],
)
def test_malformed_doors_are_rejected(door: str, message: str) -> None:
    text = f"{door}\n{GOOD}" if door.startswith("door") else f"{GOOD}\n{door}\n"
    with pytest.raises(ManifestError, match=f"^{re.escape(message)}"):
        parse(text)


def test_the_fixture_manifest_guards_the_required_paths() -> None:
    for path in [
        "config/lint.toml",
        ".claude/settings.json",
        "quality/thresholds.toml",
        "tools/guards/hook.py",
        "docs/adr/0001-guards.md",
    ]:
        assert MANIFEST.group_of(path) is not None, path
    assert MANIFEST.group_of("docs/disputes/0001-x.md") is None
    assert MANIFEST.group_of(".claude/settings.json", scope=Scope.HOME) is not None


def test_locator_prefers_a_home_hit_and_falls_back_to_the_checkout() -> None:
    locator = Locator(MANIFEST, FakeFilesystem(guarded=(ROOT, HOME / "repo")))
    settings = locator.classify(HOME / ".claude/settings.json", destructive=False)
    assert settings is not None
    assert settings.path == "~/.claude/settings.json"
    hit = locator.classify(HOME / "repo/CLAUDE.md", destructive=False)
    assert hit == Hit("CLAUDE.md", MANIFEST.groups[-1], HOME / "repo/CLAUDE.md")
    assert locator.classify(HOME / "notes.txt", destructive=True) is None
    assert locator.classify(Path("/nowhere/CLAUDE.md"), destructive=False) is None


def test_a_hit_names_the_canonical_path_it_found() -> None:
    locator = Locator(MANIFEST, FakeFilesystem())
    hit = locator.classify(ROOT / "docs/../CLAUDE.md", destructive=False)
    assert hit == Hit("CLAUDE.md", MANIFEST.groups[-1], ROOT / "CLAUDE.md")
    below = locator.classify(ROOT / "tools/../.claude", destructive=True)
    assert below is not None
    assert below.at == ROOT / ".claude"


def test_locator_judges_a_path_by_the_manifest_of_its_own_checkout() -> None:
    other = Path("/other")
    mine = parse('[[group]]\nname = "mine"\nreason = "r"\npaths = ["only-here.txt"]\n')
    filesystem = FakeFilesystem(guarded=(ROOT, other), manifests={other: mine})
    locator = Locator(MANIFEST, filesystem)
    hit = locator.classify(other / "only-here.txt", destructive=False)
    assert hit is not None
    assert (hit.path, hit.group.name) == ("only-here.txt", "mine")
    assert locator.classify(other / "CLAUDE.md", destructive=False) is None
    assert locator.classify(other, destructive=True) is not None
    assert locator.classify(ROOT / "only-here.txt", destructive=False) is None
    session_hit = locator.classify(ROOT / "CLAUDE.md", destructive=False)
    assert session_hit is not None
    assert session_hit.group.name == "decisions"
    assert locator.classify(UNGUARDED / "CLAUDE.md", destructive=False) is None


def test_locator_skips_empty_words_and_expands_globs() -> None:
    locator = Locator(MANIFEST, FakeFilesystem())
    assert locator.words(["", "README.md"], ROOT, destructive=False) == []
    assert [hit.path for hit in locator.words(["", "CLAUDE.md"], ROOT, destructive=False)] == [
        "CLAUDE.md"
    ]
    globbed = locator.words(["config/types*"], ROOT, destructive=False)
    assert [hit.path for hit in globbed] == [
        "config/types.json",
        "config/types.node.json",
    ]
    unmatched = locator.words(["formal/*.nothing"], ROOT, destructive=False)
    assert [hit.path for hit in unmatched] == ["formal/*.nothing"]


def test_home_patterns_match_only_home_paths() -> None:
    home_only = parse('[[group]]\nname = "h"\nreason = "r"\npaths = ["~/.config/tool/**"]\n')
    locator = Locator(home_only, FakeFilesystem())
    below = locator.words(["~/.config/tool"], ROOT, destructive=True)
    assert [hit.path for hit in below] == ["~/.config/tool"]
    assert locator.classify(HOME / ".config", destructive=True) is not None
    assert locator.classify(HOME / ".config/tool/x", destructive=False) is not None
    assert locator.classify(HOME / ".config", destructive=False) is None


def test_writers() -> None:
    assert touched("cp", ["only"]) == Touched(written=("only",))
    assert touched("mv", ["a/", "b"]) == Touched(written=("b", "b/a"), removed=("a/",))
    assert touched("cp", ["a", "CLAUDE.md", "out/"]) == Touched(
        written=("out/", "out/a", "out/CLAUDE.md")
    )
    assert touched("mv", ["n", "CLAUDE.md", "d"]) == Touched(
        written=("d", "d/n", "d/CLAUDE.md"), removed=("n", "CLAUDE.md")
    )
    assert touched("cp", ["-t", "d", "a"]) == Touched(written=("d", "d/a"))
    assert touched("mv", ["--target-directory", "d", "a"]) == Touched(
        written=("d", "d/a"), removed=("a",)
    )
    assert touched("truncate", ["-s", "0", "f"]) == Touched(removed=("f",))
    assert touched("sed", ["-i", "-f", "script.sed", "f"]) == Touched(written=("f",))
    assert touched("sed", ["--in-place", "--expression", "s/a/b/", "f"]) == Touched(written=("f",))
    assert touched("sed", ["s/a/b/", "f"]) == Touched()
    assert touched("dd", ["if=x", "of=y"]) == Touched(written=("y",))
    assert touched("ls", ["-l"]) == Touched()


def test_git_invocation_parses_global_options() -> None:
    call = gitcall.invocation(
        ["git", "-C", "sub", "-c", "a=b", "--no-pager", "--git-dir=x", "log", "-1"], ROOT
    )
    assert call == gitcall.Invocation(ROOT / "sub", ("a=b",), "log", ("-1",), redirected=True)
    assert not gitcall.invocation(["git", "-C", "sub", "--no-pager", "log"], ROOT).redirected
    assert gitcall.invocation(["git", "-C"], ROOT) == gitcall.Invocation(ROOT, (), "", ())
    assert gitcall.invocation(["git"], ROOT).subcommand == ""


SURE = True
MAYBE = False
ANYWHERE = (frozenset({"main", "master"}), MAYBE)


@pytest.mark.parametrize(
    ("words", "expected"),
    [
        (["git", "switch", "main"], ({"main"}, SURE)),
        (["git", "switch", "-c", "x"], ({"x"}, SURE)),
        (["git", "switch", "--create", "y"], ({"y"}, SURE)),
        (["git", "switch", "--creat", "y"], ({"y"}, SURE)),
        (["git", "checkout", "-b", "z", "origin/main"], ({"z"}, SURE)),
        (["git", "checkout", "-B", "w"], ({"w"}, SURE)),
        (["git", "switch", "-C", "v"], ({"v"}, SURE)),
        (["git", "switch", "--force-create", "u"], ({"u"}, SURE)),
        (["git", "switch", "--orphan", "t"], ({"t"}, SURE)),
        (["git", "switch", "main", "-c", "topic"], ({"topic"}, SURE)),
        (["git", "switch", "-f", "feat/x"], ({"feat/x"}, SURE)),
        (["git", "checkout", "feat/x"], ({"feat/x"}, SURE)),
        (["git", "checkout", "main"], ({"main"}, SURE)),
        (["git", "checkout", "README.md"], ({"README.md"}, MAYBE)),
        (["git", "checkout", "origin/main"], ({"origin/main"}, MAYBE)),
        (["git", "checkout", "unknown"], ({"unknown"}, MAYBE)),
        (["git", "-C", "sub", "switch", "feat/x"], ({"feat/x"}, MAYBE)),
        (["git", "--git-dir=x", "switch", "feat/x"], ({"feat/x"}, MAYBE)),
        (["git", "--work-tree", "x", "switch", "-c", "feat/x"], ({"feat/x"}, MAYBE)),
        (["git", "-C", "sub", "symbolic-ref", "HEAD", "refs/heads/x"], ({"x"}, MAYBE)),
        (["git", "switch", "-"], ANYWHERE),
        (["git", "checkout", "-"], ANYWHERE),
        (["git", "switch", "@{-1}"], ANYWHERE),
        (["git", "checkout", "@{-2}"], ANYWHERE),
        (["git", "switch", "-t", "origin/main"], ANYWHERE),
        (["git", "checkout", "--track", "origin/master"], ANYWHERE),
        (["git", "switch", "--tr", "origin/main"], ANYWHERE),
        (["git", "bisect", "reset", "main"], ANYWHERE),
        (["git", "bisect", "start"], ANYWHERE),
        (["git", "symbolic-ref", "HEAD", "refs/heads/x"], ({"x"}, SURE)),
        (["git", "symbolic-ref", "-m", "why", "HEAD", "refs/heads/y"], ({"y"}, SURE)),
        (["git", "symbolic-ref", "HEAD"], None),
        (["git", "symbolic-ref", "--short", "HEAD"], None),
        (["git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/heads/x"], None),
        (["git", "symbolic-ref"], None),
        (["git", "checkout", "--", "file"], None),
        (["git", "checkout", "main", "--", "file"], None),
        (["git", "checkout"], None),
        (["git", "switch", "--detach", "main"], None),
        (["git", "switch", "-d", "main"], None),
        (["git", "checkout", "--det", "main"], None),
        (["git", "status"], None),
    ],
)
def test_where_a_command_moves_head(
    words: list[str], expected: tuple[set[str], bool] | None
) -> None:
    move = git.switch(words, Context(ROOT), make_guard())
    assert (None if move is None else (move.branches, move.certain)) == expected


@pytest.mark.parametrize("name", ["GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "PATH", "CDPATH"])
def test_a_switch_is_not_certain_under_a_variable_that_redirects_it(name: str) -> None:
    context = Context(ROOT).exporting([name])
    move = git.switch(["git", "switch", "feat/x"], context, make_guard())
    assert move == git.Switch(frozenset({"feat/x"}), certain=False)
    assert git.switch(["git", "switch", "feat/x"], Context(ROOT).exporting(["FOO"]), make_guard())


def test_details_name_the_offending_command() -> None:
    assert details("git push -f origin x") == ["git push -f origin x"]
    assert details("echo x > CLAUDE.md", ON_MAIN) == [
        "redirects into CLAUDE.md (decisions)",
        "CLAUDE.md",
    ]
    assert details("rm -rf formal", ON_MAIN) == ["rm formal (formal)"]
    assert details("git rm justfile", ON_MAIN) == ["git rm justfile (gate-commands)"]
    assert details("echo x > .claude/settings.json") == [
        "redirects into .claude/settings.json (harness)",
        ".claude/settings.json",
    ]
    assert details("gh pr merge 3") == ["gh pr merge"]
    assert details("gh api -X delete repos/o/r/actions/runs/1") == [
        "gh api DELETE repos/o/r/actions/runs/1"
    ]
    assert details("SKIP=x git commit") == ["SKIP=x"]
    assert details("RATCHET_HOOK_PROBE=x git commit") == ["RATCHET_HOOK_PROBE=x"]
    assert details("git commit --no-verify") == ["git commit --no-verify"]
    assert details("git commit -n -m x") == ["git commit -n"]
    assert details("git push origin main") == ["git push origin main"]
    assert details("git filter-repo --force") == ["git filter-repo"]
    assert details("git config core.hooksPath /x") == ["git config core.hooksPath"]
    assert details("git -c core.hooksPath=/x status") == ["git -c core.hooksPath"]
    assert details("GIT_CONFIG_COUNT=1 git status") == ["GIT_CONFIG_COUNT=1"]
    assert details("claude --settings x.json -p hi") == ["--settings"]
    assert details("pre-commit uninstall") == ["pre-commit uninstall"]


def test_recorded_labels_are_read_from_the_checkout_holding_cwd() -> None:
    assert rules("gh label create needs-triage") == []
    assert rules("gh label create needs-triage", cwd=ROOT / "app") == []
    assert rules("gh label create needs-triage", cwd=UNGUARDED) == ["labels"]
    without = make_guard(filesystem=FakeFilesystem(contents={}))
    assert rules("gh label create needs-triage", without) == ["labels"]


def test_the_agent_ready_label_is_the_recorded_spelling_or_the_role_name() -> None:
    assert rules("gh issue edit 4 --add-label agent:ready") == ["agent-ready-label"]
    assert rules("gh issue edit 4 --add-label ready-for-agent") == []
    assert rules("gh issue edit 4 --add-label agent:ready", cwd=UNGUARDED) == []
    assert rules("gh issue edit 4 --add-label ready-for-agent", cwd=UNGUARDED) == [
        "agent-ready-label"
    ]
    without = make_guard(filesystem=FakeFilesystem(contents={}))
    assert rules("gh issue edit 4 --add-label ready-for-agent", without) == ["agent-ready-label"]
    assert gh.label_for_role(LABELS_RECORD, "ready-for-agent") == "agent:ready"
    assert gh.label_for_role(LABELS_RECORD, "needs-info") is None
    assert gh.label_for_role("| Role | Label |\n| --- | --- |\n| a | b |", "a") is None
    assert (
        gh.label_for_role("| Label in mattpocock/skills | Label in our tracker |\n| x |", "x")
        is None
    )
    assert gh.applied_labels(["issue", "edit", "4", "--add-label"]) == []
    assert gh.applied_labels(["-l", "a, b", "--label=c", "--add-label", ""]) == ["a", "b", "c"]


def test_listed_labels_reads_the_tracker_column() -> None:
    assert gh.listed_labels(LABELS_RECORD) == {"needs-triage", "agent:ready", "wontfix"}
    assert gh.listed_labels("| Label in our tracker | Meaning |\n| --- | --- |\n| x | y |") == {"x"}
    assert gh.listed_labels("| Role | Label |\n| --- | --- |\n| a | b |") == frozenset()
    assert gh.listed_labels("prose with | pipes | and `needs-triage`") == frozenset()
    assert gh.listed_labels("") == frozenset()


def test_gh_details_name_the_subcommand_and_method() -> None:
    assert details("gh label") == []
    assert details("gh label list") == []
    assert details("gh label create bug") == ["gh label create"]
    assert details("gh -R o/r label edit bug --color 000") == ["gh label edit"]
    assert details("gh pr --squash merge 12") == ["gh pr merge"]
    assert details("gh api repos/o/r/labels -f name=x") == ["gh api POST repos/o/r/labels"]


def test_details_that_need_git_state() -> None:
    on_main = make_guard(FakeRepo(branch="main", pushed=True))
    assert details("git reset --hard", on_main) == ["git reset --hard on main"]
    assert details("git commit --amend", on_main) == ["git commit --amend on a pushed HEAD"]


def test_git_state_is_read_where_the_command_runs() -> None:
    repo = FakeRepo(branch="main", pushed=True)
    verdicts(
        "git -C sub push; git -C sub reset --hard; git -C sub commit --amend", make_guard(repo)
    )
    assert repo.asked == ["branch /repo/sub", "branch /repo/sub", "pushed /repo/sub"]


def test_a_wrapper_chain_is_judged_once() -> None:
    assert len(verdicts("sudo nice git push --force")) == 1


def test_shells_with_long_options_still_read_the_script() -> None:
    assert rules("bash --norc -c 'git push -f'") == ["force-push"]
    assert rules("bash --norc script.sh") == []


def test_deep_nesting_stops_at_a_human() -> None:
    command = "echo " + "$(" * (bash.MAX_DEPTH + 2) + "ls" + ")" * (bash.MAX_DEPTH + 2)
    assert rules(command) == ["unreadable"]
    assert set(details(command)) == {"the command nests scripts too deeply"}
    assert rules("echo " + "$(" * bash.MAX_DEPTH + "ls" + ")" * bash.MAX_DEPTH) == []


def _bash_c(script: str) -> str:
    return "bash -c " + shlex.quote(script)


def _piped(script: str) -> str:
    return "echo " + shlex.quote(script) + " | bash"


@pytest.mark.parametrize("wrap", [_bash_c, _piped])
def test_each_nested_shell_counts_towards_the_depth_limit(wrap: Callable[[str], str]) -> None:
    command = "git push --force"
    for _ in range(bash.MAX_DEPTH):
        command = wrap(command)
    assert rules(command) == ["force-push"]
    assert rules(wrap(command)) == ["unreadable"]


@dataclass
class Heads(FakeRepo):
    """A fake repository where each checkout has its own branch and the default ones are chosen."""

    heads: dict[Path, str | None] = field(default_factory=dict)
    defaults: frozenset[str] = MAIN_BRANCHES

    def checked_out(self, root: Path) -> str | None:
        return self.heads.get(root, self.branch)

    def default_branches(self, root: Path) -> frozenset[str]:
        del root
        return self.defaults


@pytest.mark.parametrize(
    ("head", "lands"),
    [
        ("main", True),
        ("master", True),
        ("feat/x", False),
        ("HEAD", False),
        (None, True),
    ],
    ids=["main", "master", "a branch", "a detached HEAD", "git cannot say"],
)
def test_a_change_lands_now_on_a_default_branch_and_where_git_cannot_say(
    head: str | None, *, lands: bool
) -> None:
    guard = make_guard(Heads(branch=head))
    assert guard.lands_now(Context(ROOT), ROOT / "CLAUDE.md") is lands


@pytest.mark.parametrize(
    ("head", "lands"),
    [("main", True), ("feat/x", False), ("HEAD", False), (None, True)],
    ids=["main", "a branch", "a detached HEAD", "git cannot say"],
)
def test_a_change_lands_where_the_checkout_holding_the_file_is_not_where_the_line_runs(
    head: str | None, *, lands: bool
) -> None:
    guard = make_guard(Heads(branch="main", heads={WORKTREE: head}))
    assert guard.lands_now(Context(ROOT), WORKTREE / "CLAUDE.md") is lands


def test_the_branch_of_the_checkout_the_line_runs_in_does_not_decide_for_another_checkout() -> None:
    on_a_branch = make_guard(Heads(branch="feat/x", heads={WORKTREE: "main"}))
    assert on_a_branch.lands_now(Context(ROOT), WORKTREE / "CLAUDE.md")
    on_main = make_guard(Heads(branch="main", heads={WORKTREE: "feat/x"}))
    assert not on_main.lands_now(Context(ROOT), WORKTREE / "CLAUDE.md")


def test_a_switch_the_line_makes_counts_for_its_own_checkout_only() -> None:
    worktree_on_main = make_guard(Heads(branch="feat/x", heads={WORKTREE: "main"}))
    assert worktree_on_main.lands_now(Context(ROOT).switched_to("feat/y"), WORKTREE / "CLAUDE.md")
    worktree_on_a_branch = make_guard(Heads(branch="feat/x", heads={WORKTREE: "feat/y"}))
    assert not worktree_on_a_branch.lands_now(
        Context(ROOT).switched_to("main"), WORKTREE / "CLAUDE.md"
    )


def test_a_change_lands_now_in_a_file_that_is_in_no_checkout() -> None:
    guard = make_guard(Heads(branch="feat/x"))
    assert guard.lands_now(Context(ROOT), Path("/nowhere/notes.md"))
    assert guard.lands_now(Context(ROOT), HOME / "notes.md")


def test_the_branch_the_manifest_is_committed_on_is_a_default_branch() -> None:
    trunk = make_guard(Heads(branch="trunk", defaults=MAIN_BRANCHES | {"trunk"}))
    assert trunk.lands_now(Context(ROOT), ROOT / "CLAUDE.md")
    assert not make_guard(Heads(branch="trunk")).lands_now(Context(ROOT), ROOT / "CLAUDE.md")


@pytest.mark.parametrize(
    ("context", "on_a_branch", "on_main"),
    [
        (Context(ROOT).switched_to("main"), True, True),
        (Context(ROOT).switched_to("feat/y"), False, False),
        (Context(ROOT).claiming(["main"]), True, True),
        (Context(ROOT).claiming(["feat/y"]), False, True),
        (Context(ROOT).switched_to("feat/y").moved_to(ROOT / "app"), False, True),
        (Context(ROOT).switched_to("main").moved_to(ROOT / "app"), True, True),
        (Context(ROOT).switching_unseen().moved_to(ROOT / "app"), True, True),
    ],
    ids=[
        "a certain switch to main",
        "a certain switch to a branch",
        "a possible switch to main",
        "a possible switch to a branch",
        "a switch to a branch, then a cd",
        "a switch to main, then a cd",
        "a switch the guard cannot read, then a cd",
    ],
)
def test_a_switch_earlier_on_the_line_decides_where_a_change_lands(
    context: Context, *, on_a_branch: bool, on_main: bool
) -> None:
    path = ROOT / "CLAUDE.md"
    assert make_guard(FakeRepo(branch="feat/x")).lands_now(context, path) is on_a_branch
    assert make_guard(FakeRepo(branch="main")).lands_now(context, path) is on_main


@pytest.mark.parametrize(
    ("command", "repo_branch", "expected"),
    [
        ("git switch main && echo x > justfile", "feat/x", ["guard-path-default"]),
        ("git switch -c topic && echo x > justfile", "main", []),
        ("git switch main; echo x > justfile", "feat/x", ["guard-path-default"]),
        ("git switch topic; echo x > justfile", "feat/x", []),
        ("git switch topic; echo x > justfile", "main", ["guard-path-default"]),
        ("git switch - && echo x > justfile", "feat/x", ["guard-path-default"]),
        (
            "git switch main && echo x > .claude/settings.json",
            "feat/x",
            ["guard-path-write", "instructions-write"],
        ),
        (
            "git switch topic && echo x > .claude/settings.json",
            "main",
            ["guard-path-write", "instructions-write"],
        ),
        ("git checkout main -- && echo x > justfile", "feat/x", ["guard-path-default"]),
        ("git switch -- main && echo x > justfile", "feat/x", ["guard-path-default"]),
        ("git checkout main -- README.md && echo x > justfile", "feat/x", []),
        ("git checkout -- main && echo x > justfile", "feat/x", []),
    ],
)
def test_a_switch_on_the_same_line_moves_a_shell_write_between_branch_and_main(
    command: str, repo_branch: str, expected: list[str]
) -> None:
    assert rules(command, make_guard(FakeRepo(branch=repo_branch))) == expected


@pytest.mark.parametrize(
    "switch",
    [
        "git switch $B",
        'git switch "${B:-main}"',
        "git switch $(cat branch.txt)",
        "git switch `cat branch.txt`",
        "git switch -C $B",
        "for b in main; do git switch $b; done",
    ],
)
def test_a_switch_to_a_branch_the_guard_cannot_name_may_leave_a_shell_write_on_main(
    switch: str,
) -> None:
    assert rules(f"{switch} && echo x > justfile") == ["guard-path-default"]


@pytest.mark.parametrize(
    "switch",
    [
        "bash -c 'git switch main'",
        "eval git switch main",
        "bash -c 'git symbolic-ref HEAD refs/heads/main'",
        "eval git bisect reset main",
        "echo main | xargs git switch",
        "find . -maxdepth 0 -exec git checkout main ';'",
        "out=$(git switch main)",
        "echo $(sh -c 'git checkout main')",
        "git rebase -x 'git switch main' feat/x",
        "xargs -I{} git switch {} < branch.txt",
    ],
)
def test_a_switch_the_guard_cannot_read_may_leave_a_shell_write_on_main(switch: str) -> None:
    assert rules(f"{switch} && echo x > justfile") == ["guard-path-unseen-switch"]


def test_a_write_after_an_unseen_switch_names_the_switch_alone_as_the_way_out() -> None:
    [verdict] = verdicts("bash -c 'git switch main' && echo x > justfile")
    assert verdict.rule == "guard-path-unseen-switch"
    assert RULES[verdict.rule].way_out.startswith("Run the branch switch in a tool call of its own")
    on_main = verdicts("bash -c 'git status' && echo x > justfile", ON_MAIN)
    assert [verdict.rule for verdict in on_main] == ["guard-path-default"]


@pytest.mark.parametrize(
    "command",
    [
        "bash -c 'git status' && echo x > justfile",
        "echo git && echo x > justfile",
        "sudo git checkout -- main && echo x > justfile",
        "git rebase -x 'make test' feat/x && echo x > justfile",
    ],
)
def test_a_git_command_that_moves_no_head_leaves_a_shell_write_on_the_branch(command: str) -> None:
    assert rules(command) == []


def test_a_switch_in_another_checkout_counts_for_a_write_there() -> None:
    other = ".claude/worktrees/agent-1"
    guard = make_guard(Heads(branch="feat/x", heads={WORKTREE: "feat/y"}))
    switched = f"git -C {other} switch main && echo x > {other}/justfile"
    assert rules(switched, guard) == ["guard-path-default"]
    assert rules(f"echo x > {other}/justfile", guard) == []


@pytest.mark.parametrize(
    "command",
    ["echo x > quality/x.toml", "rm -rf formal", "git rm justfile", "git diff --output=justfile"],
)
def test_a_shell_write_to_an_unlocked_guard_path_is_refused_only_on_main(command: str) -> None:
    assert rules(command) == []
    assert rules(command, ON_MAIN) == ["guard-path-default"]


def test_a_shell_write_to_a_locked_guard_path_is_refused_on_any_branch() -> None:
    assert rules("echo x > .claude/settings.json") == ["guard-path-write", "instructions-write"]
    assert rules("echo x > .claude/settings.json", ON_MAIN) == [
        "guard-path-write",
        "instructions-write",
    ]


def test_cd_moves_later_writes() -> None:
    guard = make_guard()
    assert bash.check("cd /tmp && echo x > justfile", Context(ROOT), guard) == []
    assert rules("pushd config && rm lint.toml") == []
    assert rules("pushd config && rm lint.toml", ON_MAIN) == ["guard-path-default"]
    assert rules("pushd .claude && rm settings.json") == ["guard-path-write", "instructions-write"]
