"""Interpreters given an inline program that writes a guard path are judged like any shell write.

The 2026-11-09 trial: a subagent changed ``DESIGN.md`` with ``python3 - <<'EOF' ... EOF`` and
the hook let it through, so the owner's approval never fired. Each row runs in ``/repo`` against
a manifest that guards ``DESIGN.md``, ``pyproject.toml`` and a directory, and ``LOCKED.md`` that
only the owner changes. A write to the first three is refused where the commit lands on ``main``
and gets no verdict on a branch (the merge judge reads it); a write to ``LOCKED.md`` is refused
on any branch.
"""

from pathlib import Path

import pytest

from ratchet_guards import interpreters
from ratchet_guards.context import Guard
from ratchet_guards.hook import reason
from ratchet_guards.manifest import parse
from tests.support import ROOT, FakeRepo, make_guard, rules, verdicts
from tests.test_hook import SHIM, checkout, decision, run_shim, switch_to_a_branch

W = "guard-path-write"
P = "guard-path-default"
MANIFEST = parse(
    """
[[group]]
name = "design"
reason = "The design record is the standard every review judges against."
paths = ["DESIGN.md", "pyproject.toml", "scripts/**"]

[[group]]
name = "locked"
reason = "Only the owner changes it."
edit = "deny"
paths = ["LOCKED.md"]
"""
)
GUARD = make_guard(manifest=MANIFEST)
ON_MAIN = make_guard(FakeRepo(branch="main"), manifest=MANIFEST)
T4 = "python3 - <<'EOF'\np='DESIGN.md'\ns=open(p).read()\n...\nopen(p,'w').write(s)\nEOF"

TEXT_WITH_SLASHES = (
    "python3 - <<'EOF'\nfrom pathlib import Path\np = Path('notes.py')\ns = p.read_text()\n"
    's = s.replace(\'a\', """if not (root / FLAG).is_file():\n    (root / FLAG).unlink()""")\n'
    "p.write_text(s)\nEOF"
)

