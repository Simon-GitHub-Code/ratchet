from pathlib import Path

import pytest

import change_size
import commits
import markers
import pins
import prose
import snapshots
from tests import dependency_stack as stack
from tests.repos import branch, commit, git, repository, write

Out = pytest.CaptureFixture[str]


def run(gate: object, root: Path, *flags: str) -> int:
    main = getattr(gate, "main")  # noqa: B009 # -- the gate modules share one shape
    result = main(["gate", "--root", str(root), *flags])
    assert isinstance(result, int)
    return result


def test_markers_rise_is_red_and_paydown_is_green(tmp_path: Path, capsys: Out) -> None:
    root = repository(tmp_path, {"a.py": "x = 1  # TODO one\ny = 2  # FIXME two\n"})
    assert run(markers, root) == 0
    assert "no base commit yet; 2 marker(s)" in capsys.readouterr().out
    commit(root, "feat(core): second")
    branch(root)
    write(root, "a.py", "x = 1  # TODO one\n")
    write(root, "b.py", "z = 3  # HACK untracked counts too\n")
    assert run(markers, root) == 1
    assert "HACK: rose from 0 to 1" in capsys.readouterr().out
    (root / "b.py").unlink()
    assert run(markers, root) == 0
    assert markers.counts(root, None) == {"TODO": 1, "FIXME": 0, "HACK": 0, "XXX": 0}
    assert markers.grep_count(root, "TODOS", None) == 0, "whole words only"


def test_a_marker_inside_a_longer_word_is_not_counted(tmp_path: Path) -> None:
    root = repository(tmp_path, {"a.py": "x = 1  # TODOS and FIXMEs and HACKED and XXXL\n"})
    assert markers.counts(root, None) == {"TODO": 0, "FIXME": 0, "HACK": 0, "XXX": 0}


def test_markers_staged_judges_only_index_and_handles_deletes_and_chunks(
    tmp_path: Path, capsys: Out
) -> None:
    root = repository(tmp_path, {"a.py": "x = 1  # TODO one\n"})
    write(root, "b.py", "y = 2  # TODO two\n")
    git(root, "add", "b.py")
    write(root, "c.py", "z = 3  # TODO unstaged\n")
    assert run(markers, root, "--staged") == 0
    assert "no base commit yet; 2 marker(s) accepted as is" in capsys.readouterr().out
    (root / "c.py").unlink()
    commit(root, "feat(core): second")
    branch(root)
    assert markers.grep_count_paths(root, "TODO", []) == 0
    assert run(markers, root, "--staged") == 0
    assert "markers: green against" in capsys.readouterr().out
    write(root, "a.py", "x = 1  # TODO one\n# FIXME unstaged\n")
    write(root, "u.py", "# HACK untracked\n")
    assert run(markers, root, "--staged") == 0
    capsys.readouterr()
    git(root, "add", "a.py")
    assert run(markers, root, "--staged") == 1
    assert "FIXME: rose from 0 to 1" in capsys.readouterr().out
    git(root, "rm", "-q", "b.py")
    write(root, "a.py", "x = 1  # TODO one\n# TODO two\n")
    git(root, "add", "a.py")
    assert run(markers, root, "--staged") == 0
    capsys.readouterr()
    (root / "u.py").unlink()
    for i in range(205):
        write(root, f"m{i:03d}.py", "# HACK chunk\n" if i == 204 else "x = 1\n")
    git(root, "add", "-A")
    assert run(markers, root, "--staged") == 1
    assert "HACK: rose from 0 to 1" in capsys.readouterr().out


PINNED = "actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09 # v5"
IMAGE_PINS = (
    "# Binding\n\n## Image pins\n\nWhy.\n\n| Files | Reference |\n| --- | --- |\n"
    "| `**/Buildfile*, **/*.Buildfile` | `^\\s*FROM\\s+(?:--platform=\\S+\\s+)?(?!scratch\\b)(\\S+)"
    "(?:\\s+AS\\s+(\\S+))?` |\n| `compose*.yml, docker-compose*.yaml` "
    "| `^\\s*image:\\s*(\\S+)` |\n"
)


def test_pins_check_actions_images_and_permissions(tmp_path: Path) -> None:
    root = repository(
        tmp_path,
        {
            ".github/workflows/ci.yml": (
                "name: ci\npermissions:\n  contents: read\njobs:\n  a:\n    steps:\n"
                f"      - uses: {PINNED}\n      - uses: ./local\n"
                "      - uses: docker://alpine@sha256:0000\n"
                "    container:\n      image: alpine@sha256:0000\n"
            ),
            "docs/process/binding.md": IMAGE_PINS,
            "Buildfile": (
                "FROM python:3.13@sha256:abc AS build\nRUN make\nFROM build\nFROM scratch\n"
                "FROM $BASE\n"
            ),
            "compose.yml": "services:\n  db:\n    image: postgres@sha256:abc\n",
            "notes.md": "uses: actions/checkout@v4 is prose, not a workflow\n",
        },
    )
    assert pins.problems(root) == []
    write(
        root,
        ".github/workflows/deep.yml",
        "name: deep\njobs:\n  a:\n    steps:\n      - uses: actions/checkout@v4\n"
        "      - uses: actions/cache@0057852bfaa89a56745cba8c7296529d2fc39830\n"
        "      - uses: docker://alpine:3\n    container:\n      image: alpine:3\n",
    )
    write(root, "app.Buildfile", "FROM python:3.13\n")
    write(root, "docker-compose.yaml", "services:\n  db:\n    image: postgres:16\n")
    assert pins.problems(root) == [
        ".github/workflows/deep.yml: no top-level permissions block",
        ".github/workflows/deep.yml:5: action actions/checkout@v4 is not pinned to a commit",
        ".github/workflows/deep.yml:6: pinned action lacks a version comment",
        ".github/workflows/deep.yml:7: image docker://alpine:3 is not pinned by digest",
        ".github/workflows/deep.yml:9: image alpine:3 is not pinned by digest",
        "app.Buildfile:1: image python:3.13 is not pinned by digest",
        "docker-compose.yaml:3: image postgres:16 is not pinned by digest",
    ]
    assert run(pins, root) == 1
    (root / "bin.yml").write_bytes(b"\x00")
    write(root, ".github/workflows/bin.yml", "\x00")
    assert len(pins.problems(root)) == 7


