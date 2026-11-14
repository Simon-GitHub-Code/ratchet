"""Dependency-only changes to a guard path, read through the binding's contract (ADR 0042).

The stack here is made up (``tests/dependency_stack.py``): the plugin reads no manifest or lockfile
format, so a pass on a stack no tool writes shows it knows none.
"""

import contextlib
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

import conformance
from ratchet_guards import dependencies, dependency_entry, diff
from tests import dependency_stack as stack
from tests.dependency_stack import pin, read
from tests.repos import branch, commit, git, repository, write
from tests.test_conformance import pristine
from tests.test_diff import fake_git

CONTRACT = dependencies.parse(stack.BINDING)
assert CONTRACT is not None


def verdict(path: str, before: str | None, after: str | None) -> list[str] | str:
    """What the contract says of one changed path: each change as text, or why it is not."""
    found = dependencies.judge(CONTRACT, {path: (before, after)}, read)[path]
    if isinstance(found, dependencies.NotDependencyOnlyError):
        return str(found)
    return [str(change) for change in found]


def shown(path: str, before: str, after: str) -> list[str]:
    result = verdict(path, before, after)
    assert isinstance(result, list), result
    return result


def reason(path: str, before: str | None, after: str | None) -> str:
    result = verdict(path, before, after)
    assert isinstance(result, str), result
    return result


# --- the section ----------------------------------------------------------------------------


def test_a_binding_without_the_section_has_no_contract() -> None:
    assert dependencies.parse("# Binding\n\n## Gates\n") is None
    assert dependencies.parse(None) is None


def test_the_section_names_files_registries_and_an_entry_point() -> None:
    assert CONTRACT is not None
    assert CONTRACT.manifests == ("*.manifest",)
    assert CONTRACT.lockfiles == ("*.lock",)
    assert CONTRACT.registries == {stack.REGISTRY}
    assert CONTRACT.entry
    assert CONTRACT.problems == ()


def text_with(old: str, new: str) -> str:
    assert old in stack.BINDING
    return stack.BINDING.replace(old, new)


@pytest.mark.parametrize(
    ("old", "new", "problem"),
    [
        ("`*.manifest`", "<globs>", "'- Manifests:' is still the template's"),
        ("`*.manifest`", "none: x", "'- Manifests: none:' needs the reason"),
        ("`*.manifest`", "*.manifest", "names its files in backticks"),
        ("`*.lock`", "", "'- Lockfiles:' is still the template's"),
        ("`just dependencies`", "<x>", "'- Entry point:' is `just dependencies`, or `none:"),
        ("`just dependencies`", "none: x", "'- Entry point:' is `just dependencies`, or `none:"),
        (
            "`just dependencies`",
            "`just deps`",
            "'- Entry point:' is `just dependencies`, or `none:",
        ),
        (
            "- Registries: `https://registry.example`\n",
            "",
            "'- Registries:' is still the template's",
        ),
        ("`https://registry.example`", "<the source>", "'- Registries:' is still the template's"),
    ],
)
def test_a_section_that_is_unfilled_or_malformed_says_what_is_wrong(
    old: str, new: str, problem: str
) -> None:
    found = dependencies.parse(text_with(old, new))
    assert found is not None
    assert any(problem in line for line in found.problems), found.problems


def test_none_with_a_reason_is_a_declaration_and_none_registries_are_none() -> None:
    said = text_with("`*.manifest`", "`none: this stack has no manifest file`")
    said = said.replace("`https://registry.example`", "`none`")
    said = said.replace("`just dependencies`", "none: nothing here reads them")
    found = dependencies.parse(said)
    assert found is not None
    assert (found.manifests, found.registries, found.entry, found.problems) == (
        (),
        frozenset(),
        False,
        (),
    )