PYTHON_WRITES = [
    T4,
    "python3 - <<'EOF'\nwith open('DESIGN.md', 'w', encoding='utf-8') as f:\n    f.write('x')\nEOF",
    "python3 <<'EOF'\nopen('DESIGN.md','a').write('x')\nEOF",
    "python3 -c \"open('DESIGN.md','w').write('x')\"",
    "python -c \"open('DESIGN.md','w').write('x')\"",
    "python3.12 -c \"open('DESIGN.md','w').write('x')\"",
    "python3 -c \"open('DESIGN.md', mode='a').write('x')\"",
    "python3 -c \"open('DESIGN.md', encoding='utf-8', mode='w').write('x')\"",
    "python3 -c \"open(file='DESIGN.md', mode='x')\"",
    "python3 -c \"open('DESIGN.md','r+').write('x')\"",
    "python3 -c \"open('DESIGN.md','wb').write(b'x')\"",
    "python3 -c \"import os; open(os.path.join('DESIGN.md'),'w')\"",
    "python3 -c \"import io; io.open('DESIGN.md','w')\"",
    "python3 -c \"import pathlib; pathlib.Path('DESIGN.md').open('w')\"",
    "python3 -c \"from pathlib import Path; Path('DESIGN.md').write_text('x')\"",
    "python3 -c \"from pathlib import Path; Path('DESIGN.md').write_bytes(b'x')\"",
    "python3 -c \"from pathlib import Path; Path('DESIGN.md').touch()\"",
    "python3 -c \"from pathlib import Path; Path('DESIGN.md').unlink()\"",
    "python3 -c \"from pathlib import Path; Path('DESIGN.md').rename('/tmp/x')\"",
    "python3 -c \"from pathlib import Path; Path('DESIGN.md').replace('/tmp/x')\"",
    "python3 -c \"import os; os.remove('DESIGN.md')\"",
    "python3 -c \"import os; os.unlink('DESIGN.md')\"",
    "python3 -c \"import os; os.rename('DESIGN.md', '/tmp/x')\"",
    "python3 -c \"import os; os.replace('/tmp/x', 'DESIGN.md')\"",
    "python3 -c \"import os; os.chmod('DESIGN.md', 0o444)\"",
    "python3 -c \"import shutil; shutil.copy('/tmp/x', 'DESIGN.md')\"",
    "python3 -c \"import shutil; shutil.move('DESIGN.md', '/tmp/x')\"",
    "python3 -c \"import shutil; shutil.rmtree('scripts')\"",
    "python3 -c \"import os; x = 'a / b'; os.unlink('scripts')\"",
    "python3 -c \"import os; os.unlink('DESIGN.md'); x = 'a / b'\"",
    "python3 -c \"import os; os.system('rm scripts/x.py / y')\"",
    "python3 -c \"import subprocess; subprocess.run(['sed', '-i', 's/a/b/', 'DESIGN.md'])\"",
    "python3 -c \"import os; os.system('echo x > DESIGN.md')\"",
    "python3 -c \"import fileinput; fileinput.input('DESIGN.md', inplace=True)\"",
    "python3 -c \"import os; os.open('DESIGN.md', os.O_WRONLY)\"",
    "python3 -c \"open('/repo/DESIGN.md','w')\"",
    "python3 -c \"open('./scripts/../DESIGN.md','w')\"",
    "python3 -c \"open('scripts/gate.py','w')\"",
    "python3 -I -c \"open('DESIGN.md','w')\"",
    "python3 -Bc \"open('DESIGN.md','w')\"",
    "python3 -W ignore -c \"open('DESIGN.md','w')\"",
    "python3 -c\"open('DESIGN.md','w')\"",
    "python3 -c \"print(open('DESIGN.md').read()); open('/tmp/out','w').write('x')\"",
    "FOO=1 python3 -c \"open('DESIGN.md','w')\"",
    "python3 - arg <<'EOF'\nopen('DESIGN.md','w')\nEOF",
    "python3 - <<< \"open('DESIGN.md','w').write('x')\"",
    "python3 <<<\"open('DESIGN.md','w').write('x')\"",
    'python3 - <<< \'open("DESIGN.md","w").write("x")\'',
    "cat <<'EOF' | python3\nopen('DESIGN.md','w').write('x')\nEOF",
    "cat <<'EOF' | python3 -\nopen('DESIGN.md','w').write('x')\nEOF",
    "echo \"open('DESIGN.md','w').write('x')\" | python3",
    "echo \"open('DESIGN.md','w').write('x')\" | python3 -",
    "cd docs && python3 -c \"open('../DESIGN.md','w')\"",
    "cd /repo/docs && python3 -c \"open('../DESIGN.md','w')\"",
]
WRAPPED = [
    "sudo python3 -c \"open('DESIGN.md','w')\"",
    "timeout 5 python3 -c \"open('DESIGN.md','w')\"",
    "env X=1 python3 -c \"open('DESIGN.md','w')\"",
    "env X=1 python3 - <<'EOF'\nopen('DESIGN.md','w')\nEOF",
    "nohup python3 -c \"open('DESIGN.md','w')\"",
    "uv run python3 -c \"open('DESIGN.md','w')\"",
    "uv run python - <<'EOF'\nopen('DESIGN.md','w')\nEOF",
    "bash -c \"python3 -c \\\"open('DESIGN.md','w')\\\"\"",
    'bash -c \'python3 - <<EOF\nopen("DESIGN.md","w")\nEOF\'',
    "bash <<'EOF'\npython3 -c \"open('DESIGN.md','w')\"\nEOF",
    "echo $(python3 -c \"open('DESIGN.md','w')\")",
    "find . -name x -exec python3 -c \"open('DESIGN.md','w')\" \\;",
    "true && python3 -c \"open('DESIGN.md','w')\"",
    "(python3 - <<'EOF'\nopen('DESIGN.md','w')\nEOF\n)",
    "python3 - <<'EOF'\nopen('DESIGN.md','w')\nEOF\necho done",
]
NODE_WRITES = [
    "node -e \"require('fs').writeFileSync('DESIGN.md','x')\"",
    "node --eval \"require('fs').writeFileSync('DESIGN.md','x')\"",
    "node --eval=\"require('fs').writeFileSync('DESIGN.md','x')\"",
    "node -p \"require('fs').writeFileSync('DESIGN.md','x')\"",
    "node --print \"require('fs').writeFileSync('DESIGN.md','x')\"",
    "node -pe \"require('fs').writeFileSync('DESIGN.md','x')\"",
    "node -r dotenv/config -e \"require('fs').writeFileSync('DESIGN.md','x')\"",
    "nodejs -e \"require('fs').writeFileSync('DESIGN.md','x')\"",
    "node -e \"require('fs').appendFileSync('DESIGN.md','x')\"",
    "node -e \"require('fs').promises.writeFile('DESIGN.md','x')\"",
    "node -e \"require('fs').rmSync('DESIGN.md')\"",
    "node -e \"require('fs').rmSync('scripts', {recursive: true})\"",
    "node -e \"require('fs').unlinkSync('DESIGN.md')\"",
    "node -e \"require('fs').renameSync('DESIGN.md', '/tmp/x')\"",
    "node -e \"require('fs').copyFileSync('/tmp/x', 'DESIGN.md')\"",
    "node -e \"require('fs').createWriteStream('DESIGN.md')\"",
    "node -e \"require('fs').chmodSync('DESIGN.md', 0o444)\"",
    "node -e \"require('fs').openSync('DESIGN.md', 'w')\"",
    "node -e \"require('child_process').execSync('echo x > DESIGN.md')\"",
    "node -e \"await import('node:child_process'); 'DESIGN.md'\"",
    "node -e 'require(`fs`).writeFileSync(`DESIGN.md`, `x`)'",
    "node - <<'EOF'\nrequire('fs').writeFileSync('DESIGN.md', 'x')\nEOF",
    "node <<'EOF'\nrequire('fs').writeFileSync('DESIGN.md', 'x')\nEOF",
    "node <<< \"require('fs').writeFileSync('DESIGN.md', 'x')\"",
    "deno eval \"Deno.writeTextFileSync('DESIGN.md','x')\"",
    "deno eval --ext ts \"Deno.removeSync('DESIGN.md')\"",
    "bun -e \"Bun.write('DESIGN.md','x')\"",
    "bun --eval \"require('fs').writeFileSync('DESIGN.md','x')\"",
]
PERL_RUBY_WRITES = [
    'perl -e \'open(F, ">DESIGN.md"); print F "x"\'',
    'perl -e \'open(my $f, ">>", "DESIGN.md")\'',
    "perl -e 'open(F, \"+<DESIGN.md\")'",
    "perl -e 'unlink \"DESIGN.md\"'",
    'perl -e \'rename("DESIGN.md", "/tmp/x")\'',
    "perl -e 'system(\"rm DESIGN.md\")'",
    "perl -ne 'print \"DESIGN.md\" if 0' -i x.md",
    "perl -pi -e 'print \"DESIGN.md\"' unrelated.txt",
    "perl -pi.bak -e 'print \"DESIGN.md\"' unrelated.txt",
    "perl -pi -e 's/a/b/' DESIGN.md",
    "perl <<'EOF'\nopen(F, \">DESIGN.md\");\nEOF",
    'ruby -e \'File.write("DESIGN.md", "x")\'',
    "ruby -e 'File.delete(\"DESIGN.md\")'",
    "ruby -e 'File.unlink(\"DESIGN.md\")'",
    'ruby -e \'File.rename("DESIGN.md", "/tmp/x")\'',
    'ruby -e \'File.open("DESIGN.md", "w") { |f| f.write(1) }\'',
    "ruby -e 'FileUtils.rm_rf(\"scripts\")'",
    "ruby -i -e 'puts \"DESIGN.md\"' unrelated.txt",
    "ruby -pi -e 'gsub(/a/, \"b\")' DESIGN.md",
    'ruby <<\'EOF\'\nFile.write("DESIGN.md", "x")\nEOF',
    'ruby - <<\'EOF\'\nFile.write("DESIGN.md", "x")\nEOF',
]
DENIED = PYTHON_WRITES + WRAPPED + NODE_WRITES + PERL_RUBY_WRITES