def test_pins_read_the_image_files_the_binding_names_and_say_when_it_names_none(
    tmp_path: Path, capsys: Out
) -> None:
    files = {"Buildfile": "FROM python:3.13\n", "docs/process/binding.md": "# Binding\n"}
    root = repository(tmp_path, files)
    assert pins.problems(root) == []
    assert "no image file is read" in capsys.readouterr().out
    none = IMAGE_PINS.split("| `**", maxsplit=1)[0] + "| none: the project names no image | |\n"
    write(root, "docs/process/binding.md", none)
    assert pins.problems(root) == []
    assert "no image file is read" not in capsys.readouterr().out
    write(root, "docs/process/binding.md", IMAGE_PINS)
    assert pins.problems(root) == ["Buildfile:1: image python:3.13 is not pinned by digest"]


def test_the_merge_bases_image_pins_judge_not_the_changes_own(tmp_path: Path) -> None:
    files = {"Buildfile": "FROM python:3.13\n", "docs/process/binding.md": IMAGE_PINS}
    root = repository(tmp_path, files)
    branch(root, "feature/x")
    none = IMAGE_PINS.split("| `**", maxsplit=1)[0] + "| none: narrowed by the change | |\n"
    write(root, "docs/process/binding.md", none)
    commit(root, "feat(core): narrow its own judge")
    assert pins.problems(root) == ["Buildfile:1: image python:3.13 is not pinned by digest"]


def test_image_pin_rows_are_read_and_their_faults_named() -> None:
    rules, problems = pins.parse(IMAGE_PINS)
    assert problems == []
    assert rules is not None
    assert [rule.files for rule in rules] == [
        ("**/Buildfile*", "**/*.Buildfile"),
        ("compose*.yml", "docker-compose*.yaml"),
    ]
    assert pins.parse(None)[1] == ["has no '## Image pins' section"]
    head = "## Image pins\n\n| Files | Reference |\n| --- | --- |\n"
    assert pins.parse("# B\n\n" + head + "| <globs> | <regex> |\n")[1] == [
        "the image pins table still has the template's placeholder row"
    ]
    assert pins.parse("# B\n\n" + head + "| none: x | |\n")[1] == [
        "an image pins row says none with no reason: say why"
    ]
    assert "no regex" in pins.parse("# B\n\n" + head + "| `a` | `(` |\n")[1][0]
    assert "no group 1" in pins.parse("# B\n\n" + head + "| `a` | `x` |\n")[1][0]
    assert "needs backticked globs" in pins.parse("# B\n\n" + head + "| a | `(x)` |\n")[1][0]
    assert "lacks its table" in pins.parse("# B\n\n## Image pins\n\nprose\n")[1][0]


GLOSSARY = (
    "# Glossary\n\n## Language\n\n**Customer**:\nWho buys.\n_Avoid_: client, `buyer`, <synonym>\n\n"
    "**Order**:\nWhat they buy.\n_Avoid_: purchase order\n"
)


def test_prose_finds_avoided_words_in_documents_and_comments(tmp_path: Path) -> None:
    root = repository(
        tmp_path,
        {
            "GLOSSARY.md": GLOSSARY,
            "docs/a.md": "The client and the Buyer.\nA purchase order too.\nclients are fine.\n",
            "src/a.py": "client = 1  # the client wins\nx = 2  # glossary: allow, the client API\n",
            "src/b.ts": "// Buyer here\nconst buyer = 1\n",
            "src/c.bin": "client\n",
        },
    )
    assert prose.avoided(GLOSSARY) == {
        "client": "Customer",
        "buyer": "Customer",
        "purchase order": "Order",
    }
    assert prose.problems(root) == [
        "docs/a.md:1: 'client' is on the Avoid list of 'Customer'",
        "docs/a.md:1: 'Buyer' is on the Avoid list of 'Customer'",
        "docs/a.md:2: 'purchase order' is on the Avoid list of 'Order'",
        "src/a.py:1: 'client' is on the Avoid list of 'Customer'",
        "src/b.ts:1: 'Buyer' is on the Avoid list of 'Customer'",
    ]
    assert run(prose, root) == 1


def test_prose_without_a_glossary_or_without_avoid_lists(tmp_path: Path) -> None:
    root = repository(tmp_path, {"docs/a.md": "client\n"})
    assert prose.problems(root) == ["GLOSSARY.md: missing"]
    write(root, "GLOSSARY.md", "# Glossary\n\n**Customer**:\nWho buys.\n")
    assert prose.problems(root) == []
    (root / "src").mkdir()
    (root / "src/a.py").write_bytes(b"\x00 client")
    write(root, "GLOSSARY.md", GLOSSARY)
    assert prose.problems(root) == ["docs/a.md:1: 'client' is on the Avoid list of 'Customer'"]
    assert run(prose, root) == 1


