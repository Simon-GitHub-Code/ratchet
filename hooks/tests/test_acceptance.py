"""The acceptance harness end to end: real boots, a real proxy, real records and real signals."""

import datetime as dt
import fcntl
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

import acceptance
import gatelib
from tests import products
from tests.servers import SERVE, gone, pid_of, python

SLUG = "save-link"
SCRIPT = Path(acceptance.__file__)
KEPT_ONLY_STATUS = {
    "id": "K9",
    "criterion": "C1",
    "via": "http",
    "steps": [{"request": "GET /", "expect": {"status": 200}}],
}


def run(root: Path, *args: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, list[str]]:
    code = acceptance.main(["acceptance.py", *args, "--root", str(root)])
    return code, capsys.readouterr().out.splitlines()


def up(root: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, list[str]]:
    return run(root, "up", "--slug", SLUG, "--ticket", str(root / "ticket.md"), capsys=capsys)


def down(root: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, list[str]]:
    return run(root, "down", "--slug", SLUG, capsys=capsys)


def check(root: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, list[str]]:
    return run(root, "check", "--slug", SLUG, capsys=capsys)


def session(root: Path) -> Path:
    return acceptance.latest(root, SLUG)


def write_record(directory: Path, record: dict[str, object]) -> None:
    (directory / "tester" / "record.json").write_text(json.dumps(record), encoding="utf-8")


@pytest.fixture
def product(tmp_path: Path) -> Iterator[Path]:
    """A product to try; whatever a test left running of it is stopped at the end."""
    root = products.make(tmp_path / "product", env=f"SLOW={tmp_path / 'slow'}")
    yield root
    acceptance.main(["acceptance.py", "down", "--slug", SLUG, "--root", str(root)])
    assert products.processes_naming(str(tmp_path)) == []