ALLOWED = [
    "python3 -c \"import tomllib; print(tomllib.load(open('pyproject.toml','rb')))\"",
    "python3 - <<'EOF'\nprint(open('DESIGN.md').read())\nEOF",
    "python3 - <<'EOF'\nimport tomllib\nprint(tomllib.load(open('pyproject.toml', 'rb')))\nEOF",
    "python3 -c \"open('DESIGN.md', 'r')\"",
    "python3 -c \"open('DESIGN.md', 'rb')\"",
    "python3 -c \"open('DESIGN.md', mode='r')\"",
    "python3 -c \"open('DESIGN.md', encoding='utf-8')\"",
    "python3 -c \"open(os.path.join('a', 'b'), 'r', encoding='utf-8')\"",
    "python3 -c \"from pathlib import Path; print(Path('DESIGN.md').read_text())\"",
    "python3 -c \"from pathlib import Path; Path('DESIGN.md').open()\"",
    "python3 -c \"from pathlib import Path; Path('DESIGN.md').open('rb')\"",
    "python3 -c \"print(open('DESIGN.md').read().replace('a', 'b'))\"",
    "python3 -c \"print(open('DESIGN.md').read().replace('a', 'b', 1))\"",
    "python3 -c \"import re; print(re.sub('a', 'b', open('DESIGN.md').read()))\"",
    "python3 -c \"import os; print(os.path.exists('DESIGN.md'), os.getcwd())\"",
    "python3 -c \"import os; print(os.stat('DESIGN.md').st_mtime)\"",
    "python3 -c \"import json; print(json.loads('{}'), 'DESIGN.md')\"",
    "python3 -c \"open('/tmp/out.txt','w').write('x')\"",
    "python3 -c \"open('notes.txt','w').write('x')\"",
    "python3 -c \"import os; x = 'a / b'; os.unlink('notes.txt')\"",
    "python3 -c \"import os; os.system('echo a / b'); os.unlink('notes.txt')\"",
    "python3 -c \"import os; os.unlink('notes.txt'); x = '(root // y).unlink()'\"",
    TEXT_WITH_SLASHES,
    "python3 -c \"import subprocess; subprocess.run(['git', 'status'])\"",
    "python3 -c \"import shutil; shutil.copy('a.txt', '/tmp/b.txt')\"",
    "python3 -c \"open('/tmp/x','w')  # DESIGN.md\"",
    "python3 -c \"print('hello')\"",
    "python3 -c",
    "python3 script.py",
    "python3 scripts/tool.py DESIGN.md",
    "python3 -m pytest -q",
    "python3 -m pytest -c DESIGN.md",
    "python3 -m http.server",
    "python3 --version",
    "python3 - arg < /tmp/script.py",
    "python3 -c 'print(1)' > /tmp/x",
    "cat DESIGN.md | python3 -c 'import sys; print(sys.stdin.read())'",
    'echo \'open("DESIGN.md", "w")\' > /tmp/script.py',
    "cat <<'EOF' > /tmp/script.py\nopen('DESIGN.md', 'w')\nEOF",
    "cat <<'EOF'\nopen('DESIGN.md', 'w')\nEOF",
    "echo \"open('DESIGN.md','w')\" | grep open",
    "echo \"open('DESIGN.md','w')\" | python3 script.py",
    "python3 - <<< \"print(open('DESIGN.md').read())\"",
    "cat <<'EOF' | python3 -\nprint(open('DESIGN.md').read())\nEOF",
    "cd docs && python3 -c \"open('DESIGN.md','w')\"",
    "cd /elsewhere && python3 -c \"open('DESIGN.md','w')\"",
    "python3 -c \"open('/elsewhere/DESIGN.md','w')\"",
    "uv run python3 script.py",
    "uv run pytest -q",
    "node -e \"console.log(require('fs').readFileSync('DESIGN.md','utf8'))\"",
    "node -p \"require('fs').readFileSync('DESIGN.md','utf8').length\"",
    "node -p \"require('./package.json').version\"",
    "node -e \"require('fs').writeFileSync('/tmp/o','x')\"",
    "node -e \"require('fs').openSync('DESIGN.md', 'r')\"",
    "node -e \"console.log('DESIGN.md'.replace('a', 'b'))\"",
    "node --check workflows/pipeline.js",
    "node script.js",
    "node - <<'EOF'\nconsole.log(require('fs').readFileSync('DESIGN.md', 'utf8'))\nEOF",
    "node -r dotenv/config script.js",
    "perl -e 'open(F, \"<DESIGN.md\"); print <F>'",
    'perl -e \'open(my $f, "<", "DESIGN.md"); print <$f>\'',
    "perl -ne 'print if /x/' DESIGN.md",
    "perl -MFile::Temp -e 'print 1'",
    "perl -MStorable -e 'print \"DESIGN.md\"'",
    "perl -e 'print \"DESIGN.md\"'",
    "perl -e 'unlink \"/tmp/x\"'",
    "perl -pi -e 's/a/b/' notes.txt",
    "ruby -e 'puts File.read(\"DESIGN.md\")'",
    'ruby -e \'File.write("/tmp/x", "y")\'',
    'ruby -e \'File.open("DESIGN.md", "r") { |f| puts f.read }\'',
    "ruby script.rb",
    "ruby -rjson -e 'puts JSON.generate(1)'",
    "deno eval \"console.log(Deno.readTextFileSync('DESIGN.md'))\"",
    "deno run script.ts",
    "bun -e \"console.log(await Bun.file('DESIGN.md').text())\"",
    "bun run build",
    "pythonic -c \"open('DESIGN.md','w')\"",
    "nodemon -e \"require('fs').writeFileSync('DESIGN.md','x')\"",
]