def test_prose_judges_the_change_not_the_history(tmp_path: Path, capsys: Out) -> None:
    root = repository(tmp_path, {"GLOSSARY.md": GLOSSARY, "DESIGN.md": "A broken image.\n"})
    commit(root, "docs(design): record")
    branch(root)
    avoid_broken = GLOSSARY + "\n**Unreadable**:\nA cover that fails.\n_Avoid_: broken\n"
    write(root, "GLOSSARY.md", avoid_broken)
    assert run(prose, root) == 0
    assert "'broken'" not in capsys.readouterr().out
    write(root, "DESIGN.md", "A broken image.\nA broken cover.\n")
    assert run(prose, root) == 1
    assert "DESIGN.md:2: 'broken' is on the Avoid list of 'Unreadable'" in capsys.readouterr().out
    write(root, "DESIGN.md", "A broken image.\n")
    write(root, "docs/new.md", "Still broken.\n")
    assert run(prose, root) == 1, "a new file has no earlier uses to keep"


NOTE = (
    "A note about the ordering system.\nThe client.\nMore text so a rename keeps its similarity.\n"
)


def test_prose_follows_a_rename_to_the_earlier_uses(tmp_path: Path, capsys: Out) -> None:
    root = repository(tmp_path, {"GLOSSARY.md": GLOSSARY, "docs/old.md": NOTE})
    branch(root)
    git(root, "mv", "docs/old.md", "docs/new.md")
    assert run(prose, root) == 0
    assert "client" not in capsys.readouterr().out
    commit(root, "docs(core): move the note")
    assert run(prose, root) == 0
    write(root, "docs/new.md", NOTE + "Another client.\n")
    assert run(prose, root) == 1
    assert "docs/new.md:4: 'client' is on the Avoid list" in capsys.readouterr().out


def test_prose_counts_uses_in_a_path_holding_a_quote(tmp_path: Path) -> None:
    root = repository(tmp_path, {"GLOSSARY.md": GLOSSARY, "docs/it's.md": "The client.\n"})
    branch(root)
    write(root, "docs/it's.md", "Intro.\nThe client.\n")
    assert run(prose, root) == 0, "the earlier use only moved down a line"
    write(root, "docs/it's.md", "Intro.\nThe client.\nThe Client again.\n")
    assert prose.problems(root, "HEAD") == [
        "docs/it's.md:2: 'client' is on the Avoid list of 'Customer'",
        "docs/it's.md:3: 'Client' is on the Avoid list of 'Customer'",
    ]


def test_prose_ignores_inline_code_spans_and_fenced_blocks(tmp_path: Path) -> None:
    glossary = GLOSSARY + "\n**Delivered**:\nStored output.\n_Avoid_: cache\n"
    doc = (
        "- Build: gate `build`; caches `.next/cache`; warm 14s\n"
        "A ``nested `cache` path`` beside a client.\n"
        "```cache``` at line start beside buyer.\n"
        "An unmatched ` backtick still flags client.\n"
        "```bash\n"
        "rm -rf .next/cache\n"
        "```not-a-close\n"
        "```\n"
        "~~~yaml\n"
        "key: cache\n"
        "```\n"
        "~~~\n"
        "Prose cache outside `.next/cache` still fails.\n"
    )
    root = repository(
        tmp_path,
        {
            "GLOSSARY.md": glossary,
            "docs/binding.md": doc,
            "src/a.py": "x = 1  # reads `.next/cache` for client\n",
        },
    )
    assert prose.problems(root) == [
        "docs/binding.md:2: 'client' is on the Avoid list of 'Customer'",
        "docs/binding.md:3: 'buyer' is on the Avoid list of 'Customer'",
        "docs/binding.md:4: 'client' is on the Avoid list of 'Customer'",
        "docs/binding.md:13: 'cache' is on the Avoid list of 'Delivered'",
        "src/a.py:1: 'client' is on the Avoid list of 'Customer'",
    ]


MANIFEST = (
    '[[group]]\nname = "tool-config"\nreason = "Rule sets."\n'
    'paths = ["lint.toml", "cfg/*.json", "~/.tool.toml"]\n'
)


def test_snapshots_write_then_check_and_report_every_drift(tmp_path: Path, capsys: Out) -> None:
    root = repository(
        tmp_path,
        {"guards/manifest.toml": MANIFEST, "lint.toml": "a = 1\n", "cfg/x.json": "{}\n"},
    )
    assert run(snapshots, root, "--write") == 0
    assert sorted(p.name for p in (root / "quality/snapshots").iterdir()) == [
        "cfg__x.json",
        "lint.toml",
    ]
    assert run(snapshots, root, "--check") == 0
    write(root, "lint.toml", "a = 2\n")
    write(root, "cfg/y.json", "[]\n")
    (root / "quality/snapshots/stale.toml").write_text("old\n")
    capsys.readouterr()
    assert run(snapshots, root, "--check") == 1
    differs = "differs from its config; run `just snapshots` and review the diff"
    assert capsys.readouterr().out.splitlines()[:3] == [
        "quality/snapshots/cfg__y.json: missing; run `just snapshots`",
        f"quality/snapshots/lint.toml: {differs}",
        "quality/snapshots/stale.toml: stale; its config is gone",
    ]
    assert run(snapshots, root, "--write") == 0
    assert not (root / "quality/snapshots/stale.toml").exists()
    assert run(snapshots, root, "--check") == 0