@pytest.fixture
def tried(product: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    """A session that was tried as the good record says, ended, with that record written."""
    code, (url, _) = up(product, capsys)
    assert code == 0
    products.tried(url)
    assert down(product, capsys)[0] == 0
    directory = session(product)
    write_record(directory, products.good_record(directory))
    return directory


def test_up_puts_the_proxy_before_the_head_and_down_stops_all_of_it(
    product: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, (url, tester) = up(product, capsys)
    assert code == 0
    directory = session(product)
    assert Path(tester) == directory / "tester"
    assert directory.is_relative_to(product / ".git" / "ratchet" / "acceptance" / SLUG)
    facts = json.loads((directory / "session.json").read_text(encoding="utf-8"))
    head = products.repos.git(product, "rev-parse", "HEAD")
    assert (facts["slug"], facts["head"], facts["types"]) == (SLUG, head, ["service", "web-ui"])
    assert facts["tree"] == products.repos.git(product, "rev-parse", "HEAD^{tree}")
    assert products.request(url)[0] == 200
    assert products.request(url, path="/flag") == (200, b"")
    proxy = json.loads((directory / "proxy.json").read_text(encoding="utf-8"))
    supervisor = pid_of(directory / "live.lock")
    assert acceptance.state(directory) is acceptance.State.LIVE
    assert down(product, capsys) == (0, ["acceptance down: stopped 1"])
    assert gone(supervisor)
    assert gone(proxy["pid"])
    assert not gatelib.listening(proxy["url"])
    assert not gatelib.listening(proxy["upstream"])
    seal = json.loads((directory / "seal.json").read_text(encoding="utf-8"))
    assert (seal["session"], seal["requests"]) == (facts["session"], 2)
    assert acceptance.state(directory) is acceptance.State.ENDED
    assert down(product, capsys) == (0, ["acceptance down: nothing was running"])


def test_up_again_while_it_runs_gives_the_same_address(
    product: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = up(product, capsys)
    assert up(product, capsys) == first
    assert len(acceptance.sessions(acceptance.store(product) / SLUG, 2)) == 1


def test_two_ups_at_once_share_one_session(product: Path) -> None:
    argv = [sys.executable, str(SCRIPT), "up", "--slug", SLUG, "--ticket", "ticket.md"]
    started = [
        subprocess.Popen(argv, cwd=product, stdout=subprocess.PIPE, text=True)  # noqa: S603 # -- this interpreter and the harness
        for _ in range(2)
    ]
    said = [process.communicate(timeout=120) for process in started]
    assert [process.returncode for process in started] == [0, 0]
    assert said[0] == said[1]
    assert len(acceptance.sessions(acceptance.store(product) / SLUG, 2)) == 1


def test_the_flags_due_for_removal_are_on_in_the_boot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = products.make(tmp_path / "product", extra='flags_on := "FLAG=$(echo on)"')
    try:
        code, (url, _) = up(root, capsys)
        assert code == 0
        assert products.request(url, path="/flag") == (200, b"on")
    finally:
        down(root, capsys)
    assert json.loads((session(root) / "session.json").read_text(encoding="utf-8"))["env"] == [
        "FLAG=on"
    ]


@pytest.mark.parametrize(
    ("extra", "seconds"),
    [('boot_timeout := "7"', 7.0), ('boot_timeout := "soon"', 30.0), ('flags_on := "none"', 30.0)],
)
def test_the_boot_wait_and_flags_come_from_the_justfile(
    tmp_path: Path, extra: str, seconds: float
) -> None:
    binding = acceptance.read_binding(products.make(tmp_path / "product", extra=extra))
    assert binding.boot is not None
    assert (binding.boot.timeout, binding.boot.env) == (seconds, ())


@pytest.mark.parametrize(
    ("boot", "ready", "said"),
    [
        ("none", "http://127.0.0.1:{port}/", "its justfile sets `boot` to none"),
        ("bind: how it starts", "http://127.0.0.1:{port}/", "`boot` still holds the template"),
        ("exec true", "none", "its justfile sets `ready` to none"),
    ],
)
def test_a_product_that_gives_no_way_to_start_gets_a_blocker_and_no_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], *, boot: str, ready: str, said: str
) -> None:
    root = products.make(tmp_path / "product", boot=boot, ready=ready)
    code, out = up(root, capsys)
    directory = session(root)
    assert code == 1
    assert said in out[0]
    assert out[1] == f"acceptance up: cannot start; the record is {directory / acceptance.RECORD}"
    record = json.loads((directory / acceptance.RECORD).read_text(encoding="utf-8"))
    assert record["author"] == "harness"
    assert [c["verdict"] for c in record["criteria"]] == ["unsure"] * 3
    assert check(root, capsys) == (0, ["acceptance check: green"])
    assert run(root, "summary", "--slug", SLUG, capsys=capsys)[0] == 0
    summary = json.loads((root / "docs/features" / SLUG / "acceptance.json").read_text("utf-8"))
    assert (summary["blocking"], summary["requests"], summary["log"]) == (["F1"], 0, None)


def test_a_justfile_with_no_boot_gives_no_way_to_start(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = products.make(tmp_path / "product")
    (root / "justfile").write_text('ready := "http://127.0.0.1:{port}/"\n', encoding="utf-8")
    code, out = up(root, capsys)
    assert code == 1
    assert out[0] == "The project gives no way to start it: its justfile sets no `boot`."


@pytest.mark.parametrize(
    ("boot", "extra", "said"),
    [
        (
            python("print('Error: dist/server.js is missing'); raise SystemExit(1)"),
            "",
            "it exited with code 1, and its first line was: Error: dist/server.js is missing",
        ),
        (
            python("print('listening soon', flush=True); import time; time.sleep(60)"),
            'boot_timeout := "1"',
            "it gave no answer within 1s, and its first line was: listening soon",
        ),
        ("exec true", "", "it exited with code 0, and printed nothing"),
    ],
)
def test_a_boot_that_does_not_answer_is_a_blocker_that_says_what_it_did(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], *, boot: str, extra: str, said: str
) -> None:
    root = products.make(tmp_path / "product", boot=boot, extra=extra)
    code, out = up(root, capsys)
    assert code == 1
    assert out[0] == f"It was started with `{boot}` and did not answer: {said}"
    record = json.loads((session(root) / acceptance.RECORD).read_text(encoding="utf-8"))
    assert record["findings"][0]["actual"] == out[0]
    assert products.processes_naming(str(tmp_path)) == []


@pytest.mark.parametrize(
    ("change", "said"),
    [
        (
            lambda r: (r / "docs/process/binding.md").write_text(
                "- Application types: service, web-ui, cli\n", encoding="utf-8"
            ),
            (
                "acceptance up: the harness tries a service and a web-ui; this project is also"
                " cli, which a later version will try: nothing was started"
            ),
        ),
        (
            lambda r: (r / "ticket.md").write_text("# 01\n\nNo boxes.\n", encoding="utf-8"),
            "has no checklist, so there is nothing to accept",
        ),
        (lambda r: (r / "ticket.md").unlink(), "has no checklist, so there is nothing to accept"),
        (lambda r: (r / "justfile").unlink(), "reads boot and ready from the justfile, and"),
        (
            lambda r: (r / "docs/process/binding.md").write_text("# B\n", encoding="utf-8"),
            "declares no `- Application types:` line",
        ),
        (lambda r: shutil.rmtree(r / ".git"), "is not a git work tree with a commit"),
    ],
)
def test_a_harness_that_cannot_run_here_says_why_and_starts_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], *, change: object, said: str
) -> None:
    root = products.make(tmp_path / "product")
    change(root)  # type: ignore[operator]
    code, out = up(root, capsys)
    assert code == 2
    assert said in out[-1]
    assert not (root / ".git" / "ratchet").exists()