@pytest.mark.parametrize("command", DENIED)
def test_an_inline_program_that_writes_a_guard_path_is_denied_on_main(command: str) -> None:
    assert rules(command, ON_MAIN) == [P]


@pytest.mark.parametrize("command", DENIED)
def test_an_inline_program_that_writes_a_guard_path_gets_no_verdict_on_a_branch(
    command: str,
) -> None:
    assert rules(command, GUARD) == []


@pytest.mark.parametrize(
    "command",
    [
        "python3 -c \"open('LOCKED.md','w')\"",
        "python3 - <<'EOF'\nopen('LOCKED.md','w')\nEOF",
        "node -e \"require('fs').writeFileSync('LOCKED.md','x')\"",
        "perl -pi -e 's/a/b/' LOCKED.md",
        "ruby -e 'File.delete(\"LOCKED.md\")'",
        "python3 -c \"import shutil; shutil.rmtree('/')\"",
    ],
)
@pytest.mark.parametrize("guard", [GUARD, ON_MAIN], ids=["branch", "main"])
def test_an_inline_program_that_writes_a_locked_path_is_denied_on_any_branch(
    command: str, guard: Guard
) -> None:
    assert rules(command, guard) == [W]


@pytest.mark.parametrize("command", ALLOWED)
def test_reads_and_unrelated_writes_pass(command: str) -> None:
    assert rules(command, GUARD) == []


