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
LATER = {
    "0.12.0": "7ebb02d14e4c6932c4bbd71c58c97ac30e519508",
    "0.12.3": "429b3bfd45d8df7aa70b04599984fcbc66c6e41c",
    "0.12.4": "31aaaf1c987b4c681d8604f1645132d3b58ebb76",
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
SECONDS_COMMENT = (
    "# [ceilings.seconds]\n# tier0 = 60\n# tier1 = 600\n# tier2 = 3600\n# bite-all = 3600\n"
)
SECONDS_WITHOUT_BITE_ALL = "[ceilings.seconds]\ntier1 = 600\ntier2 = 3600\n"
PROJECT_BITE = "    ./scripts/project-bites --all\n"


def history(*args: str) -> str:
    try:
        return git(PLUGIN_ROOT, *args)
    except subprocess.CalledProcessError as error:
        message = f"the adopted fixtures read the plugin's history; fetch it whole: {error.stderr}"
        raise RuntimeError(message) from error


def template_files(commit: str) -> dict[str, str]:
    names = history("ls-tree", "-r", "--name-only", commit, "--", *TEMPLATES).splitlines()
    files = {}
    for name in names:
        target = next(name.removeprefix(prefix) for prefix in TEMPLATES if name.startswith(prefix))
        files[target] = history("show", f"{commit}:{name}") + "\n"
    return files


def fill(files: dict[str, str], commit: str) -> dict[str, str]:
    filled = dict(files)
    for name in [name for name in filled if name.startswith(".github/workflows/")]:
        filled[name] = filled[name].replace("<ratchet-sha>", commit)
    filled[".github/workflows/deep.yml"] = filled[".github/workflows/deep.yml"].replace(
        TOOLCHAIN, PROJECT_STEP
    )
    filled["justfile"] = filled["justfile"].replace(BITE_PLACEHOLDER, PROJECT_BITE)
    filled["quality/thresholds.toml"] = filled["quality/thresholds.toml"].replace(
        SECONDS_COMMENT, SECONDS_WITHOUT_BITE_ALL
    )
    filled["GLOSSARY.md"] = filled["GLOSSARY.md"].replace("<project>", "the project")
    filled["AGENTS.md"] = filled["AGENTS.md"].replace("# <project>", "# The project")
    filled["guards/manifest.toml"] = UNBOUND_GROUP.sub(
        'paths = [\n    "bound/**",\n]', filled["guards/manifest.toml"]
    )
    binding = EMPTY_TOOL_CELL.sub(r"\1 a tool |", filled["docs/process/binding.md"])
    filled["docs/process/binding.md"] = binding.replace("<The task runner:", "`just`. <The runner:")
    return filled


def release_settings(root: Path, commit: str) -> None:
    script = root.parent / f"settings_rules-{commit[:7]}.py"
    script.write_text(history("show", f"{commit}:scripts/settings_rules.py"), encoding="utf-8")
    subprocess.run(  # noqa: S603 # -- a fixed argv, the release's own script
        [sys.executable, str(script), "--root", str(root), "--write"],
        check=True,
        capture_output=True,
    )
    script.unlink()


def build(root: Path, version: str) -> Path:
    commit = {**RELEASES, **LATER}[version]
    repository(root, fill(template_files(commit), commit))
    if version in RELEASES:
        release_settings(root, commit)
        git(root, "commit", "-q", "-am", "chore(method): adopt the Ratchet method")
    return root


@functools.cache
def adopted(version: str) -> Path:
    scratch = Path(tempfile.mkdtemp(prefix=f"ratchet-adopted-{version}-"))
    atexit.register(shutil.rmtree, scratch, ignore_errors=True)
    return build(scratch / "repo", version)


@functools.cache
def half_done() -> Path:
    scratch = Path(tempfile.mkdtemp(prefix="ratchet-half-done-"))
    atexit.register(shutil.rmtree, scratch, ignore_errors=True)
    root = scratch / "repo"
    shutil.copytree(adopted("0.9.2"), root, symlinks=True)
    settings_rules.regenerate(root)
    git(root, "commit", "-q", "-am", "chore(method): regenerate the permission rules")
    return root


def copy(source: Path, target: Path) -> Path:
    shutil.copytree(source, target, symlinks=True)
    return target
