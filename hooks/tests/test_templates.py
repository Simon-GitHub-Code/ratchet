"""The adopt templates: pinned, naming the plugin by commit, and the records' placeholders."""

import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

import pins

TEMPLATES = Path(__file__).resolve().parents[2] / "skills/adopt/templates"
ADOPT = (TEMPLATES.parent / "SKILL.md").read_text(encoding="utf-8")
JUSTFILE = (TEMPLATES / "justfile").read_text(encoding="utf-8")
BINDING = (TEMPLATES / "docs/process/binding.md").read_text(encoding="utf-8")
WORKFLOWS = {
    name: (TEMPLATES / ".github/workflows" / name).read_text(encoding="utf-8")
    for name in ("ci.yml", "deep.yml")
}
MATRIX_GATES = re.compile(r"^ +gate: \[([^\]]+)\]", re.MULTILINE)
SHARDS_STEP = re.compile(r"      - id: shards\n        run: \|\n((?:          .*\n)+)")


def tier_recipes(tier: str) -> list[str]:
    match = re.search(rf"^{tier}: (.+)$", JUSTFILE, re.MULTILINE)
    assert match, tier
    return match.group(1).split()


def matrix_gates(workflow: str) -> list[str]:
    return [gate for row in MATRIX_GATES.findall(WORKFLOWS[workflow]) for gate in row.split(", ")]


def recipe(name: str) -> str:
    match = re.search(rf"^{name}:\n((?:    .*\n)+)", JUSTFILE, re.MULTILINE)
    assert match, name
    return match.group(1)


def shards(binding: str, tmp_path: Path, workflow: str) -> tuple[int, str]:
    """Run the workflow's shard step as the runner would; its exit code and what it output."""
    step = SHARDS_STEP.search(WORKFLOWS[workflow])
    bash = shutil.which("bash")
    assert step, workflow
    assert bash
    (tmp_path / "docs/process").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs/process/binding.md").write_text(binding, encoding="utf-8")
    output = tmp_path / "output"
    output.write_text("", encoding="utf-8")
    done = subprocess.run(  # noqa: S603 # -- a fixed argv; the script is the template's own
        [bash, "-e", "-c", textwrap.dedent(step.group(1))],
        cwd=tmp_path,
        env={**os.environ, "GITHUB_OUTPUT": str(output)},
        capture_output=True,
        text=True,
        check=False,
    )
    return done.returncode, output.read_text(encoding="utf-8") + done.stdout


@pytest.mark.parametrize(("workflow", "tier"), [("ci.yml", "tier1"), ("deep.yml", "tier2")])
def test_ci_runs_every_gate_of_its_tier_once(workflow: str, tier: str) -> None:
    # Why: commitment 1, CI runs the same entry points. A gate the tier lists and no job runs is
    # a gate a pull request never meets.
    assert sorted(matrix_gates(workflow)) == sorted(tier_recipes(tier))


@pytest.mark.parametrize("workflow", sorted(WORKFLOWS))
def test_the_long_gates_run_one_job_per_shard_the_binding_counts(
    workflow: str, tmp_path: Path
) -> None:
    text = WORKFLOWS[workflow]
    assert "shard: ${{ fromJSON(needs.shape.outputs.shards) }}" in text
    assert "SHARD: ${{ matrix.shard }}" in text
    assert "SHARDS: ${{ needs.shape.outputs.count }}" in text
    assert shards(BINDING, tmp_path, workflow) == (0, "count=1\nlist=[1]\n")
    four = BINDING.replace("- Shards: 1", "- Shards: 4")
    assert shards(four, tmp_path, workflow) == (0, "count=4\nlist=[1,2,3,4]\n")
    for unreadable in ("- Shards: 0", "- Shards: a few", "- Shards: 2 for now"):
        code, said = shards(BINDING.replace("- Shards: 1", unreadable), tmp_path, workflow)
        assert code == 1, unreadable
        assert said == "docs/process/binding.md: no '- Shards: N' line\n"


def test_a_recipe_learns_its_shard_from_the_environment_and_is_whole_without_one() -> None:
    assert 'shard := env_var_or_default("SHARD", "1")' in JUSTFILE
    assert 'shards := env_var_or_default("SHARDS", "1")' in JUSTFILE
    for name in ("test", "mutation-changed", "mutation", "e2e"):
        assert "shard {{shard}} of {{shards}}" in recipe(name), name


def test_the_three_service_gates_arrive_bound_to_the_shipped_scripts() -> None:
    assert '/scripts/with_service.py" --boot "\' + boot + \'" --ready "\' + ready' in JUSTFILE
    assert recipe("smoke").strip() == "{{booted}}"
    assert recipe("load-smoke").startswith("    {{booted}} -- python3 ")
    assert 'scripts/load_smoke.py" --url "{{ready}}"' in recipe("load-smoke")
    assert 'scripts/telemetry_sink.py" --service "{{service}}" -- {{booted}} --' in recipe(
        "telemetry-e2e"
    )
    for name in ("boot", "ready", "service"):
        assert re.search(rf'^{name} := "bind: ', JUSTFILE, re.MULTILINE), name
    for question, script in (
        ("starts and answers", "with_service.py"),
        ("load smoke", "load_smoke.py"),
        ("telemetry arrives", "telemetry_sink.py"),
    ):
        assert f"\n| {question} | `ratchet` plugin `scripts/{script}`" in BINDING


def test_the_ci_templates_are_green_on_the_pins_gate() -> None:
    # Why: item 6 of the owner-free brief; a template that fails its own gate teaches the
    # wrong shape to every adopter.
    for name in ("ci.yml", "deep.yml"):
        path = f".github/workflows/{name}"
        text = (TEMPLATES / path).read_text(encoding="utf-8")
        assert pins.check_workflow(path, text) == [], name
        assert pins.check_images(path, text) == [], name
        assert 'repository: Simon-GitHub-Code/ratchet, ref: "<ratchet-sha>"' in text
        assert "RATCHET_PLUGIN: ${{ github.workspace }}/ratchet" in text


def test_adr_0001_names_the_version_and_the_commit() -> None:
    text = (TEMPLATES / "docs/adr/0001-adopt-the-ratchet-method.md").read_text(encoding="utf-8")
    assert "version <X.Y.Z> at commit `<ratchet-sha>`" in text
    assert "https://github.com/Simon-GitHub-Code/ratchet" in text


def test_adopt_names_the_two_skills_and_the_owner_s_steps() -> None:
    assert "`mattpocock-skills:grilling`" in ADOPT
    assert "`mattpocock-skills:domain-modeling`" in ADOPT
    assert "Run `mattpocock-skills:grill-with-docs`" not in ADOPT
    assert "do not create `DESIGN.md`" in ADOPT
    assert "`pre-commit install`" in ADOPT
    assert "scripts/hooks_installed.py" in ADOPT
    assert "`git config --unset core.hooksPath`" not in ADOPT
    assert "exempts a range made only of such commits" in ADOPT
    assert 'ref: "<ratchet-sha>"' in ADOPT
