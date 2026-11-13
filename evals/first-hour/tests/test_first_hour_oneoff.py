import json
import os

import pytest

from first_hour import appcheck, oneoff, reduce

DEEP_RED = "==> bite-all <==\nparallel: bite-all: red (exit 1) in 2s\n==> perf <==\nlater: life:x\nparallel: perf: green in 0.1s\n"
DEEP_GREEN = "parallel: bite-all: green in 2s\n"


def lines(path, items):
    path.write_text("".join(json.dumps(i) + "\n" for i in items), encoding="utf-8")


def stamp(path, clock):
    """Give ``path`` the mtime of ``clock`` (UTC) on the day of the timeline file."""
    day = os.stat(path.parent / "timeline.jsonl").st_mtime // 86400 * 86400
    hours, minutes, seconds = (int(p) for p in clock.split(":"))
    moment = day + hours * 3600 + minutes * 60 + seconds
    os.utime(path, (moment, moment))


def logs(tmp_path, app_readme=None):
    root = tmp_path / "dogfood"
    log = root / "logs"
    log.mkdir(parents=True)
    lines(
        log / "timeline.jsonl",
        [
            {"t": "02:00:00", "step": "probe", "event": "start", "prompt": "probe"},
            {"t": "02:00:05", "step": "probe", "event": "end", "wall_s": 5},
            {"t": "02:00:10", "step": "01-init", "event": "start", "prompt": "/ratchet:init"},
            {"t": "02:02:10", "step": "01-init", "event": "end", "wall_s": 120},
            {
                "t": "02:30:10",
                "step": "05-feature",
                "event": "start",
                "prompt": "/ratchet:feature 01",
            },
            {"t": "02:40:10", "step": "05-feature", "event": "end", "wall_s": 600},
            {"t": "02:41:10", "step": "05-feature", "event": "linger-stop"},
            {"t": "02:42:10", "step": "06-next", "event": "end", "wall_s": 1},
        ],
    )
    with (log / "timeline.jsonl").open("a", encoding="utf-8") as timeline:
        timeline.write("\n")
    lines(
        log / "01-init.jsonl",
        [
            {
                "type": "ResultMessage",
                "msg": {"session_id": "a", "total_cost_usd": 1.5, "result": "ok"},
            },
            {"type": "UserMessage", "msg": "not a dict"},
        ],
    )
    lines(
        log / "05-feature.jsonl",
        [
            {
                "type": "ResultMessage",
                "msg": {"session_id": "a", "total_cost_usd": 4.0, "result": "ok"},
            }
        ],
    )
    question = {
        "header": "H",
        "question": "Q?",
        "answer": "A (Recommended)",
        "options": [{"label": "A (Recommended)"}, {"label": "B"}],
    }
    lines(
        log / "owner.jsonl",
        [
            {
                "t": "02:00:01",
                "step": "probe",
                "kind": "question",
                "class": "product: x",
                **question,
            },
            {
                "t": "02:01:00",
                "step": "01-init",
                "kind": "question",
                "class": "technical: took recommended",
                **question,
            },
            {
                "t": "02:01:10",
                "step": "01-init",
                "kind": "question",
                "class": "product: scripted pick",
                **question,
                "answer": "B",
                "options": None,
            },
            {
                "t": "02:01:20",
                "step": "01-init",
                "kind": "question",
                "class": "UNSCRIPTED: first",
                **question,
            },
            {
                "t": "02:01:30",
                "step": "01-init",
                "kind": "permission-prompt",
                "tool": "Bash",
                "decision": "allow",
            },
        ],
    )
    merge = 'merge.py "adopt"\nmerge: owner\nnext: the owner types `git switch main && git merge --no-ff adopt-ratchet`, then `just check-deep`\n'
    (log / "04-owner-merge-adopt.txt").write_text(merge, encoding="utf-8")
    (log / "04-owner-check-deep.txt").write_text(DEEP_RED, encoding="utf-8")
    (log / "04b-check-deep-rerun1.txt").write_text(DEEP_GREEN, encoding="utf-8")
    (log / "09-owner-merge-spec.txt").write_text("Merge made\n" + DEEP_GREEN, encoding="utf-8")
    (log / "12-owner-merge-plain.txt").write_text("Merge made\n", encoding="utf-8")
    (log / "x-owner-merge-y.txt").write_text("not a numbered step\n", encoding="utf-8")
    stamp(log / "04-owner-merge-adopt.txt", "02:10:10")
    stamp(log / "09-owner-merge-spec.txt", "02:20:10")
    stamp(log / "12-owner-merge-plain.txt", "02:25:10")
    if app_readme is not None:
        (root / "app").mkdir()
        if app_readme:
            (root / "app" / "README.md").write_text(app_readme, encoding="utf-8")
    return log


def test_the_hand_driven_runs_logs_reduce_like_a_driven_run(tmp_path):
    out = tmp_path / "events.jsonl"
    oneoff.convert(logs(tmp_path, app_readme=""), out)
    m = reduce.reduce(reduce.read(out))
    assert m["stop"]["reason"] == "observer-stopped"
    assert m["minutes"]["to_first_merge"] == 10.0
    q = m["questions"]
    assert (q["total"], q["product"], q["default_able"], q["took_recommended"]) == (3, 1, 2, 2)
    assert m["prompts"]["unknown"] == 1
    assert [x["branch"] for x in m["merges"]] == ["adopt-ratchet", "spec", "plain"]
    assert m["hand_merges"] == 3
    assert [p["command"] for p in m["pastes"]["commands"]] == [
        "just merge adopt-ratchet",
        "git switch main && git merge --no-ff adopt-ratchet",
        "just check-deep",
        "git switch main && git merge --no-ff spec",
        "just check-deep",
        "git switch main && git merge --no-ff plain",
    ]
    assert m["red_gates"] == [
        {"gate": "bite-all", "command": "just check-deep", "minute": 10.0, "red_on_rerun": False}
    ]
    assert m["gates"]["later"] == {"perf": "life:x"}
    assert m["cost_usd"]["session"] == 4.0
    assert m["app"]["finding"] == appcheck.NO_WAY


@pytest.mark.parametrize(
    ("readme", "finding"),
    [("```\nnode dist/server.js\n```\n", "not started by the adapter"), (None, None)],
)
def test_the_app_is_read_from_the_runs_checkout_when_there_is_one(tmp_path, readme, finding):
    events = oneoff.events(logs(tmp_path, app_readme=readme))
    apps = [e for e in events if e["kind"] == "app"]
    assert [a["finding"] for a in apps] == ([finding] if finding else [])