def test_a_path_is_a_declared_file_or_a_snapshot_of_one() -> None:
    assert CONTRACT is not None
    assert CONTRACT.original("app.manifest") == "app.manifest"
    assert CONTRACT.original("quality/snapshots/app.lock") == "app.lock"
    assert CONTRACT.original("quality/snapshots/web__a.lock") is None, "a.lock is not at the root"
    assert CONTRACT.original("README.md") is None
    assert not CONTRACT.recognised("justfile")
    assert CONTRACT.is_lockfile("quality/snapshots/app.lock")
    assert not CONTRACT.is_lockfile("app.manifest")
    assert not CONTRACT.is_lockfile("README.md")


def test_a_path_that_leaves_the_directory_the_entry_point_reads_is_not_a_declared_file() -> None:
    wide = dependencies.Contract(manifests=("**",), entry=True)
    assert wide.original("a/../b.manifest") is None
    assert wide.original("quality/snapshots/..__x") is None
    assert wide.original("quality/snapshots/__x") is None, "an absolute path"
    assert wide.original("a/b.manifest") == "a/b.manifest"


# --- a manifest ------------------------------------------------------------------------------


def test_new_dependencies_and_a_version_bump_are_dependency_only() -> None:
    after = stack.MANIFEST.replace("own-version 1.0", "own-version 2.0")
    after += "use dev delta ~2\nuse main beta 1.*\n"
    assert shown("app.manifest", stack.MANIFEST, after) == [
        "delta (dev) (none) -> ~2",
        "beta (main) (none) -> 1.*",
    ]


def test_a_setting_beside_a_dependency_is_not_dependency_only() -> None:
    after = stack.MANIFEST.replace("strict on", "strict off") + "use main beta 1\n"
    assert "more than the dependency tables" in reason("app.manifest", stack.MANIFEST, after)


@pytest.mark.parametrize(
    ("after", "said"),
    [
        (
            stack.MANIFEST.replace("alpha ^1.0", "alpha ^0.1"),
            "it changes alpha in main, which was there before",
        ),
        (stack.MANIFEST.replace("use main alpha ^1.0\n", ""), "it removes alpha from main"),
        (
            stack.MANIFEST + "use main beta @elsewhere\n",
            "beta in main is fetched by @elsewhere, not from the registry by version",
        ),
        (stack.MANIFEST + "use main alpha ^1.0\n", "alpha is listed twice in main"),
    ],
)
def test_a_manifest_change_that_is_not_dependency_only_says_why(after: str, said: str) -> None:
    assert reason("app.manifest", stack.MANIFEST, after) == said


# --- a lockfile ------------------------------------------------------------------------------


def lock(*pins: str) -> str:
    return "setting frozen yes\n" + "".join(pins)


def test_a_lockfile_reports_added_and_raised_versions() -> None:
    after = lock(pin("alpha", "1.3.0"), pin("gamma", "3.0.0"), pin("beta", "0.9"))
    assert shown("app.lock", stack.LOCK, after) == [
        "alpha (main) 1.2.0 -> 1.3.0",
        "beta (main) (none) -> 0.9",
    ]


def test_a_dropped_package_is_reported_and_a_rewrite_of_nothing_is_none() -> None:
    assert shown("app.lock", stack.LOCK, lock(pin("alpha", "1.2.0"))) == [
        "gamma (main) 3.0.0 -> (none)"
    ]
    reordered = lock(pin("gamma", "3.0.0"), pin("alpha", "1.2.0"))
    assert shown("app.lock", stack.LOCK, reordered) == []


@pytest.mark.parametrize(
    ("after", "said"),
    [
        (
            lock(pin("alpha", "1.2.0", sum_="swapped"), pin("gamma", "3.0.0")),
            "alpha 1.2.0 changed in place (https://registry.example -> https://registry.example)",
        ),
        (
            lock(pin("alpha", "1.2.0", source="https://other.example"), pin("gamma", "3.0.0")),
            "alpha 1.2.0 changed in place (https://registry.example -> https://other.example)",
        ),
        (
            lock(pin("alpha", "1.3.0", source="https://other.example"), pin("gamma", "3.0.0")),
            "alpha 1.3.0 comes from https://other.example, not a known registry",
        ),
        (
            lock(pin("alpha", "1.1.0"), pin("gamma", "3.0.0")),
            "alpha falls from 1.2.0 to 1.1.0",
        ),
        (
            lock(pin("alpha", "1.2.0"), pin("alpha", "1.1.0"), pin("gamma", "3.0.0")),
            "alpha falls from 1.2.0 to 1.1.0",
        ),
        (
            stack.LOCK.replace("setting frozen yes", "setting frozen no"),
            "it changes more than the dependency tables and the package version",
        ),
    ],
)
def test_a_lockfile_rollback_or_swap_is_not_dependency_only(after: str, said: str) -> None:
    assert reason("app.lock", stack.LOCK, after) == said