def test_snapshots_need_the_group(tmp_path: Path) -> None:
    root = repository(tmp_path)
    assert snapshots.write(root, "tool-config") == [
        "guards/manifest.toml: no group named 'tool-config'"
    ]
    write(root, "guards/manifest.toml", '[[group]]\nname = "other"\nreason = "r"\npaths = ["x"]\n')
    assert snapshots.check(root, "tool-config") == [
        "guards/manifest.toml: no group named 'tool-config'"
    ]
    write(root, "guards/manifest.toml", "group = 3\n")
    assert snapshots.patterns(root, "tool-config") is None
    write(
        root, "guards/manifest.toml", '[[group]]\nname = "tool-config"\nreason = "r"\npaths = 1\n'
    )
    assert snapshots.patterns(root, "tool-config") == []


def test_commit_subjects_follow_the_convention(tmp_path: Path, capsys: Out) -> None:
    assert (
        commits.problems(["feat(core): add", "fix!: break", "chore(method): adopt the method"])
        == []
    )
    long = "feat: " + "x" * 70
    assert commits.problems(["Fixed stuff.", long]) == [
        "'Fixed stuff.': not `type(scope): summary` with a type in " + ", ".join(commits.TYPES),
        "'Fixed stuff.': ends with a full stop",
        f"'{long[:30]}...': 76 characters, at most 72",
    ]
    root = repository(tmp_path)
    assert run(commits, root) == 0, "the single clean commit"
    branch(root)
    commit(root, "wip")
    assert run(commits, root) == 1
    assert "'wip': not" in capsys.readouterr().out
    (tmp_path / "bad").mkdir()
    bad = repository(tmp_path / "bad")
    git(bad, "commit", "-q", "--amend", "-m", "nope")
    assert run(commits, bad) == 1


def message(root: Path, text: str) -> str:
    path = root / ".git/COMMIT_EDITMSG"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_the_message_being_written_is_checked_alone(tmp_path: Path, capsys: Out) -> None:
    root = repository(tmp_path)
    branch(root)
    commit(root, "a subject the history check would refuse")
    ok = "feat: " + "x" * 66
    assert run(commits, root, "--message-file", message(root, ok + "\n\nbody\n")) == 0, (
        "72 characters pass, and the bad commit an amend would replace is not judged"
    )
    too_long = ok + "x"
    assert run(commits, root, "--message-file", message(root, too_long + "\n")) == 1
    assert "73 characters, at most 72" in capsys.readouterr().out


def test_the_subject_is_the_first_line_git_keeps(tmp_path: Path, capsys: Out) -> None:
    root = repository(tmp_path)
    text = "\n# Please enter the commit message\n\nwip\n# On branch main\n"
    assert run(commits, root, "--message-file", message(root, text)) == 1
    assert "'wip': not" in capsys.readouterr().out
    assert run(commits, root, "--message-file", message(root, "# only comments\n\n")) == 0, (
        "git aborts an empty message itself"
    )


def test_a_merge_message_is_not_judged(tmp_path: Path) -> None:
    root = repository(tmp_path)
    branch(root)
    commit(root, "feat: on the side")
    git(root, "checkout", "-q", "main")
    commit(root, "feat: on main")
    git(root, "merge", "-q", "--no-commit", "change")
    assert run(commits, root, "--message-file", message(root, "Merge branch 'change'\n")) == 0


def test_the_number_a_squash_merge_appends_is_not_counted() -> None:
    written = "feat: " + "x" * 66
    assert len(written) == 72
    assert commits.problems([written + " (#7)", written + " (#1234)"]) == [], (
        "the forge writes the pull request number, so the author's 72 characters still pass"
    )
    assert commits.problems([written + "x (#7)"]) == [
        f"'{written[:30]}...': 73 characters, at most 72, not counting ' (#7)'"
    ]
    assert commits.problems([written[:-6] + " (#7) x", written + "(#7)", written + " (#x)"]) == [
        f"'{written[:30]}...': 73 characters, at most 72",
        f"'{written[:30]}...': 76 characters, at most 72",
        f"'{written[:30]}...': 77 characters, at most 72",
    ], "only a number in brackets after a space, at the very end, is the forge's"


def test_change_size_does_not_count_the_methods_own_records(tmp_path: Path) -> None:
    root = repository(tmp_path, {"a.txt": "1\n", ".scratch/f/issues/01-a.md": "Status: open\n"})
    commit(root, "feat(core): second")
    branch(root)
    write(root, "a.txt", "1\n2\n")
    write(root, "docs/features/f/spec.md", "one\ntwo\n")
    write(root, ".scratch/f/issues/01-a.md", "Status: resolved\n")
    commit(root, "feat(core): the change and its spec")
    write(root, "docs/features/f/verdict.json", "{}\n")
    write(root, ".scratch/changes/feature-1-a.md", "# a\n")
    write(root, "docs/features/f/helper.py", "x = 1\n")
    write(root, "docs/disputes/0001-slug.md", "# Dispute\n\nwhy\n")
    write(root, "docs/adr/0001-decision.md", "".join(f"line {n}\n" for n in range(50)))
    write(root, "docs/architecture/blueprint.md", "# Blueprint\n\ndetails\n")
    write(root, "README.md", "# Readme\n\nupdated\n")
    write(root, "notes.markdown", "# Notes\n")
    assert change_size.measure(root, "main") == change_size.Size(2, 2), "code, and no record"


