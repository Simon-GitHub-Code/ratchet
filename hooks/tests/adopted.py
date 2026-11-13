"""Adopted repositories as an earlier plugin's adopt templates left them, built from its history.

Each is the init and adopt templates at a release commit with the placeholders filled as adoption
filled them, the permission rules written by that release's own ``settings_rules.py``, and two
pieces of the project's own: a setup step in ``deep.yml`` and a command in the ``bite`` recipe,
which an upgrade must keep. Nothing here comes from a real project.
"""

import atexit
import functools
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import settings_rules
from tests.repos import git, repository
from tests.support import PLUGIN_ROOT

RELEASES = {
    "0.8.2": "4cdf6b9ee51c07c2d31e8311d02ac20c38cc9c8a",
    "0.9.2": "05634d2b123c85edb023cbc94b276fb2d1225eab",
}
TEMPLATES = ("skills/init/templates/", "skills/adopt/templates/")
UNBOUND_GROUP = re.compile(r"paths = \[\n(?:\s*# bind:[^\n]*\n)+\]")
EMPTY_TOOL_CELL = re.compile(r"^(\| [^|\n]+ \|) \|", re.MULTILINE)
TOOLCHAIN = "      # bind: toolchain setup, pinned, with the setup action's own cache on\n"
PROJECT_STEP = (
    "      - name: Install the browsers the end-to-end gate drives\n"
    "        run: ./scripts/install-browsers\n"
)
BITE_PLACEHOLDER = (
    '    @echo "bind: run every project fitness test and gate tool against its synthetic'
    ' violation"; exit 1\n'
)
PROJECT_BITE = "    ./scripts/project-bites --all\n"


def history(*args: str) -> str:
    """What git prints for ``args`` in the plugin's own repository, which must hold its history."""
    try:
        return git(PLUGIN_ROOT, *args)
    except subprocess.CalledProcessError as error:
        message = f"the adopted fixtures read the plugin's history; fetch it whole: {error.stderr}"
        raise RuntimeError(message) from error


def template_files(commit: str) -> dict[str, str]:
    """Every init and adopt template file at ``commit``, by the path adoption wrote it to."""
    names = history("ls-tree", "-r", "--name-only", commit, "--", *TEMPLATES).splitlines()
    files = {}
    for name in names:
        target = next(name.removeprefix(prefix) for prefix in TEMPLATES if name.startswith(prefix))
        files[target] = history("show", f"{commit}:{name}") + "\n"
    return files


def fill(files: dict[str, str], commit: str) -> dict[str, str]:
    """The placeholders adoption filled, and the project's own setup step and bite command."""
    filled = dict(files)
    for name in [name for name in filled if name.startswith(".github/workflows/")]:
        filled[name] = filled[name].replace("<ratchet-sha>", commit)
    filled[".github/workflows/deep.yml"] = filled[".github/workflows/deep.yml"].replace(
        TOOLCHAIN, PROJECT_STEP
    )
    filled["justfile"] = filled["justfile"].replace(BITE_PLACEHOLDER, PROJECT_BITE)
    filled["GLOSSARY.md"] = filled["GLOSSARY.md"].replace("<project>", "the project")
    filled["AGENTS.md"] = filled["AGENTS.md"].replace("# <project>", "# The project")
    filled["guards/manifest.toml"] = UNBOUND_GROUP.sub(
        'paths = [\n    "bound/**",\n]', filled["guards/manifest.toml"]
    )
    binding = EMPTY_TOOL_CELL.sub(r"\1 a tool |", filled["docs/process/binding.md"])
    filled["docs/process/binding.md"] = binding.replace("<The task runner:", "`just`. <The runner:")
    return filled


def release_settings(root: Path, commit: str) -> None:
    """``.claude/settings.json`` as the release's own ``settings_rules.py --write`` wrote it."""
    script = root.parent / f"settings_rules-{commit[:7]}.py"
    script.write_text(history("show", f"{commit}:scripts/settings_rules.py"), encoding="utf-8")
    subprocess.run(  # noqa: S603 # -- a fixed argv, the release's own script
        [sys.executable, str(script), "--root", str(root), "--write"],
        check=True,
        capture_output=True,
    )
    script.unlink()


def build(root: Path, version: str) -> Path:
    """A committed repository as the adopt templates of ``version`` left it."""
    commit = RELEASES[version]
    repository(root, fill(template_files(commit), commit))
    release_settings(root, commit)
    git(root, "commit", "-q", "-am", "chore(method): adopt the Ratchet method")
    return root


@functools.cache
def adopted(version: str) -> Path:
    """The repository adoption at ``version`` left, built once per process."""
    scratch = Path(tempfile.mkdtemp(prefix=f"ratchet-adopted-{version}-"))
    atexit.register(shutil.rmtree, scratch, ignore_errors=True)
    return build(scratch / "repo", version)


@functools.cache
def half_done() -> Path:
    """A 0.9.2 adoption whose permission rules a newer plugin rewrote: the pin did not move.

    Its workflows still check the plugin out at the 0.9.2 commit, so CI runs 0.9.2's
    ``settings-check`` against rules only a newer plugin writes, and main is red.
    """
    scratch = Path(tempfile.mkdtemp(prefix="ratchet-half-done-"))
    atexit.register(shutil.rmtree, scratch, ignore_errors=True)
    root = scratch / "repo"
    shutil.copytree(adopted("0.9.2"), root, symlinks=True)
    settings_rules.write(root)
    git(root, "commit", "-q", "-am", "chore(method): regenerate the permission rules")
    return root


def copy(source: Path, target: Path) -> Path:
    """A copy of a built repository, history included, for one test to change."""
    shutil.copytree(source, target, symlinks=True)
    return target
