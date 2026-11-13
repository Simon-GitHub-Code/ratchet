"""``plugin_pin``: the plugin a tier runs from is the commit and version the binding pins."""

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

import plugin_pin
from tests import repos

MANIFEST = ".claude-plugin/plugin.json"
ELSEWHERE = "f" * 40


def plugin(root: Path, version: str = "1.2.3") -> tuple[Path, str]:
    """A plugin checkout at ``root`` and the commit it is at."""
    repos.repository(root, {MANIFEST: f'{{"version": "{version}"}}'})
    return root, repos.git(root, "rev-parse", "HEAD")


def adopted(root: Path, line: str) -> Path:
    repos.write(root, "docs/process/binding.md", f"# Binding\n\n## Runner\n\njust\n\n{line}\n")
    return root


def run(root: Path, used: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = plugin_pin.main(["plugin_pin.py", "--root", str(root), "--plugin", str(used)])
    return code, capsys.readouterr().out


def test_a_checkout_at_the_pinned_commit_and_version_is_green(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    used, commit = plugin(tmp_path / "plugin")
    root = adopted(tmp_path / "repo", f"- Ratchet plugin: 1.2.3 at {commit}")
    assert run(root, used, capsys) == (
        0,
        f"plugin-pin: 1.2.3 at {commit[:12]}, as docs/process/binding.md pins\nplugin-pin: green\n",
    )


def test_a_checkout_that_moved_on_within_a_version_is_red_and_says_both_ways_out(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    used, pinned = plugin(tmp_path / "plugin")
    root = adopted(tmp_path / "repo", f"- Ratchet plugin: 1.2.3 at {pinned}")
    moved = repos.commit(used, "feat(gate): a stricter gate")
    code, out = run(root, used, capsys)
    assert code == 1
    assert f"the plugin at {used} is 1.2.3 at {moved}" in out
    assert f"docs/process/binding.md pins 1.2.3 at {pinned}, and which is newer" in out
    assert "set RATCHET_PLUGIN to a checkout of the pinned commit" in out
    assert f"set the pin line to `- Ratchet plugin: 1.2.3 at {moved}` and run" in out
    assert f"`python3 {used / 'scripts/upgrade.py'} --apply`" in out
    assert "which moves every workflow `ref` and the permission rules" in out
    assert out.endswith("plugin-pin: red\n")


def test_a_plugin_newer_than_the_pin_is_green_and_says_so_on_every_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    used, commit = plugin(tmp_path / "plugin", "1.3.0")
    root = adopted(tmp_path / "repo", f"- Ratchet plugin: 1.2.3 at {ELSEWHERE}")
    code, out = run(root, used, capsys)
    assert code == 0
    assert out == (
        f"plugin-pin: the plugin at {used} is 1.3.0 at {commit}, newer than the 1.2.3 that"
        " docs/process/binding.md pins: this run judges by 1.3.0's gates and CI by 1.2.3's."
        f" Run `python3 {used / 'scripts/upgrade.py'} --apply`: it moves the pin in"
        f" docs/process/binding.md to `- Ratchet plugin: 1.3.0 at {commit}`, every workflow `ref`"
        " and the permission rules together, and lists what it leaves to an agent or to you"
        "\nplugin-pin: green\n"
    )
    pin = plugin_pin.Pin("1.2.3", ELSEWHERE)
    assert plugin_pin.relation(pin, used) is plugin_pin.Relation.NEWER


def test_a_plugin_older_than_the_pin_is_red_and_says_to_update_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    used, commit = plugin(tmp_path / "plugin", "1.2.3")
    root = adopted(tmp_path / "repo", f"- Ratchet plugin: 1.10.0 at {ELSEWHERE}")
    code, out = run(root, used, capsys)
    assert code == 1
    assert out.startswith(
        f"the plugin at {used} is 1.2.3 at {commit}, older than the 1.10.0 at"
        f" {ELSEWHERE} that docs/process/binding.md pins, so a local tier would run older gates"
        " than CI. Bring the plugin up to date, or point RATCHET_PLUGIN at a checkout of the"
        " pinned commit."
    )


def test_a_pin_whose_version_is_not_the_commits_is_red(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    used, commit = plugin(tmp_path / "plugin")
    root = adopted(tmp_path / "repo", f"- Ratchet plugin: 1.2.4 at {commit}")
    code, out = run(root, used, capsys)
    assert code == 1
    assert f"is 1.2.3 at {commit}, older than the 1.2.4 at {commit}" in out


def test_a_copy_with_no_history_is_judged_on_its_version_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    used = tmp_path / "cache/ratchet/1.2.3"
    repos.write(used, MANIFEST, '{"version": "1.2.3"}')
    root = adopted(tmp_path / "repo", f"- Ratchet plugin: 1.2.3 at {ELSEWHERE}")
    said = "this copy carries no commit, so only the version was compared"
    green = f"plugin-pin: 1.2.3, as docs/process/binding.md pins; {said}\nplugin-pin: green\n"
    assert run(root, used, capsys) == (0, green)
    adopted(root, f"- Ratchet plugin: 1.3.0 at {ELSEWHERE}")
    code, out = run(root, used, capsys)
    assert code == 1
    assert f"the plugin at {used} is 1.2.3, older than the 1.3.0 at" in out


@pytest.mark.parametrize("line", ["- Ratchet plugin: <X.Y.Z> at <ratchet-sha>", "", None])
def test_a_binding_that_pins_nothing_is_red_and_names_the_plugin_in_use(
    line: str | None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    used, commit = plugin(tmp_path / "plugin")
    root = tmp_path / "repo" if line is None else adopted(tmp_path / "repo", line)
    code, out = run(root, used, capsys)
    assert code == 1
    assert "docs/process/binding.md: no `- Ratchet plugin: <version> at <commit>` line" in out
    assert f"this run uses 1.2.3 at {commit}" in out


def test_the_commit_is_the_plugins_own_inside_a_git_hook(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Why: git exports GIT_DIR to a hook, and `git -C` obeys it over the directory it is given.
    used, commit = plugin(tmp_path / "plugin")
    root = repos.repository(tmp_path / "repo")
    adopted(root, f"- Ratchet plugin: 1.2.3 at {commit}")
    monkeypatch.setenv("GIT_DIR", str(root / ".git"))
    assert run(root, used, capsys)[0] == 0


@pytest.mark.parametrize("manifest", [None, "not json", "[]", '{"version": 3}'])
def test_a_plugin_whose_version_cannot_be_read_is_red(
    manifest: str | None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    used = tmp_path / "plugin"
    used.mkdir()
    if manifest is not None:
        repos.write(used, MANIFEST, manifest)
    root = adopted(tmp_path / "repo", f"- Ratchet plugin: 1.2.3 at {ELSEWHERE}")
    code, out = run(root, used, capsys)
    assert code == 1
    assert f"the plugin at {used} is an unknown version; " in out


def test_a_checkout_git_cannot_read_is_red_not_judged_on_its_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    used = tmp_path / "plugin"
    repos.write(used, MANIFEST, '{"version": "1.2.3"}')
    (used / ".git").mkdir()
    root = adopted(tmp_path / "repo", f"- Ratchet plugin: 1.2.3 at {ELSEWHERE}")
    code, out = run(root, used, capsys)
    assert code == 1
    assert "is 1.2.3 at a commit git cannot read; " in out


def test_the_plugin_judged_is_the_one_the_script_runs_from(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    version = plugin_pin.version_of(plugin_pin.PLUGIN)
    commit = plugin_pin.commit_of(plugin_pin.PLUGIN) or ELSEWHERE
    adopted(tmp_path, f"- Ratchet plugin: {version} at {commit}")
    monkeypatch.chdir(tmp_path)
    assert plugin_pin.main(["plugin_pin.py"]) == 0
    assert capsys.readouterr().out.endswith("plugin-pin: green\n")


HEX = "0123456789abcdef"
FIRST, SECOND = "a" * 40, "b" * 40
PIN_LINE = f"- Ratchet plugin: 1.2.3 at {FIRST}"
BLOCK_CHECKOUT = (
    "      - uses: actions/checkout@v4\n"
    "        with:\n"
    "          repository: Simon-GitHub-Code/ratchet\n"
    '          ref: "{ref}"\n'
    "          path: ratchet\n"
)
INLINE_CHECKOUT = (
    "      - uses: actions/checkout@v4\n"
    "        with: { repository: Simon-GitHub-Code/ratchet, ref: %s, path: ratchet } # pinned\n"
)
OTHER_CHECKOUT = (
    "      - uses: actions/checkout@v4\n        with: { repository: acme/app, ref: main }\n"
)
BINDING = "# Binding\n\n## Runner\n\n`just`.\n\n## Forge\n\n- Default branch: `main`\n"


def workflow(*checkouts: str) -> str:
    return "name: ci\njobs:\n  one:\n    steps:\n" + "".join(checkouts)


def test_refs_reads_the_value_of_each_plugin_checkout_in_either_style() -> None:
    text = workflow(
        BLOCK_CHECKOUT.format(ref=FIRST),
        INLINE_CHECKOUT % '"<ratchet-sha>"',
        INLINE_CHECKOUT % SECOND,
        OTHER_CHECKOUT,
    )
    assert plugin_pin.refs(text) == {FIRST, "<ratchet-sha>", SECOND}
    assert plugin_pin.refs(OTHER_CHECKOUT) == set()


def test_with_refs_sets_each_plugin_checkout_and_changes_nothing_else() -> None:
    text = workflow(
        BLOCK_CHECKOUT.format(ref=FIRST),
        INLINE_CHECKOUT % '"<ratchet-sha>"',
        OTHER_CHECKOUT,
    )
    moved = plugin_pin.with_refs(SECOND)(text)
    assert moved == text.replace(FIRST, SECOND).replace("<ratchet-sha>", SECOND)
    assert "ref: main" in moved
    assert plugin_pin.with_refs(SECOND)(None) == ""


def test_with_pin_replaces_the_line_where_it_stands() -> None:
    held = BINDING.replace("`just`.\n", "`just`.\n\n- Ratchet plugin: <X.Y.Z> at <ratchet-sha>\n")
    assert plugin_pin.with_pin(PIN_LINE)(held) == BINDING.replace(
        "`just`.\n", f"`just`.\n\n{PIN_LINE}\n"
    )


def test_with_pin_adds_the_line_after_the_runner_paragraph_as_the_template_does() -> None:
    assert plugin_pin.with_pin(PIN_LINE)(BINDING) == BINDING.replace(
        "`just`.\n", f"`just`.\n\n{PIN_LINE}\n"
    )
    bare = "## Runner\n## Forge\n"
    assert plugin_pin.with_pin(PIN_LINE)(bare) == f"## Runner\n\n{PIN_LINE}\n## Forge\n"
    assert plugin_pin.with_pin(PIN_LINE)("## Runner") == f"## Runner\n\n{PIN_LINE}"


def test_with_pin_adds_a_runner_section_to_a_binding_without_one() -> None:
    pin = plugin_pin.with_pin(PIN_LINE)
    assert pin(None) == f"## Runner\n\n{PIN_LINE}\n"
    assert pin("# Binding\n") == f"# Binding\n\n## Runner\n\n{PIN_LINE}\n"
    assert pin("# Binding") == f"# Binding\n\n## Runner\n\n{PIN_LINE}\n"


FRAGMENTS = [
    "## Runner",
    "## Forge",
    "",
    "just",
    "- Ratchet plugin: 1.0.0 at " + "c" * 40,
    "- Ratchet plugin: <X.Y.Z> at <ratchet-sha>",
    "  - Ratchet plugin: indented",
    "a line\r",
]
BINDINGS = st.builds(
    "\n".join, st.lists(st.one_of(st.sampled_from(FRAGMENTS), st.text(max_size=12)), max_size=10)
)
COMMITS = st.text(alphabet=HEX, min_size=40, max_size=40)
CHECKOUTS = st.lists(
    st.one_of(
        st.builds(
            lambda ref: BLOCK_CHECKOUT.format(ref=ref),
            st.one_of(COMMITS, st.just("<ratchet-sha>")),
        ),
        st.builds(lambda ref: INLINE_CHECKOUT % f'"{ref}"', COMMITS),
        st.just(OTHER_CHECKOUT),
        st.text(max_size=30),
    ),
    max_size=5,
)


def is_subsequence(wanted: list[str], held: list[str]) -> bool:
    remaining = iter(held)
    return all(line in remaining for line in wanted)


@given(BINDINGS)
def test_with_pin_is_idempotent_and_keeps_every_line_it_does_not_own(text: str) -> None:
    pin = plugin_pin.with_pin(PIN_LINE)
    once = pin(text)
    assert pin(once) == once
    assert PIN_LINE in once.split("\n")
    others = [line for line in text.split("\n") if not line.startswith("- Ratchet plugin:")]
    assert is_subsequence(others, once.split("\n"))


def masked(text: str) -> str:
    """``text`` with the value of every plugin-checkout ``ref:`` taken out."""
    return plugin_pin.PLUGIN_REF.sub(lambda m: m.group(0).replace(m.group(1), ""), text)


@given(CHECKOUTS, COMMITS)
def test_with_refs_is_idempotent_and_changes_only_the_refs(pieces: list[str], commit: str) -> None:
    text = workflow(*pieces)
    once = plugin_pin.with_refs(commit)(text)
    assert plugin_pin.with_refs(commit)(once) == once
    assert plugin_pin.refs(once) <= {commit}
    assert masked(once) == masked(text)


def pinned_repository(
    tmp_path: Path, *, pin: str | None = None, refs: tuple[str, ...] = ()
) -> Path:
    root = repos.repository(tmp_path / "repo", {"README.md": "# A\n"})
    if pin is not None:
        adopted(root, f"- Ratchet plugin: 1.2.3 at {pin}")
    for number, ref in enumerate(refs):
        text = workflow(BLOCK_CHECKOUT.format(ref=ref))
        repos.write(root, f".github/workflows/w{number}.yml", text)
    return root


def test_ci_commits_are_the_pin_and_each_full_workflow_ref(tmp_path: Path) -> None:
    root = pinned_repository(tmp_path, pin=FIRST, refs=(SECOND, "<ratchet-sha>", "main"))
    assert plugin_pin.ci_commits(root) == {FIRST, SECOND}
    assert plugin_pin.ci_commits(tmp_path / "nowhere") == set()


def test_a_repository_that_names_no_commit_may_be_written_by_any_plugin(tmp_path: Path) -> None:
    used, _ = plugin(tmp_path / "plugin")
    root = pinned_repository(tmp_path, refs=("<ratchet-sha>",))
    assert plugin_pin.write_refusal(root, used) is None
    unfilled = pinned_repository(tmp_path / "x", pin="<X.Y.Z> at <ratchet-sha>")
    assert plugin_pin.write_refusal(unfilled, used) is None


def test_the_plugin_at_the_one_commit_ci_runs_may_write(tmp_path: Path) -> None:
    used, commit = plugin(tmp_path / "plugin")
    root = pinned_repository(tmp_path, pin=commit, refs=(commit,))
    assert plugin_pin.write_refusal(root, used) is None
    assert plugin_pin.write_refusal(pinned_repository(tmp_path / "x", refs=(commit,)), used) is None


@pytest.mark.parametrize(
    ("pin", "refs"),
    [(ELSEWHERE, ()), (None, (ELSEWHERE,)), (ELSEWHERE, (ELSEWHERE,))],
    ids=["the pin is another commit", "refs only", "pin and refs agree on another commit"],
)
def test_a_plugin_that_is_not_the_one_ci_runs_may_not_write(
    pin: str | None, refs: tuple[str, ...], tmp_path: Path
) -> None:
    used, commit = plugin(tmp_path / "plugin")
    message = plugin_pin.write_refusal(pinned_repository(tmp_path, pin=pin, refs=refs), used)
    assert message is not None
    assert f"CI runs the plugin at {ELSEWHERE[:12]} and this one is 1.2.3 at {commit}" in message
    assert f"python3 {used / 'scripts/upgrade.py'} --apply" in message
    assert "RATCHET_PLUGIN" in message


def test_a_pin_and_workflows_at_two_commits_are_a_half_done_upgrade_no_plugin_writes(
    tmp_path: Path,
) -> None:
    used, commit = plugin(tmp_path / "plugin")
    message = plugin_pin.write_refusal(
        pinned_repository(tmp_path, pin=commit, refs=(SECOND,)), used
    )
    assert message is not None
    named = "(" + ", ".join(sorted([commit[:12], SECOND[:12]])) + ")"
    assert f"name 2 plugin commits {named}, so an upgrade is half done" in message
    assert "Finish it with `python3 " in message
    assert "RATCHET_PLUGIN" in message


def test_a_copy_with_no_history_of_the_pinned_version_may_write(tmp_path: Path) -> None:
    used = tmp_path / "cache/ratchet/1.2.3"
    repos.write(used, MANIFEST, '{"version": "1.2.3"}')
    assert plugin_pin.write_refusal(pinned_repository(tmp_path, pin=FIRST), used) is None
    other = pinned_repository(tmp_path / "x", pin=FIRST)
    adopted(other, f"- Ratchet plugin: 1.3.0 at {FIRST}")
    message = plugin_pin.write_refusal(other, used)
    assert message is not None
    assert "this one is 1.2.3," in message
    refs_only = pinned_repository(tmp_path / "y", refs=(FIRST,))
    assert plugin_pin.write_refusal(refs_only, used) is not None
    two = pinned_repository(tmp_path / "z", pin=FIRST, refs=(SECOND,))
    assert plugin_pin.write_refusal(two, used) is not None
