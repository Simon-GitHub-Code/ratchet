"""The flags gate: the registry's shape, new flags off, dead keys, and the removal date."""

import datetime as dt
from pathlib import Path

import pytest

import flags
from tests.repos import branch, commit, repository, write

Out = pytest.CaptureFixture[str]
GOOD = (
    '[[flag]]\nkey = "new-checkout"\nowner = "payments"\ndefault = false\n'
    'remove-by = 2027-02-01\nreason = "Ships the new checkout dark for now."\n'
)


def run(root: Path, *options: str) -> int:
    return flags.main(["flags", "--root", str(root), *options])


def table(**changes: object) -> flags.Flag:
    flag: flags.Flag = {
        "key": "new-checkout",
        "owner": "payments",
        "default": False,
        "remove-by": dt.date(2027, 2, 1),
        "reason": "Ships the new checkout dark for now.",
    }
    flag.update(changes)
    return {key: value for key, value in flag.items() if value is not None}


def test_a_missing_registry_is_red_and_an_empty_one_is_green(tmp_path: Path, capsys: Out) -> None:
    root = repository(tmp_path)
    assert run(root) == 1
    assert "flags.toml: missing; an empty file declares no flags" in capsys.readouterr().out
    write(root, "flags.toml", "")
    assert run(root) == 0
    assert capsys.readouterr().out.endswith("flags: green\n")


def test_a_registry_that_does_not_parse_is_red_in_both_modes(tmp_path: Path, capsys: Out) -> None:
    root = repository(tmp_path, {"flags.toml": "[[flag]\n"})
    assert run(root) == 1
    assert "flags.toml: does not parse: " in capsys.readouterr().out
    assert run(root, "--expired") == 1
    assert "flags-expired: red" in capsys.readouterr().out


def test_flag_must_be_an_array_of_tables() -> None:
    assert flags.parse('flag = "x"\n') == (
        [],
        ["flags.toml: 'flag' is not an array of tables; write [[flag]]"],
    )
    assert flags.parse("flag = [1]\n") == ([], [])


def test_a_well_formed_flag_has_no_shape_problem() -> None:
    assert flags.shape(table()) == []
    assert flags.shape(table(**{"remove-by": None, "permanent": True})) == []
    assert flags.shape(table(key="a.b_c-d2")) == []


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"key": None}, "flag '<no key>': key is missing or is not lower-case words"),
        ({"key": "New"}, "flag 'New': key is missing or is not lower-case words"),
        ({"key": "a--b"}, "flag 'a--b': key is missing"),
        ({"key": "a b"}, "flag 'a b': key is missing"),
        ({"key": 3}, "flag '3': key is missing"),
        ({"owner": None}, "owner is missing"),
        ({"owner": " "}, "owner is missing"),
        ({"owner": 1}, "owner is missing"),
        ({"default": None}, "default is missing or is not true or false"),
        ({"default": "off"}, "default is missing or is not true or false"),
        ({"reason": None}, "reason is missing or under 20 characters"),
        ({"reason": "x" * 19 + "   "}, "reason is missing or under 20 characters"),
        ({"reason": 7}, "reason is missing or under 20 characters"),
        ({"remove-by": None}, "needs exactly one of remove-by = <date> and permanent = true"),
        ({"remove-by": "2027-02-01"}, "needs exactly one of remove-by"),
        ({"permanent": True}, "needs exactly one of remove-by"),
        ({"remove-by": None, "permanent": "yes"}, "needs exactly one of remove-by"),
    ],
)
def test_each_missing_piece_is_named(changes: dict[str, object], problem: str) -> None:
    lines = flags.shape(table(**changes))
    assert len(lines) == 1
    assert lines[0].startswith("flags.toml: flag '")
    assert problem in lines[0]


def test_a_reason_of_exactly_twenty_characters_passes() -> None:
    assert flags.shape(table(reason="x" * 20)) == []


def test_a_key_declared_twice_is_named_once() -> None:
    assert flags.duplicates([table(), table(), table(key="other")]) == [
        "flags.toml: flag 'new-checkout': declared 2 times"
    ]