def test_a_version_that_is_not_from_a_registry_is_not_a_known_one() -> None:
    direct = stack.LOCK + "pin delta 1.0 1 direct git+https://x sum-d one\n"
    assert reason("app.lock", stack.LOCK, direct) == (
        "delta 1.0 comes from git+https://x, not a known registry"
    )
    local = stack.LOCK + "pin app 1.0 1 local . - one\n"
    assert reason("app.lock", stack.LOCK, local) == "app 1.0 comes from ., not a known registry"


def test_a_local_version_that_stays_where_it_was_is_unchanged() -> None:
    before = stack.LOCK + "pin app 1.0 1 local . - one\n"
    assert (
        shown("app.lock", before, before.replace("setting frozen yes", "setting frozen yes")) == []
    )
    moved = before.replace("local . -", "local ../x -")
    assert "app 1.0 changed in place (. -> ../x)" in reason("app.lock", before, moved)


def test_the_first_package_comes_from_a_registry_the_binding_names() -> None:
    empty = "setting frozen yes\n"
    assert shown("app.lock", empty, empty + pin("alpha", "1.0")) == ["alpha (main) (none) -> 1.0"]
    elsewhere = empty + pin("alpha", "1.0", source="https://other.example")
    assert "not a known registry" in reason("app.lock", empty, elsewhere)


def test_a_registry_the_lockfile_already_uses_is_known() -> None:
    before = stack.LOCK + pin("beta", "1.0", source="https://other.example")
    after = before + pin("delta", "2.0", source="https://other.example")
    assert shown("app.lock", before, after) == ["delta (main) (none) -> 2.0"]


def test_versions_beside_each_other_hold_only_the_highest() -> None:
    before = lock(pin("alpha", "2.0", sides="many"))
    older = before + pin("alpha", "1.0", sides="many")
    assert shown("app.lock", before, older) == ["alpha (main) 2.0 -> 1.0, 2.0"]
    gone = lock(pin("alpha", "1.0", sides="many"))
    assert reason("app.lock", older, gone) == "alpha falls from 2.0 to 1.0"
    single = before.replace("many", "one") + pin("alpha", "1.0")
    assert (
        reason("app.lock", before.replace("many", "one"), single) == "alpha falls from 2.0 to 1.0"
    )


def test_a_pre_release_sorts_below_its_release_as_the_entry_point_orders_it() -> None:
    before = lock(pin("alpha", "1.0"))
    pre = "pin alpha 1.1-rc 1.0.5 registry https://registry.example sum-rc one\n"
    final = "pin alpha 1.1 1.1 registry https://registry.example sum-final one\n"
    assert shown("app.lock", before, "setting frozen yes\n" + pre) == ["alpha (main) 1.0 -> 1.1-rc"]
    assert shown("app.lock", "setting frozen yes\n" + pre, "setting frozen yes\n" + final) == [
        "alpha (main) 1.1-rc -> 1.1"
    ]
    assert "falls" in reason(
        "app.lock", "setting frozen yes\n" + final, "setting frozen yes\n" + pre
    )


@pytest.mark.parametrize(
    ("lines", "said"),
    [
        (
            [pin("alpha", "1.0"), pin("alpha", "1.0", sum_="other")],
            "alpha 1.0 is locked twice with different contents",
        ),
        (
            [pin("alpha", "1.0", sides="many"), pin("alpha", "2.0")],
            "alpha is given both as one version and as many in main",
        ),
    ],
)
def test_a_lockfile_that_contradicts_itself_is_not_dependency_only(
    lines: list[str], said: str
) -> None:
    assert reason("app.lock", stack.LOCK, "setting frozen yes\n" + "".join(lines)) == said


