import asyncio
import contextlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import first_hour_fakes as fakes
import pytest

from first_hour import harness, owner, reduce, steps
from first_hour.merges import Window

TESTS = Path(__file__).resolve().parent
BWRAP = fakes.bwrap_works()


@pytest.fixture(autouse=True)
def _own_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test's runtime directory sits in its own temp, not in the machine's."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


def make_run(tmp_path: Path, **budget: float) -> tuple[harness.Run, harness.Operator]:
    operator = fakes.operator(tmp_path)
    plugin = fakes.plugin(tmp_path / "checkout")
    run = harness.setup(fakes.config(plugin, tmp_path / "run", **budget), operator, os.environ)
    run.sandboxed = False
    run.env.update(fakes.GIT_ENV)
    return run, operator


def events(run: harness.Run) -> list[dict[str, object]]:
    return list(reduce.read(run.layout.root / "events.jsonl"))


def test_a_whole_run_follows_the_route_to_a_usable_merge(tmp_path: Path) -> None:
    run, operator = make_run(tmp_path)
    run.env["FAKE_ALSO"] = fakes.LIFE_EVENT
    models = fakes.Models()

    metrics = asyncio.run(harness.drive(run, operator, models))

    assert metrics["stop"]["reason"] == "usable-merged"
    assert [(m["branch"], m["how"]) for m in metrics["merges"]] == [
        ("feature/2-agent", "agent"),
        ("feature/1-save", "hand"),
    ]
    assert metrics["hand_merges"] == 1
    assert metrics["app"]["slice"] == "works"
    assert metrics["minutes"]["to_first_usable_merge"] is not None
    assert metrics["pastes"]["count"] == 1
    assert metrics["questions"]["by_channel"] == {"prose": 1, "also": 1}
    prose = next(q for q in metrics["questions"]["list"] if q["channel"] == "prose")
    assert prose["took_recommended"]
    assert prose["reason"] == "a build question"
    assert metrics["cost_usd"] == {"session": 1.5, "owner": 0.25, "sessions": 1}
    assert metrics["architecture"]["read"]
    assert not metrics["architecture"]["adr"]["found"]
    assert metrics["stack_leaks"]["count"] == 0
    assert metrics["integrity"]["unchanged"]
    assert metrics["integrity"]["changed"] == []
    assert metrics["integrity"]["claude_projects"] == []
    assert (run.layout.root / "declined").read_text() == "depended-on\n"
    assert (run.layout.root / "table.md").read_text().startswith("| Measure | Value |")
    assert models.sessions[0].closed
    logged = [e for e in events(run) if e["kind"] == "question" and e["channel"] == "prose"]
    assert logged[0]["read"] == "Built it. Shall I use blue? I recommend blue."


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
@pytest.mark.parametrize("where", ["session", "paste"])
@pytest.mark.parametrize("sandboxed", [False, True] if BWRAP else [False])
def test_a_signal_stops_the_run_ends_its_processes_and_writes_the_measurements(
    tmp_path: Path, *, signum: signal.Signals, where: str, sandboxed: bool
) -> None:
    ready = tmp_path / "run" / "app" / "READY"
    marker = f"{ready}-{where}"
    paste = ""
    if where == "paste":
        paste = (
            f'python3 -c \'import pathlib, time; pathlib.Path("{ready}").write_text("paste");'
            f" time.sleep(300)' {marker}"
        )
    plugin = fakes.plugin(tmp_path / "checkout")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(TESTS), str(TESTS.parent)])}
    argv = [sys.executable, str(TESTS / "first_hour_fakes.py"), str(plugin)]
    argv += [str(tmp_path / "run"), str(tmp_path), str(ready), "1" if sandboxed else "0", paste]
    process = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 60
    while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)
    assert ready.exists(), process.communicate()[0].decode()[-2000:]
    process.send_signal(signum)
    out, _ = process.communicate(timeout=60)

    assert process.returncode == 0, out.decode()[-2000:]
    metrics = json.loads((tmp_path / "run" / "metrics.json").read_text())
    assert metrics["stop"]["reason"] == "interrupted"
    assert metrics["stop"]["detail"] == signum.name
    assert metrics["integrity"]["stopped"] >= (1 if where == "paste" else 0)
    assert metrics["integrity"]["reaped"] == 0
    left = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True, check=False)
    assert left.stdout == ""


