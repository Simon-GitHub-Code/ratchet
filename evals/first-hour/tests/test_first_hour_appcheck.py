import json
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from first_hour import appcheck
from first_hour.persona import Slice

PIECE = Slice("html-form", "save a link", "https://example.com/a", "example.com/a")

# A read-later app small enough to read: a form, a list, and a choice of how it misbehaves.
SERVER = textwrap.dedent(
    """
    import os, sys, urllib.parse
    from http.server import BaseHTTPRequestHandler, HTTPServer

    MODE = sys.argv[1]
    ITEMS = []
    FORM = (
        '<form action="/items" method="{method}"><input type="hidden" name="csrf" value="t">'
        '<input type="checkbox" name="flag"><input name="link" type="url">'
        '<textarea></textarea></form>'
    )

    class App(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def page(self, status=200):
            bare = '<input name="q"><form action="/s"><input type="hidden" name="x"></form>'
            method = "get" if MODE == "get" else "post"
            body = bare if MODE == "no-form" else FORM.format(method=method)
            body += "".join(f"<li>{i}</li>" for i in ITEMS)
            self.send_response(status)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

        def save(self, query):
            fields = urllib.parse.parse_qs(query)
            if MODE != "forgets" and fields.get("csrf") == ["t"]:
                ITEMS.append(fields["link"][0])

        def do_GET(self):
            path, _, query = self.path.partition("?")
            if MODE == "broken":
                self.page(500)
            elif MODE == "hangup":
                self.close_connection = True
            elif path == "/items":
                self.save(query)
                self.page()
            else:
                self.page()

        def do_POST(self):
            if MODE == "refuses":
                self.send_response(422)
                self.end_headers()
                return
            length = int(self.headers["Content-Length"])
            self.save(self.rfile.read(length).decode())
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()

    port = int(os.environ["PORT"]) if MODE != "own-port" else int(sys.argv[2])
    if MODE == "own-port":
        print(f"listening on http://localhost:{port}/", flush=True)
    HTTPServer(("127.0.0.1", port), App).serve_forever()
    """
)


def app(tmp_path: Path, readme: str | None) -> Path:
    (tmp_path / "server.py").write_text(SERVER, encoding="utf-8")
    (tmp_path / "README.md").write_text(readme, encoding="utf-8")
    return tmp_path


def run_readme(mode: str, extra: str = "") -> str:
    return f"# App\n\n```bash\n$ python3 server.py {mode} {extra}\n```\n"


def check(root: Path, timeout: float = 20.0) -> appcheck.Result:
    return appcheck.check(root, PIECE, root / "app.log", timeout=timeout)


@pytest.mark.parametrize("mode", ["post", "get"])
def test_an_app_that_saves_the_link_passes_the_slice(tmp_path: Path, mode: str) -> None:
    result = check(app(tmp_path, run_readme(mode)))
    assert result.started
    assert result.page_ok
    assert result.slice_ok, result.finding
    assert result.source == "README.md"
    assert result.event()["kind"] == "app"


def test_an_app_that_ignores_port_is_found_by_the_url_it_prints(tmp_path: Path) -> None:
    result = check(app(tmp_path, run_readme("own-port", str(appcheck.free_port()))))
    assert result.slice_ok, result.finding


@pytest.mark.parametrize(
    ("mode", "page_ok", "finding"),
    [
        ("forgets", True, "is not on the page after"),
        ("no-form", True, "no form with a text field"),
        ("refuses", True, "answered 422"),
        ("broken", False, "GET / answered 500"),
        ("hangup", False, "request failed"),
    ],
)
def test_an_app_that_starts_but_fails_the_slice_says_why(
    tmp_path: Path, *, mode: str, page_ok: bool, finding: str
) -> None:
    result = check(app(tmp_path, run_readme(mode)))
    assert result.started
    assert result.page_ok is page_ok
    assert not result.slice_ok
    assert finding in result.finding


def test_an_app_that_will_not_stop_is_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(appcheck, "STOP_GRACE_S", 0.5)
    stubborn = (
        "import signal, sys, time; signal.signal(signal.SIGTERM, signal.SIG_IGN);"
        " print('ready', flush=True); time.sleep(60)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", stubborn], stdout=subprocess.PIPE, start_new_session=True
    )
    assert process.stdout.readline() == b"ready\n"
    appcheck.stop(process)
    process.stdout.close()
    assert process.returncode == -signal.SIGKILL


def test_an_app_that_exits_or_never_listens_did_not_start(tmp_path: Path) -> None:
    (tmp_path / "justfile").write_text("start:\n    exit 3\n", encoding="utf-8")
    result = check(tmp_path)
    assert result.start_command == "just start"
    assert not result.started
    assert "did not listen" in result.finding
    (tmp_path / "justfile").write_text("dev:\n    sleep 30\n", encoding="utf-8")
    assert "within 1s" in check(tmp_path, timeout=1).finding


def test_a_repository_with_no_run_command_is_a_finding_not_a_crash(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "# App\n\n```\nnpm install\nnpm test\n```\nrun it\n", "utf-8"
    )
    result = check(tmp_path)
    assert result.finding == appcheck.NO_WAY
    assert not result.started


def test_the_start_command_comes_from_the_readme_then_the_justfile_then_package_json(
    tmp_path: Path,
) -> None:
    assert appcheck.start_command(tmp_path) is None
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "x"}}), "utf-8")
    assert appcheck.start_command(tmp_path) is None
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"dev": "x"}}), "utf-8")
    assert appcheck.start_command(tmp_path) == appcheck.Start("npm run dev", "package.json")
    (tmp_path / "pnpm-lock.yaml").write_text("", "utf-8")
    assert appcheck.start_command(tmp_path).command == "pnpm run dev"
    (tmp_path / "yarn.lock").write_text("", "utf-8")
    assert appcheck.start_command(tmp_path).command == "yarn run dev"
    (tmp_path / "justfile").write_text("check:\n    true\nserve port='3000':\n    true\n", "utf-8")
    assert appcheck.start_command(tmp_path) == appcheck.Start("just serve", "justfile")
    (tmp_path / "README.md").write_text("```\npnpm install\npnpm start\n```\n", "utf-8")
    assert appcheck.start_command(tmp_path) == appcheck.Start("pnpm start", "README.md")


def test_a_readme_test_or_sync_command_is_not_taken_for_the_start_command(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "```\nuv sync\nuv run pytest -q\nnpx vitest run\n```\n", "utf-8"
    )
    assert appcheck.start_command(tmp_path) is None


def test_a_package_json_that_is_not_json_or_has_no_scripts_names_nothing(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{nope", "utf-8")
    assert appcheck.start_command(tmp_path) is None
    (tmp_path / "package.json").write_text("[]", "utf-8")
    assert appcheck.start_command(tmp_path) is None
    (tmp_path / "justfile").write_text("check:\n    true\n", "utf-8")
    assert appcheck.start_command(tmp_path) is None