def test_the_trial_command_is_denied_on_main_with_the_way_to_a_branch() -> None:
    (verdict,) = verdicts(T4, ON_MAIN, ROOT)
    message = reason(verdict)
    assert verdict.rule == P
    assert "DESIGN.md (design)" in verdict.detail
    assert "python3" in verdict.detail
    assert "A read of a guard path goes in a tool call of its own" in message
    assert "apart from an unrelated write" in message
    assert "Make the change on a branch (`git switch -c <name>`)" in message
    assert "Ratchet guard denied this tool call" in message
    assert "Edit or Write tool so the owner is asked" not in message


def test_the_trial_command_gets_no_verdict_on_a_branch() -> None:
    assert verdicts(T4, GUARD, ROOT) == []


def test_every_guard_path_the_program_names_is_reported() -> None:
    command = "python3 -c \"open('DESIGN.md','w'); open('pyproject.toml','w')\""
    details = [verdict.detail for verdict in verdicts(command, ON_MAIN, ROOT)]
    assert any("DESIGN.md" in detail for detail in details)
    assert any("pyproject.toml" in detail for detail in details)


def test_a_guard_path_is_resolved_against_the_working_directory() -> None:
    command = "python3 -c \"open('DESIGN.md','w')\""
    assert rules(command, ON_MAIN, ROOT / "docs") == []
    assert rules(command, ON_MAIN, ROOT) == [P]