def test_the_operators_side_is_read_once_from_their_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    plugins = home / ".claude" / "plugins"
    plugins.mkdir(parents=True)
    (home / ".claude" / "rules").mkdir()
    (home / ".claude" / "rules" / "a.md").write_text("x")
    registry = {
        "plugins": {
            "pstack@pstack-claude": [{"version": "1", "installPath": "/x", "gitCommitSha": "c"}],
            "other@m": [{"version": "2", "installPath": "/y"}],
        }
    }
    (plugins / "installed_plugins.json").write_text(json.dumps(registry))
    (plugins / "known_marketplaces.json").write_text("not json")
    found = harness.Operator.read(home, {"uv": "/usr/bin/env", "just": None}, "c 1")
    assert [p.key for p in found.plugins] == ["pstack@pstack-claude"]
    assert found.plugins[0].source == {}
    assert found.tools == {"uv": Path("/usr/bin/env").resolve()}
    assert home / ".claude" / "rules" / "a.md" in found.watched
    assert found.projects == home / ".claude" / "projects"


def test_the_run_directory_must_be_new_or_empty_and_under_temp(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="must be under"):
        harness.checked_root(tmp_path / "x", tmp_path / "elsewhere")
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "f").write_text("x")
    with pytest.raises(SystemExit, match="not empty"):
        harness.checked_root(tmp_path / "full", tmp_path)
    assert harness.checked_root(tmp_path / "new", tmp_path) == (tmp_path / "new").resolve()


def test_a_command_past_its_time_or_after_a_halt_does_not_run(tmp_path: Path) -> None:
    run, _ = make_run(tmp_path)
    with pytest.raises(subprocess.TimeoutExpired):
        run.call(["sleep", "30"], cwd=tmp_path, timeout=0.2)
    assert run.children == set()
    assert run.halt() == 0
    with pytest.raises(harness.Deadline):
        run.call(["true"], cwd=tmp_path, timeout=5)


def test_the_wall_clock_bounds_every_wait(tmp_path: Path) -> None:
    run, _ = make_run(tmp_path, minutes=0.0)
    with pytest.raises(harness.Deadline):
        run.left()
    run2, _ = make_run(tmp_path / "2", minutes=0.01)

    async def slow() -> None:
        await asyncio.sleep(5)

    with pytest.raises(harness.Deadline):
        asyncio.run(harness.bounded(run2, slow()))


def test_a_sandboxed_run_wraps_every_command(tmp_path: Path) -> None:
    run, _ = make_run(tmp_path)
    assert run.wrap(["true"]) == ["true"]
    run.sandboxed = True
    wrapped = run.wrap(["true"])
    assert wrapped[0] == "bwrap"
    assert wrapped[-2:] == ["--", "true"]


@dataclass
class Scripted:
    """A session whose turns raise what a test names."""

    raises: type[Exception]
    costs: dict[str, float]

    async def turn(self, text: str, deadline: float) -> str:
        raise self.raises

    async def close(self) -> None:
        raise RuntimeError


@pytest.mark.parametrize(
    ("raises", "reason"),
    [
        (harness.OverBudget, "cost"),
        (harness.Deadline, "wall-clock"),
        (ValueError, "error"),
    ],
)
def test_a_budget_or_a_failure_inside_a_step_stops_the_run(
    tmp_path: Path, *, raises: type[Exception], reason: str
) -> None:
    run, operator = make_run(tmp_path)

    class Models(fakes.Models):
        async def session(self, run: harness.Run, owner_: harness.OwnerModel) -> Scripted:
            return Scripted(raises, {})

    metrics = asyncio.run(harness.drive(run, operator, Models()))
    assert metrics["stop"]["reason"] == reason


def test_a_route_that_fails_stops_the_run_with_its_output(tmp_path: Path) -> None:
    run, operator = make_run(tmp_path)
    (run.layout.pinned / "scripts" / "route.py").write_text("print('boom'); raise SystemExit(3)")
    metrics = asyncio.run(harness.drive(run, operator, fakes.Models()))
    assert metrics["stop"]["reason"] == "error"
    assert "exit 3" in metrics["stop"]["detail"]


def driver(run: harness.Run) -> harness.Driver:
    return harness.Driver(run, fakes.Owner(run), fakes.Session(run))


def test_a_forge_stage_stops_the_run(tmp_path: Path) -> None:
    run, _ = make_run(tmp_path)
    route = (
        'import json; print(json.dumps({"command": "open the pull request", "stage": "review"}))'
    )
    (run.layout.pinned / "scripts" / "route.py").write_text(route)
    assert asyncio.run(driver(run).loop()) == (steps.Stop.FORGE, "open the pull request")


def test_a_pasted_command_past_the_budget_is_a_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _ = make_run(tmp_path)
    monkeypatch.setattr(harness, "PASTE_TIMEOUT_S", 0.2)
    one = driver(run)
    with pytest.raises(harness.Deadline):
        one.paste(["sleep 30"], "route")
    assert one.windows[0].command == "sleep 30"
    assert any(e["kind"] == "paste-timeout" for e in events(run))


