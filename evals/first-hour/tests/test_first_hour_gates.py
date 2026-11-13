from pathlib import Path

from first_hour import gates, safety
from first_hour.gates import Gate, Status

RUN = """\
parallel: fmt-check: started
==> lint <==
lint: no TypeScript in the tree yet, so there is nothing to judge
parallel: lint: green in 0.1s
==> imports <==
later: code:layer
parallel: imports: green in 0.1s
==> bite-all <==
bite: smoke: a service that drops requests in flight when stopped: RED ON CLEAN STATE
parallel: bite-all: red (exit 1) in 23.0s
later: printed after the section closed
parallel: 3 of 3 gates finished in 23.1s
parallel: red
"""


def test_each_gate_reads_as_green_red_or_waiting_for_its_trigger() -> None:
    assert gates.parse(RUN) == [
        Gate("lint", Status.GREEN),
        Gate("imports", Status.LATER, "code:layer"),
        Gate("bite-all", Status.RED),
    ]
    assert gates.red(RUN) == ["bite-all"]


NEWER = """\
parallel: imports: judged nothing (later: code:layer) in 0.1s
parallel: load: judged nothing (none: not a service or a job) in 0.1s
parallel: lint: green in 2.0s
"""


def test_a_newer_runner_says_judged_nothing_and_why() -> None:
    assert gates.parse(NEWER) == [
        Gate("imports", Status.LATER, "code:layer"),
        Gate("load", Status.NONE, "not a service or a job"),
        Gate("lint", Status.GREEN),
    ]


def test_output_with_no_runner_lines_has_no_gates() -> None:
    assert gates.parse("later: code:layer\nmerge: owner\n") == []


def test_commands_that_reach_a_remote_or_change_the_users_plugins_are_refused() -> None:
    for command in (
        "git add . && git push origin main",
        "git remote add origin git@github.com:me/app.git",
        "gh repo create app --private --source . --push",
        "glab mr create",
        "claude plugin install foo@bar",
        "claude mcp add x",
        "claude --dangerously-skip-permissions -p hi",
        "echo '{}' > ~/.claude/settings.json",
        "sed -i s/a/b/ /home/me/.claude/plugins/installed_plugins.json",
        "git -C . push",
        "git -c user.name=x remote add origin ../elsewhere",
        "git config remote.origin.url git@github.com:me/app.git",
        "echo 'be terse' >> ~/.claude/CLAUDE.md",
        "cd ~/.claude && sed -i s/a/b/ settings.json",
        "cat ~/.claude/settings.json | jq . | tee ~/.claude/settings.json",
    ):
        assert safety.refusal(command), command


def test_reading_forge_tools_and_user_files_is_not_refused() -> None:
    for command in (
        "git init -b main && claude plugin list; gh auth status; command -v glab",
        "cat ~/.claude/settings.json",
        "git remote -v",
        "git log --oneline | grep push",
        "git rev-parse --show-toplevel; gh auth status 2>&1 | head -5; ls ~/.claude/rules/",
        "cat ~/.claude/settings.json > /dev/null && echo ok > notes.txt",
        "git commit -m 'save the link on push of the button'",
        "python3 -c 'print(1)' > out.txt",
    ):
        assert safety.refusal(command) is None, command


def test_the_owner_declines_writes_into_a_protected_tree_but_not_the_products_work_trees(
    tmp_path: Path,
) -> None:
    home, root = tmp_path / "home", tmp_path / "home" / "run"

    def forbidden(path: Path) -> bool:
        return safety.forbidden_write(str(path), root=root, protected=(home, root / "plugin"))

    assert not forbidden(root / "app" / "x.ts")
    assert not forbidden(tmp_path / "ratchet-queue-1001" / "app-1" / "x.ts")
    assert forbidden(home / ".claude" / "settings.json")
    assert forbidden(home)
    assert forbidden(root / "plugin" / "scripts" / "route.py")