def test_a_new_flag_ships_off_and_a_known_one_may_be_turned_on(tmp_path: Path, capsys: Out) -> None:
    root = repository(tmp_path, {"flags.toml": ""})
    branch(root)
    write(root, "flags.toml", GOOD.replace("default = false", "default = true"))
    assert run(root) == 1
    assert (
        "flags.toml: flag 'new-checkout': a new flag defaults to false, so the change ships off"
        in capsys.readouterr().out
    )
    assert run(root, "--base", "main") == 1
    write(root, "flags.toml", GOOD)
    assert run(root) == 0
    commit(root, "feat(core): the flag, off")
    write(root, "flags.toml", GOOD.replace("default = false", "default = true"))
    assert run(root) == 1, "still new against the merge base"
    assert flags.born_on([table(default=True)], [table()]) == []


def test_without_any_commit_every_flag_is_new(tmp_path: Path) -> None:
    write(tmp_path, "flags.toml", GOOD.replace("default = false", "default = true"))
    assert run(tmp_path) == 1
    write(tmp_path, "flags.toml", GOOD)
    assert run(tmp_path) == 0


def test_a_declared_key_no_source_file_names_is_red(tmp_path: Path, capsys: Out) -> None:
    root = repository(
        tmp_path, {"flags.toml": GOOD, "src/a.py": "x = 1\n", "docs/a.md": "new-checkout\n"}
    )
    assert run(root) == 0, "without --used-in the tree is not read"
    assert run(root, "--used-in", "src", "--used-in", "lib/") == 1
    assert (
        "flags.toml: flag 'new-checkout': no file under src, lib/ names it; "
        "remove the entry with the code" in capsys.readouterr().out
    )
    write(root, "srcs/a.py", 'on("new-checkout")\n')
    assert run(root, "--used-in", "src") == 1, "a directory, not a prefix of a name"
    write(root, "src/b.py", 'on("new-checkout")\n')
    assert run(root, "--used-in", "src") == 0
    write(root, "src/b.py", "x = 2\n")
    write(root, "src/blob.bin", "new-checkout\0")
    assert run(root, "--used-in", "src") == 1, "a binary file names nothing"


def test_a_flag_past_its_date_is_red_only_when_asked(tmp_path: Path, capsys: Out) -> None:
    root = repository(tmp_path, {"flags.toml": GOOD})
    assert run(root, "--today", "2027-03-01") == 0, "the pull-request question ignores the date"
    capsys.readouterr()
    assert run(root, "--expired", "--today", "2027-02-01") == 0, "the date itself is not past"
    assert capsys.readouterr().out == "flags-expired: green\n"
    assert run(root, "--expired", "--today", "2027-02-02") == 1
    assert (
        "flags.toml: flag 'new-checkout': remove-by 2027-02-01 has passed"
        in capsys.readouterr().out
    )
    assert run(root, "--expired") == 0, "today, from the clock, is before 2027"


def test_a_permanent_flag_never_expires() -> None:
    permanent = table(**{"remove-by": None, "permanent": True})
    assert flags.expired([permanent], dt.date(2999, 1, 1)) == []


def test_expired_does_not_judge_shape(tmp_path: Path) -> None:
    root = repository(tmp_path, {"flags.toml": '[[flag]]\nkey = "x"\n'})
    assert run(root, "--expired") == 0
    assert run(root) == 1


def test_changed_prints_the_keys_the_change_adds_or_edits(tmp_path: Path, capsys: Out) -> None:
    root = repository(tmp_path, {"flags.toml": GOOD})
    branch(root)
    assert run(root, "--changed") == 0
    assert capsys.readouterr().out == "", "nothing changed, nothing printed, not even a newline"
    other = GOOD.replace("new-checkout", "a-first").replace("payments", "search")
    write(root, "flags.toml", other + GOOD.replace("default = false", "default = true"))
    assert run(root, "--changed") == 0, "it lists; the flags gate judges"
    assert capsys.readouterr().out == "a-first\nnew-checkout\n"
    write(root, "flags.toml", "[[flag]\n")
    assert run(root, "--changed") == 1
    assert capsys.readouterr().out == ""