def test_change_size_does_not_count_snapshots_or_mise_locks(tmp_path: Path, capsys: Out) -> None:
    root = repository(
        tmp_path,
        {
            "docs/process/binding.md": BINDING_WITH_ROW.format(row="`tests/**`"),
            "quality/thresholds.toml": CEILINGS,
            "quality/snapshots/pyproject.toml": "line = 1\n",
            ".mise/locks/aube-lock.yaml": "version: 1\n",
        },
    )
    commit(root, "feat(core): baseline")
    branch(root)
    write(root, "src/app.py", "x = 1\ny = 2\n")
    write(root, "quality/snapshots/pyproject.toml", "".join(f"k{n} = {n}\n" for n in range(200)))
    write(root, "quality/snapshots/nested/tool.json", "".join(f"{n}\n" for n in range(150)))
    write(root, ".mise/locks/aube-lock.yaml", "".join(f"pkg_{n}: 1.0.{n}\n" for n in range(744)))
    commit(root, "feat(core): update product with snapshots and mise locks")
    write(
        root,
        ".mise/locks/extra/tool-lock.yaml",
        "".join(f"untracked_{n}: 1\n" for n in range(50)),
    )
    assert change_size.measure(root, "main") == change_size.Size(1, 2)
    assert run(change_size, root) == 0
    assert "1 product files (ceiling 2), 2 product lines (ceiling 5)" in capsys.readouterr().out


def owed_entries(count: int) -> str:
    return "".join(
        f'[[owed]]\nrule = "comments"\npath = "src/m{n}.py"\nproblem = "c{n}"\nsince = "1.0.0"\n\n'
        for n in range(count)
    )


def test_change_size_does_not_count_the_ledger_of_owed_findings(
    tmp_path: Path, capsys: Out
) -> None:
    root = repository(
        tmp_path,
        {
            "docs/process/binding.md": BINDING_WITH_ROW.format(row="`tests/**`"),
            "quality/thresholds.toml": CEILINGS,
            "quality/owed.toml": owed_entries(3),
        },
    )
    commit(root, "feat(core): second")
    branch(root)
    write(root, "src/a.py", "a = 1\n")
    write(root, "quality/owed.toml", owed_entries(400)[len(owed_entries(1)) :])
    commit(root, "chore(method): bind the comments lister")
    assert change_size.measure(root, "main") == change_size.Size(1, 1), "the code, no ledger line"
    assert run(change_size, root) == 0
    assert "1 product files (ceiling 2), 1 product lines (ceiling 5)" in capsys.readouterr().out


def test_change_size_does_not_count_the_lockfiles_the_merge_bases_binding_names(
    tmp_path: Path,
) -> None:
    binding = stack.BINDING.replace("- Lockfiles: `*.lock`", "- Lockfiles: `*.lock`, `*/*.lock`")
    root = repository(tmp_path, {"a.txt": "1\n", "docs/process/binding.md": binding})
    commit(root, "feat(core): second")
    branch(root)
    for name in ("app.lock", "quality/snapshots/app.lock", "web/app.lock"):
        write(root, name, "a\nb\n")
    write(root, "quality/snapshots/web__app.lock", "{}\n")
    write(root, "other.pins", "a\n")
    assert change_size.measure(root, "main") == change_size.Size(1, 1), (
        "other.pins is a file the binding omits"
    )


def test_change_size_counts_every_file_when_the_merge_base_names_no_lockfile(
    tmp_path: Path,
) -> None:
    root = repository(tmp_path, {"a.txt": "1\n"})
    commit(root, "feat(core): second")
    branch(root)
    write(root, "app.lock", "a\nb\n")
    assert change_size.measure(root, "main") == change_size.Size(1, 2)


def test_change_size_counts_a_moved_file_by_its_changed_lines(tmp_path: Path) -> None:
    body = "".join(f"line {n}\n" for n in range(100))
    root = repository(tmp_path, {"old/a.py": body, "docs/features/f/spec.md": "x\n"})
    commit(root, "feat(core): second")
    branch(root)
    (root / "new").mkdir()
    (root / "old/a.py").rename(root / "new/a.py")
    write(root, "new/a.py", body.replace("line 1\n", "changed\n"))
    (root / "docs/features/f/spec.md").rename(root / "docs/features/f/plan.md")
    (root / "docs/features/f/plan.md").rename(root / "plan.txt")
    commit(root, "refactor(core): move a")
    assert change_size.measure(root, "main") == change_size.Size(2, 1), "the edit, no record"


def test_change_size_measures_against_the_ceiling(tmp_path: Path, capsys: Out) -> None:
    root = repository(tmp_path, {"a.txt": "1\n2\n", "bin": "x"})
    assert run(change_size, root) == 0
    assert "no base commit yet" in capsys.readouterr().out
    commit(root, "feat(core): second")
    branch(root)
    write(root, "a.txt", "1\n3\n4\n")
    write(root, "new.txt", "n\n")
    (root / "bin").write_bytes(b"\x00\x01")
    (root / "blob").write_bytes(b"\x00\x01")
    assert change_size.measure(root, "main") == change_size.Size(4, 3), "binaries are files"
    assert run(change_size, root) == 0
    assert "no ceiling recorded" in capsys.readouterr().out
    assert (
        run(
            change_size,
            root,
            "--set",
            "--product-files",
            "2",
            "--product-lines",
            "10",
            "--test-lines",
            "50",
        )
        == 0
    )
    assert (
        run(
            change_size,
            root,
            "--set",
            "--product-files",
            "3",
            "--product-lines",
            "10",
            "--test-lines",
            "50",
        )
        == 0
    )
    assert change_size.ceilings(root) == change_size.Ceilings(3, 10, 50)
    assert run(change_size, root) == 1
    assert "product files: 5 changed, ceiling 3" in capsys.readouterr().out, "the new file counts"
    commit(root, "chore(method): adopt the ratchet method")
    assert run(change_size, root) == 0
    assert "exempt" in capsys.readouterr().out
    commit(root, "feat(core): more")
    assert run(change_size, root) == 1
    with pytest.raises(SystemExit):
        run(change_size, root, "--set")


