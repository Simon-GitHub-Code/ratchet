"""What the shell check reads as a path, with the commands an eval run's session was refused.

The refused commands come from an eval run of the first hour; each is cut to the part the check
read wrongly, with the run's own paths replaced by neutral ones.
"""

from pathlib import Path

import pytest

from first_hour import policy
from first_hour.policy import Scope

ROOT = Path("/tmp/fh-run")
SCOPE = Scope(
    root=ROOT,
    cwd=ROOT / "app",
    home=ROOT / "home",
    tmp=ROOT / "tmp",
    writable=(ROOT / "app", ROOT / "home", ROOT / "tmp"),
)
WORK = f"{ROOT}/tmp/wt-ticket01"

# Text a shell passes on that is not a path: each was refused as "reaches outside the run".
NOT_PATHS = [
    # a slash command named in a commit message, an echo and a word (/ratchet:safeguard)
    'git commit -q -m "docs: run /ratchet:safeguard when a gate is red"',
    "git commit -q -m \"$(cat <<'EOF'\nfeat: x\n\nsee /ratchet:safeguard\nEOF\n)\"",
    'echo "next: /ratchet:safeguard"',
    "echo /ratchet:safeguard",
    # a sed script: its address, its flag and its replacement (/p, /X/, //, /export, /)
    "sed -n '/[Nn]on-negotiable/,/^## /p' AGENTS.md | head -30",
    "sed -n '/^# setup = /,$d;p' quality/bites.toml > .ratchet/adopt/bites.head",
    "sed -i 's/WHERE read_at IS NULL //' src/links.ts",
    "sed -i 's/This form is not ours\\./Cross-site forms are refused./; s/X/Y/' src/hooks.ts",
    'sed -i \'s/"uv_build>=0.13.7,<0.14.0"/"uv_build==0.13.7"/\' pyproject.toml',
    (
        'sed -i \'s/^import a from "a";/import { b } from "x\\/config";\\nimport a from "a";/;'
        " s/^export default a.config(/export default b(/' eslint.config.js"
    ),
    "sed -e 's/a/b/' -e '/c/d' notes.txt",
    "awk -F: '/x/ {print $1}' notes.txt",
    # a grep pattern that holds a route (/signin)
    'grep -n "=== \\"/signin\\" ||" -A1 src/hooks.ts | head -3',
    # a heredoc that writes a file: a comment (/**), a route (/items), a regex (/\n/g)
    "cat > svelte.config.js <<'EOF'\n/** @type {import(\"@sveltejs/kit\").Config} */\nEOF",
    "cat > probe.mjs <<'EOF'\nconst s = text.replace(/\\n/g, ' ');\nEOF",
    "python3 - <<'EOF'\nfrom pathlib import Path\nx = Path('a') / 'b'\nroute = \"/items\"\nEOF",
    "node - <<'EOF'\nconst r = await page.goto(base + '/signin');\nEOF",
    "cat <<EOF >out.txt\n/** a comment */\nEOF\nls",
    "printf '<p>x</p>\\n' > src/page.svelte",
    # a heredoc after another command, a delimiter in quotes, a dash and a body that never ends
    "export A=1; python3 - <<\"EOF\"\nroute = '/items'\nEOF",
    "cat <<-EOF\n\t/** x */\n\tEOF",
    "cat <<EOF\n/** never closed",
    # a division in code run with -c, and a literal that names no directory at the root
    'python3 -I -c \'import pathlib\nprint(pathlib.Path(".") / "x")\' args',
    "python3 -c 'print(\"/items\")'",
    "node -e 'process.env.STORAGE_PATH=\"'$d'/f/links.db\"' 2>&1",
    # a word joined from quoted parts: the path is `$app/tsconfig.json`, not `/tsconfig.json`
    "cat .ratchet/verify/node_modules/'$app'/tsconfig.json | head -12",
    # a path in the run, reached by `..` from a directory the line moved to
    f"cd {WORK} && mkdir -p ../../scratch-ticket01 && ls ../..",
    f"cd {WORK} && sed -n 1,60p ../../plugin/scripts/change_size.py",
    f"cd {WORK}; cd ..; ls ..",
    "cd src && ls ../..",
    "cd -P src && cat ../notes.txt",
    # a program asked for, not run; separators and redirects around words
    "echo ok > /dev/null 2>&1",
    "ls | xargs cat notes.txt",
    "FOO=bar env BAZ=1 ls notes.txt",
    "echo 'a' ';' b",
    "echo ''",
    "FOO=bar",
    "; ls",
    "> out.txt && ls &",
    'echo "a quote that never closes',
    "cd && cd - && ls",
    f"cd {ROOT}/tmp && cd $OTHER && ls",
]