def test_without_just_the_harness_cannot_read_the_boot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = products.make(tmp_path / "product")
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    code, out = up(root, capsys)
    assert code == 2
    assert "the harness reads boot and ready with `just`, which is not here" in out[0]


def test_a_slug_is_lower_case_words(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as refused:
        run(tmp_path, "check", "--slug", "Save Link", capsys=capsys)
    assert refused.value.code == 2
    assert "is not lower-case letters, digits and hyphens" in capsys.readouterr().err


def test_check_accepts_a_record_held_to_what_was_logged(
    tried: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tried.parents[5]
    assert check(root, capsys) == (0, ["acceptance check: green"])


def test_check_refuses_a_session_still_running_and_one_never_begun(
    product: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = check(product, capsys)
    assert code == 2
    assert out[0].startswith(f"acceptance check: no acceptance session for {SLUG} at ")
    up(product, capsys)
    assert check(product, capsys) == (
        1,
        ["the session is still running: run `down` first", "acceptance check: red"],
    )


def edit_record(directory: Path, change: Callable[[dict[str, Any]], object]) -> None:
    record = json.loads((directory / acceptance.RECORD).read_text(encoding="utf-8"))
    change(record)
    write_record(directory, record)


def edit_lines(path: Path, change: Callable[[list[bytes]], list[bytes]]) -> None:
    path.write_bytes(b"".join(change(path.read_bytes().splitlines(keepends=True))))


def trim_ticket(directory: Path) -> None:
    ticket = directory / "ticket.md"
    kept = [line for line in ticket.read_text("utf-8").splitlines() if "empty list" not in line]
    ticket.write_text("\n".join(kept) + "\n", encoding="utf-8")
    edit_record(
        directory,
        lambda r: r.update(criteria=[c for c in r["criteria"] if "empty list" not in c["text"]]),
    )


def drop_web_ui(directory: Path) -> None:
    facts = json.loads((directory / "session.json").read_text(encoding="utf-8"))
    (directory / "session.json").write_text(json.dumps({**facts, "types": ["service"]}))
    edit_record(directory, lambda r: [c.pop("screenshots", None) for c in r["criteria"]])


def change_first_answer(directory: Path) -> None:
    first = products.log(directory)[0]["id"]
    (directory / "probes" / f"{first}.response").write_bytes(b"There is nothing left.")


# Each way a careless or dishonest tester might change what is on disk, and what `check` says.
SEEDED: dict[str, tuple[Callable[[Path], object], str]] = {
    "truncated log": (
        lambda d: edit_lines(d / "requests.jsonl", lambda lines: lines[:-1]),
        "the request log was cut short: the harness logged 3 requests and the file holds 2",
    ),
    "changed log": (
        lambda d: edit_lines(
            d / "requests.jsonl",
            lambda lines: [line.replace(b'"status": 303', b'"status": 200') for line in lines],
        ),
        "the request log was changed after the harness wrote it",
    ),
    "a line added": (
        lambda d: edit_lines(d / "requests.jsonl", lambda lines: [*lines, lines[0]]),
        "the request log holds 4 lines but the harness logged 3 requests",
    ),
    "no seal": (lambda d: (d / "seal.json").unlink(), "the request log was never sealed"),
    "changed answer": (change_first_answer, "whose stored answer was changed or lost"),
    "empty screenshot": (
        lambda d: (d / "tester" / "shots" / "list.png").write_bytes(b""),
        "cites screenshot shots/list.png, which is not a PNG file",
    ),
    "screenshot outside": (
        lambda d: edit_record(d, lambda r: r["criteria"][0].update(screenshots=["../seal.json"])),
        "cites screenshot ../seal.json, which is not a PNG file",
    ),
    "ticket trimmed": (trim_ticket, "the session's copy of the ticket was changed after `up`"),
    "session file edited": (drop_web_ui, "session.json was changed after `up`"),
}


@pytest.mark.parametrize("name", SEEDED)
def test_check_holds_the_record_to_the_files_the_tester_did_not_write(
    tried: Path, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    seed, sentence = SEEDED[name]
    seed(tried)
    code, out = check(tried.parents[5], capsys)
    print(f"{name}: {out[0]}")  # noqa: T201 # -- the demonstration prints each refusal
    assert code == 1
    assert any(sentence in line for line in out), out
    assert out[-1] == "acceptance check: red"


def test_down_prints_the_seal_and_check_holds_the_session_to_it(
    product: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, (url, _) = up(product, capsys)
    products.tried(url)
    _, said = down(product, capsys)
    directory = session(product)
    digest = hashlib.sha256((directory / "seal.json").read_bytes()).hexdigest()
    assert said == [f"session {directory.name}: seal {digest}", "acceptance down: stopped 1"]
    write_record(directory, products.good_record(directory))
    assert run(product, "check", "--slug", SLUG, "--seal", digest, capsys=capsys) == (
        0,
        ["acceptance check: green"],
    )
    log = directory / "requests.jsonl"
    log.write_bytes(log.read_bytes() + log.read_bytes().splitlines(keepends=True)[0])
    seal = json.loads((directory / "seal.json").read_text(encoding="utf-8"))
    seal |= {"requests": 4, "sha256": hashlib.sha256(log.read_bytes()).hexdigest()}
    (directory / "seal.json").write_text(json.dumps(seal), encoding="utf-8")
    code, out = run(product, "check", "--slug", SLUG, "--seal", digest, capsys=capsys)
    assert code == 1
    assert out[0] == f"seal.json is not the seal `down` printed ({digest[:12]}): it was rewritten"


def test_a_session_that_ended_before_down_came_is_not_blessed(
    product: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, (url, _) = up(product, capsys)
    products.tried(url)
    directory = session(product)
    proxy = json.loads((directory / "proxy.json").read_text(encoding="utf-8"))
    os.kill(proxy["pid"], signal.SIGTERM)
    assert acceptance.wait_unlocked(directory, acceptance.STOP_GRACE)
    code, out = down(product, capsys)
    assert code == 1
    assert out[0].startswith(f"session {directory.name} ended at ")
    assert out[0].endswith(", before `down` came: something stopped it early")
    assert out[-1] == "acceptance down: stopped 0"
    write_record(directory, products.good_record(directory))
    code, out = check(product, capsys)
    assert code == 1
    assert any(line.endswith("before `down` came: something stopped it early") for line in out)
    assert down(product, capsys) == (0, ["acceptance down: nothing was running"])


@pytest.mark.parametrize(
    ("text", "sentence"),
    [
        (None, "the tester wrote no record at"),
        ("{not json", "is not JSON"),
        ("[]", "the record is not an object"),
    ],
)
def test_check_refuses_a_record_that_is_missing_or_not_one(
    tried: Path, capsys: pytest.CaptureFixture[str], *, text: str | None, sentence: str
) -> None:
    path = tried / acceptance.RECORD
    path.unlink()
    if text is not None:
        path.write_text(text, encoding="utf-8")
    code, out = check(tried.parents[5], capsys)
    assert code == 1
    assert sentence in out[0]


def test_replay_runs_each_kept_check_on_a_fresh_boot_and_against_nothing(
    tried: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tried.parents[5]
    code, out = run(root, "replay", "--slug", SLUG, capsys=capsys)
    assert (code, out) == (0, ["K1 (C1) holds on a fresh boot", "acceptance replay: green"])
    code, out = run(root, "replay", "--slug", SLUG, "--control", capsys=capsys)
    assert code == 0
    assert out[0].startswith(
        "K1 (C1) fails against a server that does nothing: step 2, GET /, does not show"
    )
    assert out[1] == "acceptance replay --control: green"
    replayed = json.loads((tried / "replay.json").read_text(encoding="utf-8"))
    assert replayed["results"] == [{"id": "K1", "criterion": "C1", "holds": True, "reason": None}]
    assert json.loads((tried / "control.json").read_text("utf-8"))["results"][0]["holds"] is False
    assert run(root, "summary", "--slug", SLUG, capsys=capsys)[0] == 0
    summary = json.loads((root / "docs/features" / SLUG / "acceptance.json").read_text("utf-8"))
    assert (summary["replay"], summary["control"]) == (
        {"passed": 1, "of": 1},
        {"failed": 1, "of": 1},
    )


def test_a_check_that_holds_against_nothing_proves_nothing(
    tried: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    checks = tried.parent / "checks.json"
    checks.write_text(json.dumps({"checks": [KEPT_ONLY_STATUS]}), encoding="utf-8")
    root = tried.parents[5]
    code, out = run(root, "replay", "--checks", str(checks), "--control", capsys=capsys)
    assert (code, out) == (
        1,
        [
            "K9 (C1) holds against a server that does nothing, so it proves nothing",
            "acceptance replay --control: red",
        ],
    )


def test_replay_says_why_a_check_does_not_hold(
    tried: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lies = {**KEPT_ONLY_STATUS, "steps": [{"request": "GET /", "expect": {"status": 201}}]}
    checks = tried.parent / "checks.json"
    checks.write_text(json.dumps({"checks": [lies]}), encoding="utf-8")
    code, out = run(tried.parents[5], "replay", "--checks", str(checks), capsys=capsys)
    assert (code, out) == (
        1,
        [
            "K9 (C1) does not hold on a fresh boot: step 1, GET /, answered 200, expected 201",
            "acceptance replay: red",
        ],
    )


def test_replay_with_nothing_to_replay_or_a_check_that_does_not_read(
    tried: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    checks = tried.parent / "checks.json"
    root = tried.parents[5]
    checks.write_text(json.dumps({"checks": []}), encoding="utf-8")
    assert run(root, "replay", "--checks", str(checks), capsys=capsys) == (
        1,
        ["there are no kept checks to replay", "acceptance replay: red"],
    )
    checks.write_text(json.dumps({"checks": [{"id": "K1"}]}), encoding="utf-8")
    code, out = run(root, "replay", "--checks", str(checks), capsys=capsys)
    assert code == 2
    assert out[0].startswith("acceptance replay: kept check K1 needs criterion as a non-empty")


def test_replay_of_a_product_that_cannot_start(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    checks = tmp_path / "checks.json"
    checks.write_text(json.dumps({"checks": [KEPT_ONLY_STATUS]}), encoding="utf-8")
    root = products.make(tmp_path / "none", boot="none")
    assert run(root, "replay", "--checks", str(checks), capsys=capsys) == (
        1,
        [
            "The project gives no way to start it: its justfile sets `boot` to none.",
            "acceptance replay: red",
        ],
    )
    failing = python("print('Error: no build'); raise SystemExit(1)")
    root = products.make(tmp_path / "failing", boot=failing)
    code, out = run(root, "replay", "--checks", str(checks), capsys=capsys)
    assert code == 1
    assert out[0].startswith("K9 (C1) does not hold on a fresh boot: the product did not start:")


def test_the_summary_is_the_same_bytes_every_run_and_carries_the_kept_checks(
    tried: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tried.parents[5]
    out = root / "docs" / "features" / SLUG
    assert run(root, "summary", "--slug", SLUG, capsys=capsys) == (
        0,
        [str(out / acceptance.SUMMARY), "acceptance summary: green"],
    )
    first = (out / acceptance.SUMMARY).read_bytes()
    assert run(root, "summary", "--slug", SLUG, capsys=capsys)[0] == 0
    assert (out / acceptance.SUMMARY).read_bytes() == first
    summary = json.loads(first)
    session_id = json.loads((tried / "session.json").read_text(encoding="utf-8"))["session"]
    assert (summary["session"], summary["requests"], summary["kept"]) == (session_id, 3, 1)
    kept = json.loads((out / acceptance.CHECKS).read_text(encoding="utf-8"))
    assert [check["id"] for check in kept["checks"]] == ["K1"]
    code, said = run(root, "replay", "--checks", str(out / acceptance.CHECKS), capsys=capsys)
    assert (code, said[0]) == (0, "K1 (C1) holds on a fresh boot")
    record = json.loads((tried / acceptance.RECORD).read_text(encoding="utf-8"))
    record["kept"] = []
    record["criteria"][0]["unkept"] = "It needs a fresh store."
    write_record(tried, record)
    assert run(root, "summary", "--slug", SLUG, capsys=capsys)[0] == 0
    assert not (out / acceptance.CHECKS).exists()


def test_no_summary_of_a_record_that_does_not_hold(
    tried: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    SEEDED["changed log"][0](tried)
    root = tried.parents[5]
    code, out = run(root, "summary", "--slug", SLUG, capsys=capsys)
    assert (code, out[-1]) == (1, "acceptance summary: red")
    assert not (root / "docs" / "features" / SLUG).exists()


def test_a_session_that_ended_long_ago_is_removed_by_the_next_up(
    product: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    up(product, capsys)
    down(product, capsys)
    old = session(product)
    other = acceptance.store(product) / "other" / "c" / "20200101T000000000000Z-00000000"
    other.mkdir(parents=True)
    (other / "session.json").write_text("{}", encoding="utf-8")
    month = (dt.datetime.now(dt.UTC) - dt.timedelta(days=30)).timestamp()
    for path in (old, *old.rglob("*"), other, other / "session.json"):
        os.utime(path, (month, month))
    up(product, capsys)
    assert not old.exists()
    assert not (acceptance.store(product) / "other").exists()
    assert len(acceptance.sessions(acceptance.store(product), 3)) == 1


def test_down_ends_a_session_that_will_not_stop_and_names_what_still_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "store" / SLUG / "head" / "stuck"
    directory.mkdir(parents=True)
    (directory / "session.json").write_text("{}", encoding="utf-8")
    sleeper = subprocess.Popen(["sleep", "60"])  # noqa: S607 # -- sleep from PATH
    stuck = directory / "live.lock"
    stuck.write_text(str(sleeper.pid), encoding="utf-8")
    starting = tmp_path / "store" / SLUG / "head" / "starting"
    starting.mkdir()
    (starting / "session.json").write_text("{}", encoding="utf-8")
    (starting / "live.lock").write_text("", encoding="utf-8")
    port = gatelib.free_port()
    server = subprocess.Popen(  # noqa: S603 # -- this interpreter and a fixed server
        [sys.executable, "-c", SERVE], env={**os.environ, "PORT": str(port)}
    )
    listening = f"http://127.0.0.1:{port}/"
    monkeypatch.setattr(acceptance, "DOWN_WAIT", 0.2)
    try:
        while not gatelib.listening(listening):
            time.sleep(0.02)
        (directory / "proxy.json").write_text(json.dumps({"url": listening}), encoding="utf-8")
        with stuck.open("rb") as held, starting.joinpath("live.lock").open("rb") as also:
            fcntl.flock(held, fcntl.LOCK_EX)
            fcntl.flock(also, fcntl.LOCK_EX)
            code = acceptance.stop_sessions(tmp_path / "store" / SLUG)
    finally:
        server.terminate()
        server.wait()
    out = capsys.readouterr().out.splitlines()
    assert code == 1
    assert gone(sleeper.pid)
    sleeper.wait()
    assert out == [
        "session starting did not stop within 0.2s; ended",
        "session stuck did not stop within 0.2s; ended",
        f"{listening} still answers after session stuck stopped",
        "acceptance down: stopped 2",
    ]


def test_down_while_a_request_is_in_flight_stops_everything_and_seals(
    product: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, (url, _) = up(product, capsys)
    assert products.request(url)[0] == 200
    failed: list[BaseException] = []

    def hang() -> None:
        try:
            products.request(url, path="/slow")
        except OSError as error:
            failed.append(error)

    waiting = threading.Thread(target=hang)
    waiting.start()
    products.wait_for(tmp_path / "slow")
    began = time.monotonic()
    assert down(product, capsys) == (0, ["acceptance down: stopped 1"])
    assert time.monotonic() - began < acceptance.STOP_GRACE
    waiting.join()
    assert failed, "the request in flight ended with the session"
    seal = json.loads((session(product) / "seal.json").read_text(encoding="utf-8"))
    assert seal["requests"] == 1, "what was answered is logged; what never was is not"


def test_up_after_down_begins_a_new_session_and_keeps_the_ended_one(
    product: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    up(product, capsys)
    down(product, capsys)
    ended = session(product)
    code, (url, tester) = up(product, capsys)
    assert code == 0
    assert Path(tester).parent != ended
    assert acceptance.state(ended) is acceptance.State.ENDED
    assert products.request(url)[0] == 200


def test_a_boot_log_that_says_nothing_known_still_gives_a_sentence(tmp_path: Path) -> None:
    log = tmp_path / "boot.log"
    log.write_text("with_service: red\n", encoding="utf-8")
    assert acceptance.what_it_did(log) == "it never answered, and printed nothing"
    assert acceptance.what_it_did(tmp_path / "none.log") == "it never answered, and printed nothing"