def test_change_size_exempts_safeguard_only_when_it_binds_a_later_row(
    tmp_path: Path, capsys: Out
) -> None:
    binding = (
        "# Binding\n\n## Gates\n\n"
        "| Question | Tool | Tier | Threshold source | Entry point |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| load smoke | later: code:entry-point | 1 | | `just load-smoke` |\n"
    )
    root = repository(
        tmp_path,
        {
            "docs/process/binding.md": binding,
            "quality/thresholds.toml": "[ceilings.change]\nproduct_files = 1\nproduct_lines = 5\n",
        },
    )
    branch(root)
    write(root, "a.txt", "1\n2\n3\n4\n5\n6\n")
    commit(root, "chore(method): safeguard spoofed without binding")
    assert run(change_size, root) == 1, "spoofing the commit subject without binding stays red"

    write(
        root,
        "docs/process/binding.md",
        binding.replace("later: code:entry-point", "none: skipped"),
    )
    commit(root, "chore(method): safeguard none is not bound")
    assert run(change_size, root) == 1, "moving later: to none: is not a safeguard binding"

    write(
        root,
        "docs/process/binding.md",
        binding.replace("later: code:entry-point", "a real tool"),
    )
    commit(root, "chore(method): safeguard entry-point")
    assert run(change_size, root) == 0
    assert "the safeguard binding is exempt" in capsys.readouterr().out

    commit(root, "feat(core): mixed commit")
    assert run(change_size, root) == 1, "mixing a feature commit revokes the safeguard exemption"


def test_change_size_ceilings_are_rewritten_in_place() -> None:
    before = "[ceilings.change]\nfiles = 1\nlines = 2\n"
    after = "[ceilings.change]\nproduct_files = 3\nproduct_lines = 4\ntest_lines = 5\n"
    text = f"[python.coverage.core]\nline = 90.0\n\n{before}\n[x]\ny = 1\n"
    assert change_size.with_ceilings(text, (3, 4, 5)) == (
        f"[python.coverage.core]\nline = 90.0\n\n{after}\n[x]\ny = 1\n"
    ), "the older keys go with the table"
    assert change_size.with_ceilings("", (3, 4, 5)) == after
    assert change_size.with_ceilings("[x]\ny = 1\n", (3, 4, 5)) == f"[x]\ny = 1\n\n{after}"
    size, limits = change_size.Size(2, 2, 0, 9), change_size.Ceilings(None, 1, 8)
    assert change_size.over(size, limits) == [
        "product lines: 2 added, ceiling 1",
        "test lines: 9 added, ceiling 8",
    ]


def test_change_size_ignores_non_integer_ceilings(tmp_path: Path) -> None:
    write(tmp_path, "quality/thresholds.toml", "[ceilings.change]\nfiles = true\nlines = 2.5\n")
    assert change_size.ceilings(tmp_path) == change_size.Ceilings()
    assert not change_size.ceilings(tmp_path).recorded()


@pytest.mark.parametrize(
    "case",
    [
        ("product_files = 9.5", "ceilings.change.product_files"),
        ("test_lines = true", "ceilings.change.test_lines"),
        ("test_lines = -1", "ceilings.change.test_lines"),
        ("files = 9.5", "ceilings.change.files"),
        ("lines = 2.5", "ceilings.change.lines"),
        ("files = true", "ceilings.change.files"),
        ("lines = false", "ceilings.change.lines"),
        ("files = -1", "ceilings.change.files"),
        ('lines = "9"', "ceilings.change.lines"),
        ("files = 3\nlines = nan", "ceilings.change.lines"),
    ],
)
def test_change_size_is_red_on_a_ceiling_that_is_not_a_non_negative_integer(
    tmp_path: Path, capsys: Out, case: tuple[str, str]
) -> None:
    body, named = case
    root = repository(tmp_path, {"quality/thresholds.toml": f"[ceilings.change]\n{body}\n"})
    commit(root, "feat(core): second")
    branch(root)
    write(root, "a.txt", "1\n")
    assert run(change_size, root) == 1
    out = capsys.readouterr().out
    assert f"{named}: " in out
    assert "not a non-negative integer" in out
    assert "no ceiling recorded" not in out


def test_change_size_without_a_change_ceiling_key_records_none(tmp_path: Path, capsys: Out) -> None:
    root = repository(tmp_path, {"quality/thresholds.toml": "[ceilings.change]\nnote = 1\n"})
    commit(root, "feat(core): second")
    branch(root)
    write(root, "a.txt", "1\n")
    assert run(change_size, root) == 0
    assert "no ceiling recorded" in capsys.readouterr().out
    assert change_size.malformed(root) == []


def test_change_size_on_the_default_branch_reports_a_merge_it_cannot_reopen(
    tmp_path: Path, capsys: Out
) -> None:
    root = repository(
        tmp_path, {"quality/thresholds.toml": "[ceilings.change]\nfiles = 1\nlines = 1\n"}
    )
    branch(root, "feature/1-big")
    write(root, "a.py", "a = 1\nb = 2\nc = 3\n")
    commit(root, "feat(core): big")
    git(root, "checkout", "-q", "main")
    git(root, "merge", "-q", "--no-ff", "-m", "Merge feature/1-big", "feature/1-big")
    assert run(change_size, root) == 0
    assert "already judged" in capsys.readouterr().out
    write(root, "b.py", "x = 1\ny = 2\n")
    assert run(change_size, root) == 1, "uncommitted work on the default branch is still judged"
    commit(root, "feat(core): direct")
    assert run(change_size, root) == 1, "a direct commit on the default branch is still judged"


BINDING_WITH_ROW = "# Binding\n\n## Test paths\n\nWhy.\n\n- Tests: {row}\n\n## Limits\n"
CEILINGS = "[ceilings.change]\nproduct_files = 2\nproduct_lines = 5\ntest_lines = 20\n"


