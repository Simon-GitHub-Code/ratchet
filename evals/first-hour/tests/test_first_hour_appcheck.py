import json
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from first_hour import appcheck
from first_hour.appcheck import Verdict
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
            spa = '<div id="app"></div><script type="module" src="/src/main.ts"></script>'
            method = "get" if MODE == "get" else "post"
            body = bare if MODE == "no-form" else FORM.format(method=method)
            body = spa if MODE == "spa" else body
            body += "<script>/* saves in the browser */</script>" if MODE == "scripted" else ""
            body += "".join(f"<li>{i}</li>" for i in ITEMS)
            self.send_response(status)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

        def save(self, query):
            fields = urllib.parse.parse_qs(query)
            if MODE not in ("forgets", "scripted") and fields.get("csrf") == ["t"]:
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
    assert result.slice is Verdict.WORKS, result.finding
    assert result.usable
    assert result.source == "README.md"
    assert result.event()["kind"] == "app"
    assert result.event()["slice_ok"] is True


def test_an_app_that_ignores_port_is_found_by_the_url_it_prints(tmp_path: Path) -> None:
    result = check(app(tmp_path, run_readme("own-port", str(appcheck.free_port()))))
    assert result.usable, result.finding


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
    assert result.slice is Verdict.FAILS
    assert not result.usable
    assert finding in result.finding


@pytest.mark.parametrize(
    ("mode", "finding"),
    [
        ("spa", "no form with a text field"),
        ("scripted", "is not on the page after"),
    ],
)
def test_a_page_that_builds_itself_in_the_browser_is_unchecked_not_failed(
    tmp_path: Path, *, mode: str, finding: str
) -> None:
    result = check(app(tmp_path, run_readme(mode)))
    assert result.started
    assert result.page_ok
    assert result.slice is Verdict.UNCHECKED
    assert not result.usable
    assert "runs no browser" in result.finding
    assert finding in result.finding


def test_the_start_command_runs_through_the_wrapper_with_the_given_environment(
    tmp_path: Path,
) -> None:
    seen: list[list[str]] = []

    def wrap(command: list[str]) -> list[str]:
        seen.append(list(command))
        return ["env", *command]

    root = app(tmp_path, run_readme("post"))
    env = {"PATH": "/usr/bin:/bin"}
    result = appcheck.check(
        root, PIECE, root / "app.log", timeout=20.0, launch=appcheck.Launch(wrap, env)
    )
    assert result.usable, result.finding
    assert seen == [["sh", "-c", "python3 server.py post"]]


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


CLI_PIECE = Slice("cli", "add a task, list it", "add buy oat milk", "buy oat milk", then="list")
TODO = textwrap.dedent(
    """
    import pathlib, sys
    store = pathlib.Path("tasks.txt")
    if sys.argv[1] == "add":
        store.open("a").write(" ".join(sys.argv[2:]) + "\\n")
    elif sys.argv[1] == "list":
        print(store.read_text() if sys.argv[-1] != "empty" else "nothing")
    else:
        sys.exit(2)
    """
)


def cli_app(tmp_path: Path, recipe: str = "python3 todo.py {{args}}") -> Path:
    (tmp_path / "todo.py").write_text(TODO, encoding="utf-8")
    (tmp_path / "justfile").write_text(f"run *args:\n    {recipe}\n", encoding="utf-8")
    return tmp_path


def cli_check(root: Path, piece: Slice = CLI_PIECE, timeout: float = 20.0) -> appcheck.Result:
    return appcheck.check(root, piece, root / "app.log", timeout=timeout)


def test_a_command_line_tool_is_run_the_way_the_repository_says_and_its_list_read(
    tmp_path: Path,
) -> None:
    result = cli_check(cli_app(tmp_path))
    assert result.usable, result.finding
    assert result.start_command == "just run"
    assert result.started
    assert "printed what the slice expects" in result.finding
    log = (tmp_path / "app.log").read_text(encoding="utf-8")
    assert "$ just run add buy oat milk" in log
    assert "$ just run list" in log


def test_a_command_line_tool_that_runs_but_lists_nothing_fails_the_slice(tmp_path: Path) -> None:
    piece = Slice("cli", "d", "add x", "buy oat milk", then="list empty")
    result = cli_check(cli_app(tmp_path), piece)
    assert not result.usable
    assert result.started
    assert "is not in its output" in result.finding


def test_a_command_line_tool_that_exits_non_zero_stops_at_that_command(tmp_path: Path) -> None:
    result = cli_check(cli_app(tmp_path), Slice("cli", "d", "frobnicate", "x", then="list"))
    assert not result.usable
    assert not result.started
    assert "failed (2)" in result.finding
    assert "$ just run list" not in (tmp_path / "app.log").read_text(encoding="utf-8")


def test_a_command_line_tool_that_does_not_answer_fails_with_the_wait(tmp_path: Path) -> None:
    result = cli_check(cli_app(tmp_path, "exec sleep 3"), timeout=0.5)
    assert not result.usable
    assert "no answer within 0.5s" in result.finding


def test_a_command_line_tool_runs_through_the_wrapper_and_the_slices_one_step_alone(
    tmp_path: Path,
) -> None:
    seen: list[list[str]] = []

    def wrap(command: list[str]) -> list[str]:
        seen.append(list(command))
        return command

    root = cli_app(tmp_path)
    one = Slice("cli", "d", "add buy oat milk", "buy oat milk")
    launch = appcheck.Launch(wrap, {"PATH": "/usr/bin:/bin"})
    result = appcheck.check(root, one, root / "app.log", launch=launch)
    assert result.start_command == "just run"
    assert seen == [["sh", "-c", "just run add buy oat milk"]]
    assert not result.usable  # the add prints nothing, so the expected line is not in its output


def test_a_readmes_example_run_is_cut_back_to_the_program_before_its_arguments() -> None:
    assert appcheck.cli_base("go run . add <task>", "add") == "go run ."
    assert appcheck.cli_base("go run . add buy milk", "add") == "go run ."
    assert appcheck.cli_base("cargo run -- <args>", "list") == "cargo run --"
    assert appcheck.cli_base("./todo", "") == "./todo"


@pytest.mark.parametrize(
    "command",
    [
        "go run .",
        "cargo run",
        "dotnet run",
        "bundle exec rackup",
        "bin/rails server",
        "rails s",
        "./gradlew bootRun",
        "mvn spring-boot:run",
        "java -jar app.jar --serve",
    ],
)
def test_the_start_commands_of_the_other_ecosystems_are_read_from_a_readme(
    tmp_path: Path, command: str
) -> None:
    (tmp_path / "README.md").write_text(f"```sh\n{command}\n```\n", encoding="utf-8")
    found = appcheck.start_command(tmp_path)
    assert found is not None
    assert found.command == command


@pytest.mark.parametrize(
    "command", ["go build ./...", "cargo test", "dotnet build", "bundle install"]
)
def test_the_build_and_test_commands_of_the_other_ecosystems_are_not_start_commands(
    tmp_path: Path, command: str
) -> None:
    (tmp_path / "README.md").write_text(f"```sh\n{command}\n```\n", encoding="utf-8")
    assert appcheck.start_command(tmp_path) is None
