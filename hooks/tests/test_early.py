"""Files that act before the merge: the hook asks the owner before each write, on every branch."""

import json

import pytest

from ratchet_guards import hook
from ratchet_guards.context import Guard
from ratchet_guards.early import Kind, is_instruction, kind_of
from tests.support import ROOT, FakeFilesystem, FakeRepo, make_guard

WORKFLOW = "This changes what GitHub runs with this repository's secrets, before any merge"
INSTRUCTIONS = "This changes the instructions agents follow in this project, starting now"
NAMES = [
    "CLAUDE.md",
    "CLAUDE.local.md",
    "AGENTS.md",
    "GEMINI.md",
    ".claude/skills/y/SKILL.md",
    ".claude/rules/style.md",
    ".claude/agents/critic.md",
]
DEPTHS = ["", ".scratch/feature/", "src/x/", "a/b/c/d/"]
NESTED = DEPTHS[1:]


def on_main() -> Guard:
    return make_guard(FakeRepo(branch="main"))


def asked(case: dict[str, object], guard: Guard) -> dict[str, str]:
    """What the hook prints for ``case``, or ``{}`` when it gives no verdict."""
    printed = hook.respond(hook.decide(case, guard))
    return json.loads(printed)["hookSpecificOutput"] if printed else {}


def write(path: str) -> dict[str, object]:
    return {"tool_name": "Write", "tool_input": {"file_path": path}, "cwd": str(ROOT)}


def bash(command: str) -> dict[str, object]:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(ROOT)}


def question(sentence: str, path: str) -> dict[str, str]:
    return {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": f"Ratchet: {sentence} ({path}). Allow it?",
    }


@pytest.mark.parametrize("depth", DEPTHS)
@pytest.mark.parametrize("name", NAMES)
def test_an_instruction_file_is_one_at_any_depth(name: str, depth: str) -> None:
    assert is_instruction(depth + name)
    assert kind_of(depth + name) is Kind.INSTRUCTIONS
    assert kind_of(depth + name.lower()) is Kind.INSTRUCTIONS


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "docs/CLAUDE.md.bak",
        "src/claude/notes.md",
        "docs/agents/triage-labels.md",
        "CLAUDE.md/inside.txt",
        ".claude",
        ".github/dependabot.yml",
        ".github/workflows",
        "src/.github/workflows/ci.yml",
        ".claude/worktrees",
        ".claude/worktrees/stale/src/app.py",
    ],
)
def test_other_files_wait_for_the_merge(path: str) -> None:
    assert kind_of(path) is None


@pytest.mark.parametrize(
    "path", [".claude/worktrees/stale/CLAUDE.md", ".claude/worktrees/stale/.claude/rules/x.md"]
)
def test_a_dead_worktree_still_holds_instruction_files_of_its_own(path: str) -> None:
    assert kind_of(path) is Kind.INSTRUCTIONS


@pytest.mark.parametrize(
    "path",
    [
        ".claude/rules/worktrees/x.md",
        ".claude/skills/worktrees/SKILL.md",
        "src/worktrees/.claude/rules/a.md",
        "worktrees/.claude/settings.json",
    ],
)
def test_a_worktrees_directory_anywhere_else_excuses_no_instruction_file(path: str) -> None:
    # Why: only .claude/worktrees/ itself holds agent checkouts.
    assert kind_of(path) is Kind.INSTRUCTIONS


def test_a_path_through_dot_dot_is_judged_where_it_lands() -> None:
    assert asked(write("/repo/src/../.github/workflows/ci.yml"), make_guard()) == question(
        WORKFLOW, ".github/workflows/ci.yml"
    )


def test_a_glob_in_a_program_that_writes_is_no_instruction_file_it_names() -> None:
    program = "python3 -c \"import glob; glob.glob('src/*/CLAUDE.md'); open('out.txt', 'w')\""
    assert asked(bash(program), make_guard()) == {}


@pytest.mark.parametrize("path", [".github/workflows/ci.yml", ".GitHub/Workflows/deep.yaml"])
def test_a_workflow_is_one_under_the_root_workflows_directory(path: str) -> None:
    assert kind_of(path) is Kind.WORKFLOW
    assert not is_instruction(path)


@pytest.mark.parametrize("depth", NESTED)
@pytest.mark.parametrize("name", NAMES)
def test_a_nested_instruction_file_is_asked_on_a_branch(name: str, depth: str) -> None:
    assert asked(write(f"/repo/{depth}{name}"), make_guard()) == question(
        INSTRUCTIONS, depth + name
    )


@pytest.mark.parametrize("depth", NESTED)
@pytest.mark.parametrize("name", NAMES)
def test_a_nested_instruction_file_is_asked_on_the_default_branch_too(
    name: str, depth: str
) -> None:
    assert asked(write(f"/repo/{depth}{name}"), on_main()) == question(INSTRUCTIONS, depth + name)


