"""The conformance checks of the formal model: the Triggers row, the section and the proof."""

from pathlib import Path

import pytest

import application_types
import conformance
import formal_model
import owed
from tests.repos import commit, git, repository, write

BINDING = "docs/process/binding.md"
BITES = "quality/bites.toml"
MANIFEST = "guards/manifest.toml"
ROW = "| formal verification of state | a verifier | 2 | | `just formal` |"
SECTION = (
    "\n## Formal verification\n\n- Model: `formal/**`\n- Mirrors: `src/**/*.py`\n- Synced: {}\n"
)
GROUP = (
    '[[group]]\nname = "formal"\nreason = "Formal models are the design the code mirrors."\n'
    'paths = ["formal/**"]\n'
)
BITE = (
    '[[bite]]\ngate = "formal"\nname = "departs"\nproof = "code-departs-from-model"\n'
    'write = {{ path = "{path}", content = "x" }}\nexpect = "red"\n'
)


def binding(rows: str = ROW, section: str = "", state: str = "`src/**/*.py`") -> str:
    return (
        "# Binding\n\n- Application types: service\n- Life events: none\n\n## Gates\n\n"
        "| Question | Tool | Tier | Threshold source | Entry point |\n"
        f"| --- | --- | --- | --- | --- |\n{rows}\n\n## Triggers\n\n"
        f"| Kind | Paths | Pattern |\n| --- | --- | --- |\n| state-machine | {state} | |\n{section}"
    )


def project(tmp_path: Path, text: str, bite: str = "src/a.py") -> tuple[Path, str]:
    root = repository(
        tmp_path,
        {
            BINDING: text,
            BITES: BITE.format(path=bite),
            MANIFEST: GROUP,
            "src/a.py": "a = 1\n",
            "formal/m.md": "m\n",
        },
    )
    return root, git(root, "rev-parse", "HEAD")


def found(
    root: Path, check: conformance.Check = conformance.check_formal_verification
) -> list[str]:
    return [str(finding) for finding in check(root)]


def filled(tmp_path: Path, section: str = SECTION) -> tuple[Path, str]:
    root, head = project(tmp_path, binding())
    write(root, BINDING, binding(section=section.format(head)))
    commit(root, "chore(method): bind the formal model")
    return root, head


def test_a_repository_that_binds_no_per_kind_row_and_writes_no_section_is_asked_nothing(
    tmp_path: Path,
) -> None:
    root, _ = project(
        tmp_path, binding(rows="| formal verification of state | none: no | 2 | | `just formal` |")
    )
    assert found(root) == []


def test_a_bound_per_kind_row_asks_for_the_section_and_a_template_one_for_its_lines(
    tmp_path: Path,
) -> None:
    root, _ = project(tmp_path, binding())
    [problem] = found(root)
    assert "has no '## Formal verification' section" in problem
    template = (
        "\n## Formal verification\n\n- Model: <`globs`>\n- Mirrors: <`globs`>\n- Synced: <c>\n"
    )
    write(root, BINDING, binding(section=template))
    [problem] = found(root)
    assert "still holds the template's placeholders" in problem


def test_a_filled_section_with_a_bite_inside_the_mirror_is_clean(tmp_path: Path) -> None:
    root, _ = filled(tmp_path)
    assert found(root) == []


def test_a_glob_that_matches_no_tracked_file_is_found(tmp_path: Path) -> None:
    section = SECTION.replace("`formal/**`", "`models/**`").replace("`src/**/*.py`", "`lib/**`")
    root, _ = filled(tmp_path, section)
    problems = found(root)
    assert any(
        "the '- Model:' glob `models/**`" in p and "matches no tracked file" in p for p in problems
    )
    assert any("the '- Mirrors:' glob `lib/**`" in p for p in problems)


def test_a_model_path_the_formal_group_does_not_hold_is_found(tmp_path: Path) -> None:
    root, _ = filled(tmp_path)
    write(root, MANIFEST, GROUP.replace("formal/**", "other/**"))
    commit(root, "chore: the group moves")
    [problem] = found(root)
    assert problem.startswith("guards/manifest.toml: `formal/m.md` is a '- Model:' path")
    write(root, MANIFEST, "not toml [")
    commit(root, "chore: a broken manifest")
    assert found(root) == []


def test_a_clone_with_no_default_branch_holds_no_group_to_ask(tmp_path: Path) -> None:
    root, _ = filled(tmp_path)
    git(root, "branch", "-m", "main", "trunk")
    assert conformance.default_ref(root) is None
    assert found(root) == []