@pytest.mark.parametrize("command", NOT_PATHS)
def test_text_that_only_looks_like_a_path_is_not_refused(command: str) -> None:
    assert policy.decide("Bash", {"command": command}, SCOPE) == policy.ALLOW, command


# Each reaches outside the run, and the policy still says so.
REACHES = [
    # the run's own refused reaches: `..` from the run's app directory, and a path in /proc
    "mkdir -p ../../scratch-ticket01",
    "sed -n 1,60p ../../plugin/scripts/change_size.py",
    "cd $E; ls ../..",
    "STORAGE_PATH=/proc/nope/links.db node build/index.js",
    "cd ../.. && ls",
    "cd /home/someone && ls",
    f"cd {WORK} && ls ../../../..",
    "cd src && cd ../../.. && ls",
    # a script's own files are paths: a sed file, a sed -e script's input, an awk input, a grep root
    "sed -n 1,5p /home/someone/.bashrc",
    "sed -n -e p /home/someone/.bashrc",
    "sed -e 's/a/b/' /home/someone/.bashrc",
    "awk '{print}' /srv/x",
    "grep -rn password /home/someone/.config",
    "grep -e password /home/someone/.config",
    "grep -n -A 1 /home/someone/.config",
    # a path in a quoted substitution, in a backtick substitution and in a nested shell
    'echo "$(cat /home/someone/.ssh/id_ed25519)"',
    "echo `cat /home/someone/.ssh/id_ed25519`",
    'bash -c "cat /home/someone/.ssh/id_ed25519"',
    "sh -ec 'cat /home/someone/.ssh/id_ed25519'",
    "bash <<EOF\ncat /home/someone/.ssh/id_ed25519\nEOF",
    "bash <<EOF\ncat /home/someone/.ssh/id_ed25519",
    # code that names a path at the root, or climbs: in -c, in -e and in a heredoc
    "python3 -c 'print(open(\"/home/someone/x\").read())'",
    "perl -pe 'open F, \"/home/someone/x\"' notes.txt",
    "node -e \"require('fs').readFileSync('/srv/x')\"",
    "python3 -I -c 'import os; os.listdir(\"../..\")'",
    "python3 -c 'open(\"~/../../x\")'",
    "python3 - <<'EOF'\nopen('/home/someone/x').read()\nEOF",
    # a path used as a word, an assignment, an option value and a redirect target
    "cat <<EOF >/srv/x\nhi\nEOF",
    "FOO=/srv/x env ls",
    "env FOO=/srv/x ls",
    "ls --directory=/srv",
    "ls | xargs cat /home/someone/x",
    "ls ~/../../x",
    "ls ${HOME}/../../x",
    # a line the shell could not read: every word that starts like a path counts
    'echo "unclosed /home/someone/x',
    "echo 'unclosed /ratchet:safeguard",
]


@pytest.mark.parametrize("command", REACHES)
def test_a_path_outside_the_run_is_still_refused(command: str) -> None:
    decision = policy.decide("Bash", {"command": command}, SCOPE)
    assert not decision.allowed, command
    assert "outside the run directory" in decision.why


def test_a_refusal_names_the_path_not_a_neighbouring_word() -> None:
    command = "sed -n '/x/p' notes.txt /home/someone/y"
    decision = policy.decide("Bash", {"command": command}, SCOPE)
    assert decision.why == "reaches outside the run directory (/home/someone/y)"
