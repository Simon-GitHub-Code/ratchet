"""The architecture check: the binding declares the parts and names a whole decision record.

ADR 0049. Each structural gap is one red line an agent can act on; an adopted repository that
predates the section gets the template's section from the upgrade, and the rest is owed.
"""

import shutil
from pathlib import Path

import pytest

import architecture
import conformance
from tests.test_conformance import BINDING, Mutate, pristine, remove, replace

RECORD = conformance.SELF_TEST_RECORD


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    shutil.copytree(pristine(), tmp_path / "repo", symlinks=True)
    return tmp_path / "repo"


def lines(root: Path) -> list[str]:
    return [str(finding) for finding in conformance.check_architecture(root)]


def test_a_declared_architecture_with_a_whole_record_is_green(repo: Path) -> None:
    assert lines(repo) == []


@pytest.mark.parametrize(
    ("mutate", "failure"),
    [
        (
            replace(BINDING, f"- Decision: `{RECORD}`", "- Decision: <the record>"),
            f"{BINDING}: the Architecture section's '- Decision:' line names no record",
        ),
        (
            replace(BINDING, f"- Decision: `{RECORD}`", "- Decision: docs/adr/0002.md"),
            f"{BINDING}: the Architecture section's '- Decision:' line names no record",
        ),
        (
            remove(RECORD),
            f"{BINDING}: the architecture decision record {RECORD} is not a record under",
        ),
        (
            replace(BINDING, f"`{RECORD}`", "`docs/adr/../../AGENTS.md`"),
            f"{BINDING}: the architecture decision record docs/adr/../../AGENTS.md is not",
        ),
        (
            replace(BINDING, f"`{RECORD}`", "`AGENTS.md`"),
            f"{BINDING}: the architecture decision record AGENTS.md is not a record",
        ),
        (
            replace(BINDING, conformance.SELF_TEST_PARTS, ""),
            f"{BINDING}: the Architecture section declares no part",
        ),
        (
            replace(BINDING, "| Part | Path | May depend on |", "| Layer | Path | May import |"),
            f"{BINDING}: the Architecture section declares no part",
        ),
        (
            replace(BINDING, "| rules | `src/rules/**` |", "| rules | <globs> |"),
            f"{BINDING}: the Architecture table's row 'rules' has a cell still to fill",
        ),
        (
            replace(BINDING, "| rules | `src/rules/**` |", "| rules | src/rules |"),
            f"{BINDING}: part 'rules' gives no path in backticks",
        ),
        (
            replace(BINDING, "`src/cli/**` | rules |", "`src/cli/**` | rules, ghost |"),
            f"{BINDING}: part 'command' may depend on 'ghost', which the Architecture table",
        ),
        (
            replace(RECORD, "## Decision\n", "## Choice\n"),
            f"{RECORD}: has no '## Decision' section",
        ),
        (
            replace(RECORD, "- Chosen by: the owner", "- Chosen by: <who>"),
            f"{RECORD}: says under '## Decision' no '- Chosen by:' line",
        ),
        (
            replace(RECORD, "- **One file**", "**One file**"),
            f"{RECORD}: lists 1 alternative under '## Considered options'; at least 2",
        ),
        (
            replace(RECORD, "## Considered options", "## Options"),
            f"{RECORD}: lists 0 alternatives under '## Considered options'",
        ),
        (
            replace(RECORD, "## Reversal cost", "## Reversal"),
            f"{RECORD}: says nothing under '## Reversal cost'",
        ),
    ],
)
def test_each_gap_is_one_red_line(repo: Path, mutate: Mutate, failure: str) -> None:
    mutate(repo)
    found = lines(repo)
    assert any(line.startswith(failure) for line in found), found
    assert all(line in conformance.failures(repo) for line in found)


def test_a_binding_without_the_section_gets_the_templates_and_the_rest_is_an_agents(
    repo: Path,
) -> None:
    # Why: an adopted repository predates the section. The upgrade adds the template's, whose
    # placeholders are then the agent's items that the owed ledger carries (ADR 0037).
    text = (repo / BINDING).read_text(encoding="utf-8")
    start = text.index(architecture.SECTION)
    end = text.index("\n## ", start + 1)
    (repo / BINDING).write_text(text[:start] + text[end + 1 :], encoding="utf-8")
    (finding,) = conformance.findings(repo)
    assert finding.problem.startswith("has no '## Architecture' section")
    assert isinstance(finding.fix, conformance.Auto)
    with conformance.made(repo, finding.path, finding.fix.want):
        after = lines(repo)
    assert after == [
        f"{BINDING}: {architecture.NO_RECORD}",
        f"{BINDING}: the Architecture table's row '<part>' has a cell still to fill",
    ]


def test_a_missing_binding_is_left_to_the_binding_check(repo: Path) -> None:
    remove(BINDING)(repo)
    assert lines(repo) == []


def test_the_record_readers_take_only_structure() -> None:
    whole = (
        "# A\n\n## Decision\n\nIt.\n\n- Chosen by: the recommendation\n\n"
        "## Considered options\n\n- **B**: no.\n- **C**: no.\n\n"
        "## Reversal cost\n\nA day to move the store, nothing else redone.\n"
    )
    assert architecture.record_problems(whole) == []