def test_the_same_version_written_twice_alike_is_one_version() -> None:
    twice = stack.LOCK + pin("alpha", "1.2.0")
    assert shown("app.lock", stack.LOCK, twice) == []


def test_a_new_version_the_change_does_not_show_is_refused() -> None:
    lying = {"app.lock": (stack.LOCK, stack.LOCK + pin("beta", "1.0"))}
    old = read({"app.lock": stack.LOCK})
    new = read({"app.lock": stack.LOCK + pin("beta", "1.0")}).replace("\t1.0\t", "\t7.7\t")
    outputs = iter([old, new])
    found = dependencies.judge(CONTRACT, lying, lambda _: next(outputs))["app.lock"]
    assert str(found) == "the entry point reports '7.7' for beta, which no added line shows"


def test_a_spec_the_change_does_not_show_is_refused() -> None:
    after = stack.MANIFEST + "use main beta 1\n"
    outputs = iter(
        [
            read({"app.manifest": stack.MANIFEST}),
            read({"app.manifest": after}).replace("\t1\t", "\t9\t"),
        ]
    )
    found = dependencies.judge(
        CONTRACT, {"app.manifest": (stack.MANIFEST, after)}, lambda _: next(outputs)
    )["app.manifest"]
    assert str(found) == "the entry point reports '9' for beta, which no added line shows"


# --- what the entry point prints ----------------------------------------------------------------

REST = "rest\tapp.lock\tx\n"
LOCKED = "locked\tapp.lock\tmain\tbeta\t1\t1\tregistry\thttps://registry.example\tsum\tone\n"
DECLARED = "declared\tapp.manifest\tmain\tbeta\t1\tregistry\n"


def printing(*texts: str) -> object:
    outputs = iter(texts)
    return lambda _: next(outputs)


@pytest.mark.parametrize(
    ("printed", "said"),
    [
        ("loose words\n", "the entry point prints a line that is not a record: 'loose words'"),
        (
            "rest\tapp.lock\n",
            "the entry point prints a line that is not a record: 'rest\\tapp.lock'",
        ),
        (REST + REST, "the entry point prints two rest lines for app.lock"),
        (
            REST + LOCKED.replace("registry\thttps", "elsewhere\thttps"),
            "a locked line it cannot be read from",
        ),
        (REST + LOCKED.replace("\tone\n", "\tsome\n"), "a locked line it cannot be read from"),
        (REST + LOCKED.replace("\t1\t1\t", "\t1\ta.b\t"), "a locked line it cannot be read from"),
        (
            REST + LOCKED.replace("\tsum\t", "\t\t"),
            "beta 1 has no integrity, so it cannot be shown unswapped",
        ),
        (
            "rest\tapp.manifest\tx\n" + DECLARED.replace("registry\n", "fetched\n"),
            "the entry point prints the origin 'fetched' for a declared entry",
        ),
        ("", "the entry point prints no rest line for it"),
    ],
)
def test_output_that_is_not_a_record_is_not_dependency_only(printed: str, said: str) -> None:
    path = "app.lock" if "app.manifest" not in printed else "app.manifest"
    found = dependencies.judge(
        CONTRACT,
        {path: (stack.LOCK, stack.LOCK + "x\n")},
        printing(printed, printed),  # type: ignore[arg-type]
    )[path]
    assert said in str(found)


def test_a_missing_rest_line_leaves_the_file_unjudged() -> None:
    nothing = dependencies.judge(
        CONTRACT,
        {"app.lock": (stack.LOCK, stack.LOCK + "x\n")},
        printing("", ""),  # type: ignore[arg-type]
    )["app.lock"]
    assert str(nothing) == "the entry point prints no rest line for it"