def test_a_paste_follows_its_next_lines_once_and_reruns_a_red_gate(tmp_path: Path) -> None:
    run, _ = make_run(tmp_path)
    one = driver(run)
    red = "printf 'parallel: lint: red (exit 1) in 0.1s\\nnext: `just check` to see\\n'"
    one.paste([red], "route")
    pastes = [e for e in events(run) if e["kind"] == "paste"]
    assert [p["command"] for p in pastes] == [red, "just check"]
    runs = [e for e in events(run) if e["kind"] == "gates"]
    assert [r["attempt"] for r in runs] == [1, 2]


def test_merges_are_logged_even_when_the_step_is_cut_off(tmp_path: Path) -> None:
    run, _ = make_run(tmp_path)
    one = driver(run)
    asyncio.run(one.session.turn("/ratchet:init", run.deadline))

    class Cut(fakes.Session):
        async def turn(self, text: str, deadline: float) -> str:
            raise harness.Deadline

    one.session = Cut(run)
    with pytest.raises(harness.Deadline):
        asyncio.run(one.step(steps.Move(steps.Action.ASK, "do it")))
    merged = [e for e in events(run) if e["kind"] == "merge"]
    assert [m["branch"] for m in merged] == ["feature/2-agent"]
    assert merged[0]["how"] == "agent"


def test_an_app_check_with_no_branch_or_no_checkout_says_so(tmp_path: Path) -> None:
    run, _ = make_run(tmp_path)
    one = driver(run)
    assert one.app_check("x") is None
    assert one.record_merges() == []
    asyncio.run(one.session.turn("/ratchet:init", run.deadline))
    (run.layout.appcheck / "taken").mkdir()
    (run.layout.appcheck / "taken" / "f").write_text("x")
    assert one.app_check("taken") is None
    assert any("could not check out" in str(e.get("finding")) for e in events(run))


def test_the_drains_leftover_work_trees_are_removed_and_claude_projects_named(
    tmp_path: Path,
) -> None:
    run, _ = make_run(tmp_path)
    one = driver(run)
    asyncio.run(one.session.turn("/ratchet:init", run.deadline))
    queue = run.layout.tmp / "ratchet-queue-1" / "app-1"
    fakes.git(run.layout.app, "worktree", "add", "-q", "--detach", str(queue), "main")
    projects = tmp_path / "projects"
    (projects / (harness.slug(run.layout.root) + "-app")).mkdir(parents=True)
    (projects / "-other").mkdir()
    assert run.layout.runtime.is_dir()
    left = harness.tidy(run, projects)
    assert not run.layout.runtime.exists()
    assert left["removed_work_trees"] == [str(queue)]
    assert not queue.exists()
    assert left["claude_projects"] == [str(projects / (harness.slug(run.layout.root) + "-app"))]


def test_an_sdk_message_is_kept_whole_and_its_events_logged(tmp_path: Path) -> None:
    @dataclass
    class ResultMessage:
        session_id: str
        total_cost_usd: float
        extra: object

    log = harness.Log(tmp_path)
    log.message(ResultMessage("s", 1.0, {"k": (1, None, object)}))
    log.message("not a dataclass")
    kept = [json.loads(line) for line in (tmp_path / "transcript.jsonl").read_text().splitlines()]
    assert kept[0]["msg"]["_type"] == "ResultMessage"
    assert kept[0]["msg"]["extra"]["k"][0] == 1
    assert kept[1]["msg"] == {"value": "not a dataclass"}
    assert [e["kind"] for e in reduce.read(tmp_path / "events.jsonl")] == ["result"]


def test_a_stray_cancel_is_not_taken_for_an_interrupt(tmp_path: Path) -> None:
    run, operator = make_run(tmp_path)

    class Cancelled(fakes.Models):
        async def session(self, run: harness.Run, owner_: harness.OwnerModel) -> fakes.Session:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(harness.drive(run, operator, Cancelled()))


def test_a_reaped_process_is_one_whose_directory_is_in_the_run(tmp_path: Path) -> None:
    inside = tmp_path / "inside"
    inside.mkdir()
    process = subprocess.Popen(["sleep", "30"], cwd=inside)
    assert harness.reap([tmp_path]) >= 1
    process.wait(timeout=10)
    assert harness.reap([tmp_path / "nothing-runs-here"]) == 0


def test_owner_answers_are_logged_with_what_the_owner_read() -> None:
    reply = owner.Reply(
        needs_reply=True, kind=owner.Kind.PRODUCT, text="Keep it local.", reason="a firm position"
    )
    event = owner.prose_question("x" * 7000 + " I recommend a remote?", reply)
    assert len(str(event["read"])) == owner.TURN_TAIL
    assert event["recommended"] is not None
    assert event["took_recommended"] is False
    assert Window("a", 0, 1).command == "a"