@pytest.mark.parametrize("synced", ["", "f" * 40])
def test_a_sync_commit_that_is_absent_or_not_held_is_found(tmp_path: Path, synced: str) -> None:
    root, head = filled(tmp_path)
    write(root, BINDING, (root / BINDING).read_text().replace(head, synced))
    [problem] = found(root)
    if synced:
        assert f"the '- Synced:' commit {synced} is not in this clone; fetch it with" in problem
        assert f"`git fetch origin {synced}`" in problem
    else:
        assert problem.endswith("has no '- Synced:' commit")


def test_a_sync_commit_off_the_line_of_head_is_found(tmp_path: Path) -> None:
    root, head = filled(tmp_path)
    git(root, "switch", "-q", "-c", "side")
    write(root, "side.txt", "s\n")
    side = commit(root, "chore: a side commit")
    git(root, "switch", "-q", "main")
    write(root, BINDING, (root / BINDING).read_text().replace(head, side))
    [problem] = found(root)
    assert problem.endswith(f"the '- Synced:' commit {side} is not an ancestor of HEAD")


@pytest.mark.parametrize(
    ("bite", "state"),
    [
        ("formal/m.md", "`src/**/*.py`"),
        ("src/a.py", "`lib/**`"),
        ("README.md", "`src/**/*.py`"),
        ("src/a.py", ""),
    ],
    ids=["in the model", "outside the kind", "outside the mirror", "no row for the kind"],
)
def test_a_bound_row_needs_a_proof_bite_planted_in_its_mirror_and_kind_and_not_its_model(
    tmp_path: Path, bite: str, state: str
) -> None:
    root, head = project(tmp_path, binding(section=SECTION.format("x"), state=state), bite)
    write(root, BINDING, binding(section=SECTION.format(head), state=state))
    problems = [p for p in found(root) if p.startswith(BITES)]
    assert len(problems) == 1
    assert 'has proof = "code-departs-from-model"' not in problems[0]
    assert "needs a [[bite]] of gate 'formal'" in problems[0]
    assert "'formal verification of state'" in problems[0]


def test_a_gate_with_no_registry_or_no_bite_is_asked_for_one(tmp_path: Path) -> None:
    root, head = project(tmp_path, binding())
    write(root, BINDING, binding(section=SECTION.format(head)))
    (root / BITES).unlink()
    [problem] = found(root)
    assert problem.startswith(f"{BITES}: the bound row")


def test_the_triggers_row_of_a_kind_is_a_check_of_its_own_that_the_fill_writes() -> None:
    assert conformance.rule_of(conformance.check_trigger_kinds) == "trigger_kinds"
    assert "trigger_kinds" in owed.FILL_FIRST
    assert "triggers" not in owed.FILL_FIRST


def test_the_state_machine_row_is_asked_of_every_type_and_its_absence_is_the_fills(
    tmp_path: Path,
) -> None:
    text = binding().replace("| state-machine | `src/**/*.py` | |\n", "")
    root, _ = project(tmp_path, text)
    problems = found(root, conformance.check_trigger_kinds)
    assert any("the Triggers table has no row for kind 'state-machine'" in p for p in problems)
    assert not any("kind 'state-machine'" in str(f) for f in conformance.check_triggers(root))
    assert application_types.load().kinds["state-machine"].startswith("a fixed set of named states")


def test_a_binding_without_a_triggers_section_leaves_the_kinds_to_the_triggers_check(
    tmp_path: Path,
) -> None:
    root, _ = project(tmp_path, "# Binding\n\n- Application types: service\n")
    assert found(root, conformance.check_trigger_kinds) == []
    assert formal_model.read("# Binding\n") is None


def fired_row(question: str, trigger: str) -> conformance.Finding:
    kind = trigger.removeprefix("code:")
    fired = conformance.Fired({kind: ["src/a.py:1"]}, frozenset())
    return conformance.dropped_row(
        "the Gates table has no row", question, trigger=trigger, fired=fired
    )


@pytest.mark.parametrize(
    ("question", "trigger"),
    [
        ("formal verification of concurrency", "code:concurrency"),
        ("formal verification of untrusted input", "code:untrusted-input"),
        ("formal verification of state", "code:state-machine"),
    ],
)
def test_a_fired_formal_row_is_written_later_so_it_is_owed_at_once(
    question: str, trigger: str
) -> None:
    found = fired_row(question, trigger)
    assert found.problem.endswith(
        f"add the template's row as `later: {trigger}`: it has fired here, so the row is owed"
    )
    assert isinstance(found.fix, conformance.Edit)
    assert found.fix.does == f"add the '{question}' row as `later: {trigger}`"


def test_a_fired_row_that_is_not_formal_is_still_bound_and_never_written_later() -> None:
    found = fired_row("race and memory detectors", "code:concurrency")
    assert found.problem.endswith(
        "add the template's row and bind it: its trigger `code:concurrency` has fired here"
    )
    assert isinstance(found.fix, conformance.Agent)