def test_a_here_string_fed_to_a_shell_is_checked() -> None:
    assert rules("bash <<< 'git push --force'", GUARD) == ["force-push"]
    assert rules("sh <<<'git push -f'", GUARD) == ["force-push"]
    assert rules("bash <<< 'echo hello'", GUARD) == []


def test_the_shim_denies_the_trial_command_in_a_real_checkout(tmp_path: Path) -> None:
    root = checkout(tmp_path)
    program = "python3 - <<'EOF'\np='justfile'\ns=open(p).read()\n...\nopen(p,'w').write(s)\nEOF"
    case = {"tool_name": "Bash", "tool_input": {"command": program}, "cwd": str(root)}
    done = run_shim(SHIM, case)
    assert decision(done) == "deny"
    assert "Make the change on a branch" in done.stdout
    read = "python3 - <<'EOF'\nprint(open('justfile').read())\nEOF"
    read_case = {"tool_name": "Bash", "tool_input": {"command": read}, "cwd": str(root)}
    assert run_shim(SHIM, read_case).stdout == ""
    switch_to_a_branch(root)
    assert run_shim(SHIM, case).stdout == ""


@pytest.mark.parametrize(
    ("head", "args", "programs", "stdin"),
    [
        ("python3", ["-c", "p"], ("p",), False),
        ("python3", ["-cp"], ("p",), False),
        ("python3", ["-Ic", "p", "arg"], ("p",), False),
        ("python3", ["-W", "error", "-c", "p"], ("p",), False),
        ("python3", ["-W", "error", "-"], (), True),
        ("python3", ["-"], (), True),
        ("python3", [], (), True),
        ("python3", ["--", "-"], (), True),
        ("python3", ["script.py"], (), False),
        ("python3", ["-m", "pytest"], (), False),
        ("python3", ["-c"], (), False),
        ("python3.11", ["-c", "p"], ("p",), False),
        ("python", ["-c", "p"], ("p",), False),
        ("node", ["-e", "p"], ("p",), False),
        ("node", ["-pe", "p"], ("p",), False),
        ("node", ["--eval=p"], ("p",), False),
        ("node", ["--print", "p"], ("p",), False),
        ("node", ["-r", "x", "-e", "p"], ("p",), False),
        ("node", ["-r", "x", "-"], (), True),
        ("node", ["script.js"], (), False),
        ("nodejs", ["-e", "p"], ("p",), False),
        ("bun", ["-e", "p"], ("p",), False),
        ("bun", [], (), False),
        ("deno", ["eval", "p"], ("p",), False),
        ("deno", ["eval", "--ext", "ts", "p"], ("p",), False),
        ("deno", ["run", "x.ts"], (), False),
        ("perl", ["-e", "p"], ("p",), False),
        ("perl", ["-lane", "p"], ("p",), False),
        ("perl", ["-ne", "p", "file"], ("p",), False),
        ("perl", ["-eprint 1"], ("print 1",), False),
        ("perl", ["-MFile::Temp", "-e", "p"], ("p",), False),
        ("perl", ["-I", "lib", "-e", "p"], ("p",), False),
        ("perl", [], (), True),
        ("ruby", ["-e", "p"], ("p",), False),
        ("ruby", ["-e", "p", "-e", "q"], ("p", "q"), False),
        ("ruby", ["-rjson", "-e", "p"], ("p",), False),
        ("ruby", ["-"], (), True),
    ],
)
def test_the_registry_finds_the_program_or_stdin(
    head: str, args: list[str], programs: tuple[str, ...], *, stdin: bool
) -> None:
    call = interpreters.invocation(head, args)
    assert call is not None
    assert call.name == head
    assert call.programs == programs
    assert call.reads_stdin is stdin