def test_blank_lines_in_the_output_are_ignored() -> None:
    out = read({"app.lock": stack.LOCK})
    assert dependencies.read_output("\n" + out + "\n\n").rest.keys() == {"app.lock"}


# --- what is judged ------------------------------------------------------------------------------


def never_run(_: Mapping[str, str]) -> str:
    message = "the entry point should not run"
    raise AssertionError(message)


def test_a_path_that_is_no_dependency_file_has_no_verdict_and_runs_nothing() -> None:
    assert dependencies.judge(CONTRACT, {"src/a.py": ("a", "b")}, never_run) == {}


def test_a_file_created_or_deleted_is_not_dependency_only() -> None:
    created = dependencies.judge(CONTRACT, {"app.lock": (None, stack.LOCK)}, never_run)
    assert str(created["app.lock"]) == "it is created or deleted"
    deleted = dependencies.judge(CONTRACT, {"app.manifest": (stack.MANIFEST, None)}, never_run)
    assert str(deleted["app.manifest"]) == "it is created or deleted"


def test_a_binding_that_names_no_entry_point_leaves_every_file_unjudged() -> None:
    silent = dependencies.Contract(("*.manifest",), ("*.lock",), entry=False)
    found = dependencies.judge(silent, {"app.lock": (stack.LOCK, stack.LOCK + "x")}, never_run)
    assert str(found["app.lock"]) == "the binding names no entry point to read dependencies with"


def test_a_snapshot_is_judged_as_the_file_it_copies() -> None:
    after = stack.MANIFEST + "use main beta 1\n"
    found = dependencies.judge(
        CONTRACT,
        {
            "app.manifest": (stack.MANIFEST, after),
            "quality/snapshots/app.manifest": (stack.MANIFEST, after),
        },
        read,
    )
    assert {path: [str(c) for c in found[path]] for path in found} == {  # type: ignore[union-attr]
        "app.manifest": ["beta (main) (none) -> 1"],
        "quality/snapshots/app.manifest": ["beta (main) (none) -> 1"],
    }


def test_a_snapshot_that_differs_from_the_file_it_copies_is_refused() -> None:
    after = stack.MANIFEST + "use main beta 1\n"
    found = dependencies.judge(
        CONTRACT,
        {
            "app.manifest": (stack.MANIFEST, after),
            "quality/snapshots/app.manifest": (stack.MANIFEST, after + "use main more 1\n"),
        },
        read,
    )
    assert str(found["quality/snapshots/app.manifest"]) == (
        "it differs from app.manifest, the file it copies"
    )
    assert isinstance(found["app.manifest"], list)


def test_an_entry_point_that_fails_refuses_every_file_it_was_to_read() -> None:
    def broken(_: Mapping[str, str]) -> str:
        message = "the entry point exited 3"
        raise dependencies.NotDependencyOnlyError(message)

    found = dependencies.judge(
        CONTRACT,
        {"app.lock": (stack.LOCK, stack.LOCK + pin("beta", "1")), "app.manifest": ("a", "b")},
        broken,
    )
    assert {str(v) for v in found.values()} == {"the entry point exited 3"}
    assert found.keys() == {"app.lock", "app.manifest"}


# --- the entry point, run as the merge base has it -----------------------------------------------


@pytest.fixture
def project(tmp_path: Path) -> Path:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    return repository(tmp_path, dict(stack.FILES))


def test_the_entry_point_reads_the_files_it_is_given_from_the_merge_bases_tree(
    project: Path,
) -> None:
    base = git(project, "rev-parse", "HEAD")
    branch(project)
    # The head's own reader prints a lie and a lock that is clean; only the base's runs.
    write(project, "tools/read_deps.py", "print('rest\\tapp.lock\\tx')\n")
    commit(project, "feat(core): the change")
    with dependency_entry.Entry(project, base) as run:
        out = run({"app.lock": stack.LOCK})
        assert "locked\tapp.lock\tmain\talpha\t1.2.0\t" in out
        assert run({"app.manifest": stack.MANIFEST}).count("declared\t") == 2
        assert run.runs == 2


