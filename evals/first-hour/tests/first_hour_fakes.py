"""A plugin checkout, a product session and an owner model small enough to read, for driving a run.

The plugin's route reads the owner's project: no repository yet asks for ``/ratchet:init``; an
unmerged ``feature/1-save`` asks the owner to merge it by hand; after that it names
``/ratchet:next`` until the run stops. The session's ``/ratchet:init`` writes an app whose page
does not yet save a link, merges one branch itself and leaves ``feature/1-save``, which makes the
page save, for the owner.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

from first_hour import harness, owner, steps
from first_hour.persona import Persona, Slice

PIECE = Slice("html-form", "save a link", "https://example.com/a", "example.com/a")
PERSONA = Persona("fake", "a read-later list", "an owner", PIECE)
LIFE_EVENT = (
    "also: a sign that the project is `depended-on` (others rely on it): /ratchet:safeguard"
)
ROUTE = textwrap.dedent(
    """
    import json, os, subprocess, sys
    args = sys.argv
    root = args[args.index("--root") + 1]
    if "--decline" in args:
        open(os.path.join(root, "..", "declined"), "a").write(args[-1] + "\\n")
        sys.exit(0)
    def git(*a):
        done = subprocess.run(["git", "-C", root, *a], capture_output=True, text=True)
        return done.returncode, done.stdout
    SAVE = "feature/1-save"
    MERGE = "git switch -q main && git merge -q --no-ff -m \\"Merge branch '%s'\\" %s"
    MERGE = MERGE % (SAVE, SAVE)
    waiting = git("rev-parse", "--verify", "-q", SAVE)[0] == 0
    waiting = waiting and SAVE not in git("branch", "--merged", "main")[1]
    if git("rev-parse", "--verify", "-q", "main")[0] != 0:
        print(json.dumps({"command": "/ratchet:init", "stage": "init"}))
    elif os.environ.get("FAKE_PASTE"):
        print(json.dumps({"command": "run `%s`" % os.environ["FAKE_PASTE"], "stage": "merge"}))
    elif waiting:
        also = [os.environ.get("FAKE_ALSO", "")]
        print(json.dumps({"command": "run `%s`" % MERGE, "stage": "merge", "also": also}))
    else:
        print(json.dumps({"command": "/ratchet:next", "stage": "idle"}))
    """
)
SERVER = textwrap.dedent(
    """
    import os, urllib.parse
    from http.server import BaseHTTPRequestHandler, HTTPServer
    SAVES = False
    ITEMS = []
    class App(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            body = '<form action="/" method="post"><input name="link"></form>'
            body += "".join(f"<li>{i}</li>" for i in ITEMS)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body.encode())
        def do_POST(self):
            data = self.rfile.read(int(self.headers["Content-Length"])).decode()
            if SAVES:
                ITEMS.append(urllib.parse.parse_qs(data)["link"][0])
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()
    HTTPServer(("127.0.0.1", int(os.environ["PORT"])), App).serve_forever()
    """
)
GIT_ENV = {
    "GIT_AUTHOR_NAME": "o",
    "GIT_AUTHOR_EMAIL": "o@x.invalid",
    "GIT_COMMITTER_NAME": "o",
    "GIT_COMMITTER_EMAIL": "o@x.invalid",
}


def bwrap_works() -> bool:
    """Whether ``bwrap`` can make a sandbox on this machine (CI runners often cannot)."""
    if shutil.which("bwrap") is None:
        return False
    done = subprocess.run(
        ["bwrap", "--ro-bind", "/", "/", "true"], capture_output=True, check=False
    )
    return done.returncode == 0


def git(repo: Path, *args: str) -> str:
    env = {**os.environ, **GIT_ENV, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, env=env
    )
    return done.stdout.strip()


def plugin(where: Path) -> Path:
    """A plugin checkout with a manifest and the fake route, committed."""
    (where / ".claude-plugin").mkdir(parents=True)
    (where / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": "9.9.9"}))
    (where / "scripts").mkdir()
    (where / "scripts" / "route.py").write_text(ROUTE)
    git(where, "init", "-q", "-b", "main")
    git(where, "add", ".")
    git(where, "commit", "-q", "-m", "plugin")
    return where


def operator(where: Path) -> harness.Operator:
    """An operator with no companions and one watched file."""
    where.mkdir(parents=True, exist_ok=True)
    watched = where / "settings.json"
    watched.write_text("{}")
    return harness.Operator(
        tools={}, plugins=[], claude="fake 1.0", watched=[watched], projects=where / "projects"
    )


def config(plugin_dir: Path, root: Path, **budget: float) -> harness.Config:
    return harness.Config(
        plugin=plugin_dir, root=root, persona=PERSONA, budget=steps.Budget(**budget)
    )


class Owner:
    """Answers a life event with ``pick``, and replies to the first ``replies`` turns that ask."""

    def __init__(self, run: harness.Run, *, pick: str = "Not yet", replies: int = 1) -> None:
        self.run = run
        self.usd = 0.25
        self.read = 0
        self.pick = pick
        self.replies = replies

    async def answer(self, question: owner.Question, channel: owner.Channel) -> owner.Answer:
        verdict = owner.Verdict(owner.Kind.PRODUCT, (self.pick,), "it is mine alone")
        reply = owner.answer(question, verdict)
        self.run.log.emit(**reply.event(channel))  # type: ignore[arg-type]
        return reply

    async def read_turn(self, text: str) -> owner.Reply:
        if self.read == 0:
            self.run.log.emit("owner-cost", usd=self.usd)
        self.read += 1
        needs = "?" in text and self.read <= self.replies
        return owner.Reply(
            needs_reply=needs,
            kind=owner.Kind.DEFAULT_ABLE,
            text=owner.GO_AHEAD,
            reason="a build question",
        )


class Session:
    """Builds the app on ``/ratchet:init``; with ``ready``, hangs on the next slash command.

    The hang is the way a stuck session looks: a CLI process runs behind it and the turn never ends.
    """

    def __init__(self, run: harness.Run, ready: Path | None = None) -> None:
        self.run = run
        self.costs: dict[str, float] = {"s1": 0.0}
        self.ready = ready
        self.closed = False
        self.process: subprocess.Popen[bytes] | None = None
        self.typed: list[str] = []

    async def turn(self, text: str, deadline: float) -> str:
        self.typed.append(text)
        app = self.run.layout.app
        if text == "/ratchet:init":
            git(app, "init", "-q", "-b", "main")
            (app / "server.py").write_text(SERVER)
            if self.ready is None:
                (app / "README.md").write_text("# App\n\n```\npython3 server.py\n```\n")
            git(app, "add", ".")
            git(app, "commit", "-q", "-m", "the app")
            git(app, "switch", "-q", "-c", "feature/2-agent", "main")
            git(app, "commit", "-q", "--allow-empty", "-m", "a page")
            git(app, "switch", "-q", "-c", "feature/1-save", "main")
            (app / "server.py").write_text(SERVER.replace("SAVES = False", "SAVES = True"))
            git(app, "commit", "-q", "-am", "save a link")
            git(app, "switch", "-q", "main")
            git(
                app,
                "merge",
                "-q",
                "--no-ff",
                "-m",
                "Merge branch 'feature/2-agent'",
                "feature/2-agent",
            )
            self.costs["s1"] = 1.5
            self.run.log.emit("result", session="s1", cost_usd=1.5)
            return "Built it. Shall I use blue? I recommend blue."
        if self.ready is not None and text.startswith("/"):
            self.process = subprocess.Popen(  # noqa: ASYNC220 - the way a CLI runs behind a session
                [sys.executable, "-c", "import time; time.sleep(300)", f"{self.ready}-session"]
            )
            self.ready.write_text("session")
            await asyncio.sleep(3600)
        self.costs["s1"] += 0.5
        return f"done: {text}"

    async def close(self) -> None:
        self.closed = True
        if self.process is not None:
            self.process.kill()
            self.process.wait()


class Models:
    def __init__(
        self, ready: Path | None = None, *, pick: str = "Not yet", replies: int = 1
    ) -> None:
        self.ready = ready
        self.pick = pick
        self.replies = replies
        self.sessions: list[Session] = []

    def owner(self, run: harness.Run) -> Owner:
        return Owner(run, pick=self.pick, replies=self.replies)

    async def session(self, run: harness.Run, owner_: harness.OwnerModel) -> Session:
        session = Session(run, self.ready)
        self.sessions.append(session)
        return session


def main(argv: list[str]) -> None:
    """One run in this process, for the kill tests.

    ``argv`` is the plugin checkout, the run directory, the operator's home, the file that says
    the run is ready to be stopped, ``1`` to sandbox it, and a command to paste (or nothing). The
    run waits to be stopped in the session or, when there is a command, in that pasted command.
    """
    plugin_dir, root, home, ready, sandboxed, paste = argv
    config_ = config(Path(plugin_dir), Path(root), minutes=10.0)
    operator_ = operator(Path(home))
    run = harness.setup(config_, operator_, os.environ)
    run.sandboxed = sandboxed == "1"
    run.env.update(GIT_ENV)
    if paste:
        run.env["FAKE_PASTE"] = paste
    asyncio.run(harness.drive(run, operator_, Models(Path(ready))))


if __name__ == "__main__":
    main(sys.argv[1:])