def sized(tmp_path: Path, row: str | None) -> Path:
    binding = BINDING_WITH_ROW.format(row=row) if row is not None else "# Binding\n\n## Limits\n"
    root = repository(
        tmp_path, {"docs/process/binding.md": binding, "quality/thresholds.toml": CEILINGS}
    )
    commit(root, "feat(core): second")
    branch(root)
    write(root, "src/a.py", "a = 1\nb = 2\nc = 3\n")
    write(root, "tests/test_a.py", "".join(f"t{n} = 1\n" for n in range(8)))
    write(root, "src/a.test.ts", "x\ny\nz\nw\n")
    return root


def test_change_size_counts_tests_apart_when_the_binding_names_them(tmp_path: Path) -> None:
    root = sized(tmp_path, "`tests/**`, `**/*.test.ts`")
    assert change_size.test_globs(root, "main") == ["tests/**", "**/*.test.ts"]
    assert change_size.measure(root, "main") == change_size.Size(1, 3, 2, 12)
    assert run(change_size, root) == 0


def test_a_change_cannot_widen_what_it_exempts_by_writing_the_test_paths_row_itself(
    tmp_path: Path,
) -> None:
    root = sized(tmp_path, None)
    write(
        root,
        "docs/process/binding.md",
        BINDING_WITH_ROW.format(row="`tests/**`, `**/*.test.ts`"),
    )
    assert change_size.test_globs(root, "main") is None


def test_change_size_is_red_on_product_lines_and_on_a_runaway_of_tests_alone(
    tmp_path: Path, capsys: Out
) -> None:
    root = sized(tmp_path, "`tests/**`, `**/*.test.ts`")
    write(root, "src/b.py", "".join(f"p{n} = 1\n" for n in range(3)))
    assert run(change_size, root) == 1
    assert "product lines: 6 added, ceiling 5" in capsys.readouterr().out
    (root / "src/b.py").unlink()
    write(root, "tests/test_b.py", "".join(f"u{n} = 1\n" for n in range(9)))
    assert run(change_size, root) == 1
    assert "test lines: 21 added, ceiling 20" in capsys.readouterr().out


@pytest.mark.parametrize("row", [None, "<globs of the tests>", "", "tests/**", "none: no"])
def test_change_size_counts_everything_as_product_without_a_filled_row(
    tmp_path: Path, capsys: Out, row: str | None
) -> None:
    root = sized(tmp_path, row)
    assert change_size.test_globs(root, "main") is None
    assert change_size.measure(root, "main") == change_size.Size(3, 15)
    assert run(change_size, root) == 1
    out = capsys.readouterr().out
    assert "no filled `## Test paths` section" in out
    assert "product files: 3 changed, ceiling 2" in out


def test_change_size_reads_none_as_no_test_paths_and_says_nothing_is_missing(
    tmp_path: Path, capsys: Out
) -> None:
    root = sized(tmp_path, "none: the repository has no tests yet")
    assert change_size.test_globs(root, "main") == []
    run(change_size, root)
    assert "no filled `## Test paths` section" not in capsys.readouterr().out


def test_change_size_reads_the_older_keys_as_the_product_ceilings(tmp_path: Path) -> None:
    write(tmp_path, "quality/thresholds.toml", "[ceilings.change]\nfiles = 7\nlines = 9\n")
    assert change_size.ceilings(tmp_path) == change_size.Ceilings(7, 9, None)
    write(tmp_path, "quality/thresholds.toml", "[ceilings.change]\nfiles = 7\nproduct_files = 4\n")
    assert change_size.ceilings(tmp_path).product_files == 4, "the new key wins"


@pytest.mark.parametrize(
    "binding",
    [
        "# B\n\n## Test paths\n\nProse only.\n\n## Next\n\n- Tests: `t/**`\n",
        "# B\n\n## Test paths\n\nProse only.\n",
    ],
    ids=["a later section names tests", "the section runs to the end"],
)
def test_a_test_paths_section_without_its_bullet_is_unfilled(binding: str) -> None:
    globs, problems = change_size.test_paths(binding)
    assert globs is None
    assert any("still the template's" in problem for problem in problems)


def test_change_size_counts_added_lines_only_and_excludes_generated_and_recorded_outputs(
    tmp_path: Path, capsys: Out
) -> None:
    binding = (
        "# Binding\n\n## Test paths\n\n"
        "- Tests: `tests/**`\n"
        "- Generated: `contracts/**/*.json`\n\n"
        "## Recorded outputs\n\n"
        "| Paths | Pattern |\n"
        "| --- | --- |\n"
        "| `snapshots/**` | |\n"
        "| `src/inline.py` | `expect\\(` |\n"
    )
    root = repository(
        tmp_path,
        {
            "docs/process/binding.md": binding,
            "quality/thresholds.toml": CEILINGS,
            "src/old.py": "".join(f"old_{n} = {n}\n" for n in range(40)),
            "contracts/api.json": "{}\n",
            "snapshots/out.txt": "before\n",
        },
    )
    commit(root, "feat(core): baseline")
    branch(root)
    write(root, "src/old.py", "kept = 1\nadded = 2\n")
    write(root, "contracts/api.json", "".join(f'"{n}": {n}\n' for n in range(500)))
    write(root, "snapshots/out.txt", "".join(f"row {n}\n" for n in range(300)))
    write(root, "src/inline.py", "expect(1)\n")
    commit(root, "refactor(core): shrink code and regenerate artefacts")
    assert change_size.measure(root, "main") == change_size.Size(2, 3)
    assert change_size.measure(root, "main", "HEAD") == change_size.Size(2, 3)
    assert run(change_size, root) == 0
    assert "2 product files (ceiling 2), 3 product lines (ceiling 5)" in capsys.readouterr().out


