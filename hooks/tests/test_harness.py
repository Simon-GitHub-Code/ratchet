from pathlib import Path

import pytest
from hypothesis import settings

from tests import products
from tests.repos import git, repository
from tests.servers import sleeper


@pytest.mark.parametrize("profile", ["default", "ci"])
def test_no_hypothesis_profile_fails_a_test_for_the_hosts_load(profile: str) -> None:
    assert settings.get_profile(profile).deadline is None


def test_no_test_reads_the_machines_git_config_whose_hooks_path_changes_which_hooks_run(
    tmp_path: Path,
) -> None:
    assert git(tmp_path, "config", "--list", "--show-origin") == ""


def test_a_scratch_repository_starts_no_background_maintenance_a_copy_could_read_mid_run(
    tmp_path: Path,
) -> None:
    assert git(repository(tmp_path), "config", "--get", "maintenance.auto") == "false"


def test_a_process_left_running_is_found_however_narrow_the_terminal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("COLUMNS", "80")
    tree = tmp_path / ("left-running-" + "x" * 200)
    process = sleeper(tree)
    try:
        assert products.processes_naming(str(tree))
    finally:
        process.kill()
        process.wait()