def test_a_yes_to_a_life_event_types_its_safeguard_command(tmp_path: Path) -> None:
    run, operator = make_run(tmp_path, steps=3)
    run.env["FAKE_ALSO"] = fakes.LIFE_EVENT
    models = fakes.Models(pick="Yes, it is")
    asyncio.run(harness.drive(run, operator, models))
    assert "/ratchet:safeguard depended-on" in models.sessions[0].typed
    assert not (run.layout.root / "declined").exists()


def test_the_owner_answers_at_most_three_questions_a_turn(tmp_path: Path) -> None:
    run, _ = make_run(tmp_path)

    class Asking(fakes.Session):
        async def turn(self, text: str, deadline: float) -> str:
            self.typed.append(text)
            return "Which colour?"

    session = Asking(run)
    one = harness.Driver(run, fakes.Owner(run, replies=99), session)
    asyncio.run(one.converse("/ratchet:init"))
    assert session.typed == ["/ratchet:init", *[owner.GO_AHEAD] * harness.PROSE_REPLIES]


def test_a_red_gate_in_a_merge_is_not_run_again(tmp_path: Path) -> None:
    run, _ = make_run(tmp_path)
    red = "printf 'parallel: lint: red (exit 1) in 0.1s\\n' # merge"
    driver(run).paste([red], "route")
    assert [r["attempt"] for r in events(run) if r["kind"] == "gates"] == [1]


def test_the_app_check_starts_the_app_with_the_projects_installed_packages(
    tmp_path: Path,
) -> None:
    run, _ = make_run(tmp_path)
    one = driver(run)
    asyncio.run(one.session.turn("/ratchet:init", run.deadline))
    app = run.layout.app
    start = "python3 -c \"import os; os.stat('node_modules'); exec(open('server.py').read())\""
    (app / "README.md").write_text(f"```\n{start}\n```\n")
    fakes.git(app, "commit", "-q", "-am", "needs its packages")
    first = one.app_check("without")
    (app / "node_modules").mkdir()
    second = one.app_check("with")
    assert first is not None
    assert not first.started
    assert second is not None
    assert second.started


def test_a_second_signal_while_the_run_stops_changes_nothing() -> None:
    async def scenario() -> tuple[str, bool]:
        work = asyncio.ensure_future(asyncio.sleep(10, result=(steps.Stop.ERROR, "", None)))
        interrupt = harness.Interrupt(work)  # type: ignore[arg-type]
        interrupt(signal.SIGTERM)
        interrupt(signal.SIGINT)
        with contextlib.suppress(asyncio.CancelledError):
            await work
        return interrupt.name, work.cancelled()

    assert asyncio.run(scenario()) == ("SIGTERM", True)


def test_a_finished_run_logs_what_it_did_about_the_architecture_and_any_stack_leak(
    tmp_path: Path,
) -> None:
    run, _ = make_run(tmp_path)
    one = driver(run)
    asyncio.run(one.session.turn("/ratchet:init", run.deadline))
    app = run.layout.app
    (app / "docs" / "adr").mkdir(parents=True)
    (app / "docs" / "process").mkdir(parents=True)
    (app / "docs" / "adr" / "0002-system.md").write_text(
        "## Decision\n\nx\n\n- Chosen by: the owner\n\n## Considered options\n\n- a\n- b\n\n"
        "## Reversal cost\n\nsmall\n\n## Refutation criterion\n\nslow\n",
        encoding="utf-8",
    )
    (app / "docs" / "process" / "binding.md").write_text(
        "## Architecture\n\n- Decision: `docs/adr/0002-system.md`\n\n"
        "| Part | Path | May depend on |\n| --- | --- | --- |\n| core | `src/**` | nothing |\n",
        encoding="utf-8",
    )
    fakes.git(app, "add", "-A")
    fakes.git(app, "commit", "-q", "-m", "docs")
    transcript = {
        "type": "SystemMessage",
        "msg": {"subtype": "hook_response", "data": {"output": "cargo"}},
    }
    with (run.layout.root / "transcript.jsonl").open("a", encoding="utf-8") as out:
        out.write(json.dumps(transcript) + "\n")
    harness.record_audit(run, one)
    found = {e["kind"]: e for e in events(run)}
    assert found["architecture"]["adr"]["complete"]
    assert len(found["architecture"]["binding"]["parts"]) == 1
    assert found["stack-leaks"]["names"] == ["Rust"]
    metrics = reduce.reduce(events(run))
    assert metrics["architecture"]["parts_declared"] == 1
    assert metrics["stack_leaks"]["count"] == 1


def test_a_run_that_never_reached_a_default_branch_logs_only_the_leak_reading(
    tmp_path: Path,
) -> None:
    run, _ = make_run(tmp_path)
    harness.record_audit(run, None)
    assert [e["kind"] for e in events(run) if e["kind"] in ("architecture", "stack-leaks")] == [
        "stack-leaks"
    ]
    harness.record_audit(run, driver(run))
    assert not any(e["kind"] == "architecture" for e in events(run))