def test_change_size_generated_paths_validation_and_merge_base_immutability(tmp_path: Path) -> None:
    assert change_size.generated_paths(None) == ([], [])
    assert change_size.generated_paths("# B\n\n## Test paths\n\n- Tests: `t/**`\n") == ([], [])
    assert change_size.generated_paths(
        "# B\n\n## Test paths\n\n- Tests: `t/**`\n- Generated: <globs>\n"
    ) == ([], [])
    assert change_size.generated_paths(
        "# B\n\n## Test paths\n\n- Tests: `t/**`\n- Generated: none: no generated product files\n"
    ) == ([], [])
    assert change_size.generated_paths(
        "# B\n\n## Test paths\n\n- Tests: `t/**`\n- Generated: `contracts/**`, `gen/*.json`\n"
    ) == (["contracts/**", "gen/*.json"], [])
    for bad in ("none: short", "contracts/**", "`*`", "`**`", "`/abs/**`", "`../up/**`"):
        globs, problems = change_size.generated_paths(
            f"# B\n\n## Test paths\n\n- Tests: `t/**`\n- Generated: {bad}\n"
        )
        assert globs == []
        assert problems
    assert change_size.recorded_output_globs(None) == []

    root = repository(
        tmp_path,
        {
            "docs/process/binding.md": "# B\n\n## Test paths\n\n- Tests: `tests/**`\n",
            "quality/thresholds.toml": CEILINGS,
        },
    )
    branch(root)
    write(
        root,
        "docs/process/binding.md",
        "# B\n\n## Test paths\n\n- Tests: `tests/**`\n- Generated: `src/**`\n",
    )
    write(root, "src/big.py", "".join(f"x{n} = {n}\n" for n in range(10)))
    assert change_size.measure(root, "main").product_lines >= 10, (
        "a branch cannot exempt its own files via - Generated:"
    )


def test_change_size_two_tier_warns_between_autonomous_and_hard_ceilings_and_blocks_past_hard(
    tmp_path: Path, capsys: Out
) -> None:
    thresholds = (
        "[ceilings.change]\n"
        "product_files = 1\n"
        "product_files_hard = 3\n"
        "product_lines = 4\n"
        "product_lines_hard = 10\n"
        "test_lines = 20\n"
    )
    root = repository(
        tmp_path,
        {
            "docs/process/binding.md": BINDING_WITH_ROW.format(row="`tests/**`"),
            "quality/thresholds.toml": thresholds,
        },
    )
    branch(root)
    write(root, "src/a.py", "".join(f"a{n} = {n}\n" for n in range(6)))
    write(root, "src/b.py", "b = 1\n")
    assert run(change_size, root) == 0
    out = capsys.readouterr().out
    assert "autonomous ceiling 1, hard ceiling 3" in out
    assert "autonomous ceiling 4, hard ceiling 10" in out
    assert "change-size: warning: product files: 2 changed, over autonomous ceiling 1" in out
    assert "change-size: warning: product lines: 7 added, over autonomous ceiling 4" in out
    assert "requires owner merge" in out

    write(root, "src/c.py", "".join(f"c{n} = {n}\n" for n in range(5)))
    write(root, "src/d.py", "d = 1\n")
    assert run(change_size, root) == 1
    out = capsys.readouterr().out
    assert "product files: 4 changed, hard ceiling 3" in out
    assert "product lines: 13 added, hard ceiling 10" in out


def test_change_size_rejects_hard_ceiling_below_autonomous_ceiling_and_supports_set_hard(
    tmp_path: Path, capsys: Out
) -> None:
    root = repository(
        tmp_path,
        {
            "quality/thresholds.toml": (
                "[ceilings.change]\nproduct_files = 5\nproduct_files_hard = 3\n"
                "product_lines = 10\nproduct_lines_hard = 5\n"
            ),
        },
    )
    branch(root)
    write(root, "a.py", "x = 1\n")
    assert run(change_size, root) == 1
    out = capsys.readouterr().out
    assert "ceilings.change.product_files_hard: 3 is below ceilings.change.product_files (5)" in out
    assert (
        "ceilings.change.product_lines_hard: 5 is below ceilings.change.product_lines (10)" in out
    )

    assert (
        run(
            change_size,
            root,
            "--set",
            "--product-files",
            "5",
            "--product-files-hard",
            "12",
            "--product-lines",
            "10",
            "--product-lines-hard",
            "25",
            "--test-lines",
            "50",
        )
        == 0
    )
    assert change_size.ceilings(root) == change_size.Ceilings(5, 10, 50, 12, 25)
    assert "hard 12" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        run(
            change_size,
            root,
            "--set",
            "--product-files",
            "5",
            "--product-files-hard",
            "2",
            "--product-lines",
            "10",
            "--test-lines",
            "50",
        )
    with pytest.raises(SystemExit):
        run(
            change_size,
            root,
            "--set",
            "--product-files",
            "5",
            "--product-lines",
            "10",
            "--product-lines-hard",
            "8",
            "--test-lines",
            "50",
        )


def test_prose_reads_the_comments_of_any_text_file_and_skips_a_binary_one(tmp_path: Path) -> None:
    files = {"GLOSSARY.md": GLOSSARY, "a.zzz": "x = 1  # the client pays\n", "p.zzz": "client\n"}
    root = repository(tmp_path, files)
    (root / "b.bin").write_bytes(b"\x00 # client")
    assert prose.problems(root) == ["a.zzz:1: 'client' is on the Avoid list of 'Customer'"]