@pytest.mark.parametrize(
    "head", ["python3.", "pythonic", "python3-config", "nodemon", "node-gyp", "rubyfmt", "bash"]
)
def test_other_commands_are_not_interpreters(head: str) -> None:
    assert interpreters.invocation(head, ["-c", "p"]) is None


@pytest.mark.parametrize(
    ("head", "args", "in_place"),
    [
        ("perl", ["-pi", "-e", "p"], True),
        ("perl", ["-i.bak", "-pe", "p"], True),
        ("perl", ["-0777pi", "-e", "p"], True),
        ("perl", ["-pe", "p"], False),
        ("perl", ["-Mstrict", "-e", "p"], False),
        ("perl", ["-MPOSIX=floor", "-e", "p"], False),
        ("ruby", ["-pi", "-e", "p"], True),
        ("ruby", ["-rminitest", "-e", "p"], False),
        ("python3", ["-i", "-c", "p"], False),
    ],
)
def test_an_in_place_flag_is_a_write_by_itself(
    head: str, args: list[str], *, in_place: bool
) -> None:
    call = interpreters.invocation(head, args)
    assert call is not None
    assert call.in_place is in_place
    assert call.writes("no calls here") is in_place


def test_a_write_call_is_found_per_language() -> None:
    python = interpreters.invocation("python3", ["-c", "p"])
    node = interpreters.invocation("node", ["-e", "p"])
    assert python is not None
    assert node is not None
    assert python.writes("open(p, 'w')")
    assert not python.writes("open(p, 'r')")
    assert not python.writes("writeFileSync(p)")
    assert node.writes("writeFileSync(p)")
    assert not node.writes("open(p, 'r')")
    assert not node.writes("console.log(1)")
    assert python.removes("os.remove(p)")
    assert not python.removes("open(p, 'w')")
    assert node.removes("fs.rmSync(p)")
    assert not node.removes("fs.writeFileSync(p)")


def test_the_literals_of_a_program_are_the_candidates() -> None:
    names = interpreters.named_paths("""p = 'DESIGN.md'; q = "a b.txt"; r = 'x'; s = \'\'""")
    assert names == ["DESIGN.md", "a b.txt", "a", "b.txt", "x"]
    assert interpreters.named_paths("'cat > DESIGN.md'") == ["cat > DESIGN.md", "cat", "DESIGN.md"]
    assert interpreters.named_paths("""open(F, ">>DESIGN.md")""") == [">>DESIGN.md", "DESIGN.md"]
    assert interpreters.named_paths("x = DESIGN.md") == []
    assert interpreters.named_paths("s = '''a\nDESIGN.md'''") == [
        "a\nDESIGN.md",
        "a",
        "DESIGN.md",
    ]
    assert interpreters.named_paths("`DESIGN.md`") == ["DESIGN.md"]
    assert interpreters.named_paths("# don't\np = 'DESIGN.md'") == ["DESIGN.md"]


def test_only_a_literal_that_reads_as_a_command_gives_its_words_for_the_owner() -> None:
    assert interpreters.written_names("p = 'AGENTS.md'") == ["AGENTS.md"]
    assert interpreters.written_names("os.system('cp t.md AGENTS.md')") == [
        "cp t.md AGENTS.md",
        "cp",
        "t.md",
        "AGENTS.md",
    ]
    assert interpreters.written_names("""open(F, ">>DESIGN.md")""") == [">>DESIGN.md", "DESIGN.md"]
    assert interpreters.written_names("""row = ("layer", "`AGENTS.md`")""") == [
        "layer",
        "`AGENTS.md`",
    ]
    assert interpreters.written_names("s = 'see .claude/skills/<area>/** first'") == [
        "see .claude/skills/<area>/** first"
    ]


def test_a_separator_word_of_a_command_string_is_not_a_path() -> None:
    assert interpreters.named_paths("s = 'a / b'") == ["a / b", "a", "b"]
    assert interpreters.named_paths("s = 'a // b /'") == ["a // b /", "a", "b"]
    assert interpreters.named_paths("p = '/'") == ["/"]
    assert interpreters.named_paths("p = '//'") == ["//"]
    assert interpreters.named_paths("p = '/ '") == ["/ "]