def test_an_entry_that_never_ran_has_nothing_to_clean_up(project: Path) -> None:
    with dependency_entry.Entry(project, "HEAD") as run:
        assert run.scratch is None


def test_the_entry_point_is_bounded_and_every_failure_is_a_refusal(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused(files: Mapping[str, str]) -> str:
        with (
            dependency_entry.Entry(project, "HEAD") as run,
            pytest.raises(dependencies.NotDependencyOnlyError) as error,
        ):
            run(files)
        return str(error.value)

    monkeypatch.setattr(dependency_entry, "LIMIT", 10)
    assert refused({"app.lock": stack.LOCK}) == "the entry point printed more than it may"
    monkeypatch.setattr(dependency_entry, "LIMIT", 8_000_000)
    monkeypatch.setattr(shutil, "which", lambda _: None)
    assert "just is not on the path" in refused({})
    monkeypatch.undo()


@pytest.mark.parametrize(
    ("script", "said", "seconds"),
    [
        ("import sys\nsys.exit(3)\n", "the entry point exited 3", 60),
        (
            "import sys\nsys.stdout.buffer.write(b'\\xff\\xfe')\n",
            "printed what is not text",
            60,
        ),
        ("import time\ntime.sleep(30)\n", "the entry point ran over 1 seconds", 1),
    ],
)
def test_an_entry_point_that_fails_hangs_or_prints_binary_is_refused(
    project: Path, *, monkeypatch: pytest.MonkeyPatch, script: str, said: str, seconds: int
) -> None:
    write(project, "tools/read_deps.py", script)
    commit(project, "feat(core): a broken reader")
    monkeypatch.setattr(dependency_entry, "TIMEOUT", seconds)
    with (
        dependency_entry.Entry(project, "HEAD") as run,
        pytest.raises(dependencies.NotDependencyOnlyError, match=said),
    ):
        run({"app.lock": stack.LOCK})


def test_a_tree_that_cannot_be_copied_is_a_refusal(project: Path) -> None:
    with (
        dependency_entry.Entry(project, "no-such-revision") as run,
        pytest.raises(dependencies.NotDependencyOnlyError, match="could not be copied"),
    ):
        run({"app.lock": stack.LOCK})


# --- what guard-diff says of it --------------------------------------------------------------


def test_guard_diff_reads_the_changed_files_through_the_merge_bases_binding(tmp_path: Path) -> None:
    after = stack.MANIFEST + "use main beta 1.0\n"
    write(tmp_path, "app.manifest", after)
    write(tmp_path, "app.lock", stack.LOCK + pin("beta", "1.0"))
    git = fake_git(tmp_path, "", base_binding=stack.BINDING)

    def show(args: Sequence[str]) -> str | None:
        shown = {
            "abc123:app.manifest": stack.MANIFEST,
            "abc123:app.lock": stack.LOCK,
            "abc123:docs/process/binding.md": stack.BINDING,
        }
        return shown.get(args[1]) if args[0] == "show" else git(args)

    runs: list[str] = []

    def entry(_root: Path, rev: str) -> contextlib.AbstractContextManager[dependencies.Runner]:
        runs.append(rev)
        return contextlib.nullcontext(read)

    notes = diff.dependency_notes(
        show, "origin/main", tmp_path, paths=["app.manifest", "app.lock", "README.md"], entry=entry
    )
    assert notes == {
        "app.manifest": "dependencies added: beta (main) (none) -> 1.0",
        "app.lock": "lockfile: beta (main) (none) -> 1.0",
    }
    assert runs == ["abc123"]


def test_guard_diff_says_why_a_file_is_not_dependency_only(tmp_path: Path) -> None:
    write(tmp_path, "app.manifest", stack.MANIFEST.replace("strict on", "strict off"))
    gone = tmp_path / "app.lock"

    def show(args: Sequence[str]) -> str | None:
        shown = {
            "abc123:app.manifest": stack.MANIFEST,
            "abc123:app.lock": stack.LOCK,
            "abc123:docs/process/binding.md": stack.BINDING,
        }
        return shown.get(args[1]) if args[0] == "show" else fake_git(tmp_path, "")(args)

    notes = diff.dependency_notes(
        show,
        "origin/main",
        tmp_path,
        paths=["app.manifest", "app.lock"],
        entry=lambda _root, _rev: contextlib.nullcontext(read),
    )
    assert not gone.exists()
    assert notes["app.manifest"].startswith("not dependency-only: it changes more than")
    assert notes["app.lock"] == "not dependency-only: it is created or deleted"


def test_guard_diff_asks_nothing_when_no_path_is_a_dependency_file_or_there_is_no_contract(
    tmp_path: Path,
) -> None:
    def never(_root: Path, _rev: str) -> contextlib.AbstractContextManager[dependencies.Runner]:
        message = "the entry point should not run"
        raise AssertionError(message)

    assert diff.dependency_notes(fake_git(tmp_path, ""), "origin/main", tmp_path, paths=[]) == {}
    bare = fake_git(tmp_path, "", base_binding="# Binding\n")
    assert (
        diff.dependency_notes(bare, "origin/main", tmp_path, paths=["app.lock"], entry=never) == {}
    )
    named = fake_git(tmp_path, "", base_binding=stack.BINDING)
    assert (
        diff.dependency_notes(named, "origin/main", tmp_path, paths=["README.md"], entry=never)
        == {}
    )


# --- conformance -----------------------------------------------------------------------------

NONE = "`none: the self-test has no such file`"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    shutil.copytree(pristine(), tmp_path / "repo", symlinks=True)
    return tmp_path / "repo"


def found(root: Path) -> list[str]:
    return [
        f.problem for f in conformance.findings(root) if f.stops == conformance.DEPENDENCY_STOPS
    ]


def swap(root: Path, path: str, old: str, *, new: str) -> None:
    text = (root / path).read_text(encoding="utf-8")
    assert old in text, old
    (root / path).write_text(text.replace(old, new, 1), encoding="utf-8")


def named(
    root: Path, recipe: str = "dependencies dir:\n    python3 tools/read_deps.py {{dir}}"
) -> None:
    swap(
        root,
        "docs/process/binding.md",
        f"- Entry point: {NONE}",
        new="- Entry point: `just dependencies`",
    )
    text = (root / "justfile").read_text(encoding="utf-8")
    start = text.index("dependencies dir:")
    end = text.index("\n\n", start)
    (root / "justfile").write_text(text[:start] + recipe + text[end:], encoding="utf-8")


def test_a_section_that_says_none_with_a_reason_asks_nothing_of_the_justfile(repo: Path) -> None:
    assert found(repo) == []


def test_an_entry_point_with_a_recipe_that_takes_a_directory_is_green(repo: Path) -> None:
    named(repo)
    assert found(repo) == []


@pytest.mark.parametrize(
    ("recipe", "problem"),
    [
        ("dependencies:\n    true", "takes no directory"),
        ('dependencies dir:\n    @echo "bind: print the records"; exit 1', "`bind:` placeholder"),
    ],
)
def test_a_recipe_that_cannot_be_the_entry_point_is_red(
    repo: Path, recipe: str, problem: str
) -> None:
    named(repo, recipe)
    [said] = found(repo)
    assert problem in said


def test_an_entry_point_with_no_recipe_behind_it_is_red(repo: Path) -> None:
    named(repo)
    swap(repo, "justfile", "dependencies dir:", new="elsewhere dir:")
    [said] = found(repo)
    assert "dependencies" in said


def test_a_section_left_as_the_template_wrote_it_or_missing_is_red(repo: Path) -> None:
    swap(repo, "docs/process/binding.md", f"- Entry point: {NONE}", new="- Entry point: <x>")
    assert any("'- Entry point:' is `just dependencies`" in said for said in found(repo))
    swap(repo, "docs/process/binding.md", "## Dependencies", new="## Packages")
    assert any("has no '## Dependencies' section" in said for said in found(repo))