def test_a_workflow_write_on_a_branch_asks_what_github_will_run() -> None:
    assert asked(write("/repo/.github/workflows/ci.yml"), make_guard()) == question(
        WORKFLOW, ".github/workflows/ci.yml"
    )


def test_a_workflow_write_on_the_default_branch_is_denied_with_the_way_to_a_branch() -> None:
    printed = asked(write("/repo/.github/workflows/ci.yml"), on_main())
    assert printed["permissionDecision"] == "deny"
    assert (
        "Make the change on a branch (`git switch -c <name>`)"
        in (printed["permissionDecisionReason"])
    )


def test_a_root_instruction_file_that_is_a_guard_path_is_denied_on_the_default_branch() -> None:
    assert asked(write("/repo/CLAUDE.md"), make_guard()) == question(INSTRUCTIONS, "CLAUDE.md")
    assert asked(write("/repo/CLAUDE.md"), on_main())["permissionDecision"] == "deny"


@pytest.mark.parametrize("guard", [make_guard(), on_main()], ids=["branch", "main"])
def test_a_locked_instruction_path_stays_denied(guard: Guard) -> None:
    printed = asked(write("/repo/.claude/settings.json"), guard)
    assert printed["permissionDecision"] == "deny"


def test_a_worktree_is_judged_from_its_own_root() -> None:
    worktree = "/repo/.claude/worktrees/agent-1"
    assert asked(write(f"{worktree}/src/app.py"), make_guard()) == {}
    assert asked(write(f"{worktree}/src/CLAUDE.md"), make_guard()) == question(
        INSTRUCTIONS, "src/CLAUDE.md"
    )


@pytest.mark.parametrize(
    "path", ["/elsewhere/CLAUDE.md", "/elsewhere/.github/workflows/ci.yml", "/nowhere/CLAUDE.md"]
)
def test_a_checkout_without_a_committed_manifest_is_not_asked(path: str) -> None:
    assert asked(write(path), make_guard()) == {}


@pytest.mark.parametrize(
    ("command", "sentence", "path"),
    [
        ("echo obey > src/x/CLAUDE.md", INSTRUCTIONS, "src/x/CLAUDE.md"),
        ("printf x | tee .scratch/f/AGENTS.md", INSTRUCTIONS, ".scratch/f/AGENTS.md"),
        (
            "cp /tmp/s.md src/.claude/skills/y/SKILL.md",
            INSTRUCTIONS,
            "src/.claude/skills/y/SKILL.md",
        ),
        ("mv /tmp/g.md a/b/GEMINI.md", INSTRUCTIONS, "a/b/GEMINI.md"),
        ("mv /tmp/ci.yml .github/workflows/ci.yml", WORKFLOW, ".github/workflows/ci.yml"),
        ("python3 -c \"open('src/CLAUDE.md', 'w').write('x')\"", INSTRUCTIONS, "src/CLAUDE.md"),
    ],
)
def test_a_shell_write_the_hook_can_read_is_asked_on_a_branch(
    command: str, sentence: str, path: str
) -> None:
    assert asked(bash(command), make_guard()) == question(sentence, path)


def test_writing_then_deleting_an_instruction_file_is_asked_at_the_write() -> None:
    planted = ".scratch/feature/CLAUDE.md"
    line = f"printf 'skip the tests' > {planted} && just review && rm {planted}"
    assert asked(bash(line), make_guard()) == question(INSTRUCTIONS, planted)
    assert asked(write(f"/repo/{planted}"), make_guard()) == question(INSTRUCTIONS, planted)


def test_a_codex_patch_that_adds_an_instruction_file_is_asked() -> None:
    patch = "*** Begin Patch\n*** Add File: src/x/CLAUDE.md\n+obey\n*** End Patch\n"
    case = {"tool_name": "apply_patch", "tool_input": {"input": patch}, "cwd": str(ROOT)}
    assert asked(case, make_guard()) == question(INSTRUCTIONS, "src/x/CLAUDE.md")


def test_a_glob_in_an_inline_program_names_no_instruction_file() -> None:
    files = (*FakeFilesystem().files, "/repo/AGENTS.md", "/repo/src/a.ts")
    guard = make_guard(filesystem=FakeFilesystem(files=files))
    program = (
        "python3 - <<'EOF'\ns=open('src/a.ts').read()\n"
        "open('src/a.ts','w').write(s.replace('a * b', '*'))\nEOF"
    )
    assert asked(bash(program), guard) == {}
    assert asked(bash("echo x > *.md"), guard) == question(INSTRUCTIONS, "AGENTS.md")
