"""The CI leaves out the end-to-end tests; -m e2e runs them, and stops them on a variable they
must not get; a recorded property is in the HTML report, readable by the user alone."""

import html
import importlib.util
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from k_b.commands.environment import DATA_DIR_VARIABLE, LOG_TO_FILE_VARIABLE
from k_b.core.api_keys import API_KEY_VARIABLE_PREFIX
from k_b.etl.collected_sources import ZOTERO_DIR_VARIABLE
from k_b.etl.source_kinds import SOURCE_KINDS

TESTS_DIR = Path(__file__).parent
E2E_DIR = TESTS_DIR / "e2e"
# The exit code of pytest when it collects no test: every one was left out.
NO_TESTS_COLLECTED = pytest.ExitCode.NO_TESTS_COLLECTED


def collect_e2e_dir(*options):
    """The ids pytest collects in tests/e2e/ with the options given, its output and its exit
    code."""
    result = subprocess.run(  # noqa: S603 — runs this interpreter on pytest, fixed arguments
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            *options,
            str(E2E_DIR),
        ],
        capture_output=True,
        text=True,
        cwd=TESTS_DIR.parent,
        # The options of the terminal would change what is collected, not the code.
        env={
            name: value
            for name, value in os.environ.items()
            if name != "PYTEST_ADDOPTS"
        },
    )
    ids = re.findall(r"::\w+\[(\w+)\]$", result.stdout, re.MULTILINE)
    return ids, result.stdout, result.returncode


# The end-to-end tests that are not of a source: the export of the metrics (tests/e2e/
# test_real_export.py, #162).
END_TO_END_TESTS_BEYOND_THE_SOURCES = 1


def check_every_end_to_end_test_left_out(*options):
    ids, output, exit_code = collect_e2e_dir(*options)

    assert ids == []
    assert exit_code == NO_TESTS_COLLECTED
    deselected = len(SOURCE_KINDS) + END_TO_END_TESTS_BEYOND_THE_SOURCES
    assert f"{deselected} deselected" in output


def test_pytest_without_m_leaves_out_every_end_to_end_test():
    check_every_end_to_end_test_left_out()


@pytest.mark.parametrize(
    "expression", ["not nightly_e2e", "not nightly-e2e", "not e2e.slow"]
)
def test_pytest_m_of_a_marker_that_holds_e2e_leaves_out_every_end_to_end_test(
    expression,
):
    check_every_end_to_end_test_left_out("-m", expression)


def test_pytest_m_e2e_collects_one_end_to_end_test_per_source():
    ids, _, _ = collect_e2e_dir("-m", "e2e")

    assert ids == [kind.folder_prefix for kind in SOURCE_KINDS]


# A value the end-to-end tests must never print, in a variable they must never get.
VALUE_BEYOND = "value-beyond-the-end-to-end-tests"


def run_the_zotero_end_to_end_test(variables):
    """pytest -m e2e -k zotero with the variables given, without KB_ZOTERO_DIR, so that even with
    the guard broken no real source is called."""
    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in ("PYTEST_ADDOPTS", ZOTERO_DIR_VARIABLE)
    }
    environment.update(variables)
    return subprocess.run(  # noqa: S603 — runs this interpreter on pytest, fixed arguments
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-m",
            "e2e",
            "-k",
            "zotero",
            str(E2E_DIR),
        ],
        capture_output=True,
        text=True,
        cwd=TESTS_DIR.parent,
        env=environment,
    )


def test_the_end_to_end_tests_stop_on_a_variable_beyond_theirs_without_its_value():
    result = run_the_zotero_end_to_end_test(
        {
            "KB_LLM_API_KEY": VALUE_BEYOND,
            # Set or removed by the test before any kb: never a reason to stop.
            DATA_DIR_VARIABLE: VALUE_BEYOND,
            LOG_TO_FILE_VARIABLE: VALUE_BEYOND,
        }
    )

    assert result.returncode == pytest.ExitCode.USAGE_ERROR
    assert "1 selected" in result.stdout
    assert "KB_LLM_API_KEY" in result.stdout
    assert DATA_DIR_VARIABLE not in result.stdout
    assert LOG_TO_FILE_VARIABLE not in result.stdout
    assert " skipped" not in result.stdout
    assert " passed" not in result.stdout
    assert VALUE_BEYOND not in result.stdout + result.stderr


def test_the_end_to_end_tests_stop_on_the_key_of_any_client_without_its_value():
    """kb serve-data reads every KB_DATA_API_KEY_<CLIENT>: a client that .env.example does not name stops
    the tests too."""
    key_variable = f"{API_KEY_VARIABLE_PREFIX}CLIENT_NOT_IN_THE_EXAMPLE"

    result = run_the_zotero_end_to_end_test({key_variable: VALUE_BEYOND})

    assert result.returncode == pytest.ExitCode.USAGE_ERROR
    assert key_variable in result.stdout
    assert " skipped" not in result.stdout
    assert VALUE_BEYOND not in result.stdout + result.stderr


def write_marker_test(directory):
    """A test that records a property, run with a copy of the conftest of the suite."""
    shutil.copy(TESTS_DIR / "conftest.py", directory / "conftest.py")
    (directory / "test_marker.py").write_text(
        "def test_marker(record_property):\n"
        f"    record_property('marker', {MARKER!r})\n"
    )


# A value with a tag: the report shows it escaped, never as HTML.
MARKER = "<b>marker</b>"


def produce_report(directory, report_path):
    """Run the marker test of directory with a report at report_path, directory as --rootdir:
    under a --basetemp in K-B, pytest would take K-B by its pyproject.toml."""
    return subprocess.run(  # noqa: S603 — runs this interpreter on pytest, fixed arguments
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            f"--rootdir={directory}",
            f"--html={report_path}",
            "--self-contained-html",
            str(directory / "test_marker.py"),
        ],
        capture_output=True,
        text=True,
        cwd=directory,
        env={
            name: value
            for name, value in os.environ.items()
            if name != "PYTEST_ADDOPTS"
        },
    )


def read_report_data(report_path):
    """The data of the report, which its page renders: an attribute holding JSON."""
    blob = re.search(r'data-jsonblob="([^"]*)"', report_path.read_text())
    assert blob is not None
    return html.unescape(blob.group(1))


def test_a_recorded_property_is_in_the_report_escaped(tmp_path):
    write_marker_test(tmp_path)
    report_path = tmp_path / "reports" / "report.html"

    result = produce_report(tmp_path, report_path)

    assert result.returncode == pytest.ExitCode.OK

    report_data = read_report_data(report_path)
    assert f"marker={html.escape(MARKER)}" in report_data
    assert MARKER not in report_data


def test_the_report_and_its_new_folder_are_readable_by_the_user_alone(tmp_path):
    write_marker_test(tmp_path)
    report_path = tmp_path / "reports" / "report.html"

    result = produce_report(tmp_path, report_path)

    assert result.returncode == pytest.ExitCode.OK
    assert stat.S_IMODE(report_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600


def test_a_report_written_before_is_made_readable_by_the_user_alone(tmp_path):
    write_marker_test(tmp_path)
    report_path = tmp_path / "reports" / "report.html"
    report_path.parent.mkdir()
    report_path.write_text("")
    report_path.chmod(0o644)

    result = produce_report(tmp_path, report_path)

    assert result.returncode == pytest.ExitCode.OK
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600


def test_a_report_in_the_repository_outside_reports_is_refused_before_any_file(
    tmp_path,
):
    """Only reports/ is ignored by git: a report elsewhere in the repository could be
    committed."""
    write_marker_test(tmp_path)
    report_path = tmp_path / "docs" / "report.html"

    result = produce_report(tmp_path, report_path)

    assert result.returncode == pytest.ExitCode.USAGE_ERROR
    # Stopped while pytest configures: the message goes to stderr.
    assert str(report_path) in result.stderr
    assert not report_path.parent.exists()


def test_a_report_outside_the_repository_is_written(tmp_path, tmp_path_factory):
    repository_dir = tmp_path / "repository"
    repository_dir.mkdir()
    write_marker_test(repository_dir)
    report_path = tmp_path_factory.mktemp("outside") / "report.html"

    result = produce_report(repository_dir, report_path)

    assert result.returncode == pytest.ExitCode.OK
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600


def load_end_to_end_module():
    """The conftest of the end-to-end tests, loaded by its path, as tests/e2e/ is not a package."""
    spec = importlib.util.spec_from_file_location(
        "end_to_end_tests", E2E_DIR / "conftest.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_name_commented_out_in_the_end_to_end_example_is_not_theirs(tmp_path):
    """.env.example comments out some keys (KB_DATA_API_KEY_AI_DIGEST): its commented names count.
    In .env.e2e_test.example, a commented name is not a variable of the tests."""
    env_example_path = tmp_path / ".env.example"
    env_example_path.write_text(
        "KB_LLM_API_KEY=\n# KB_DATA_API_KEY_AI_DIGEST=\nKB_GITHUB_TOKEN=\n"
    )
    e2e_env_example_path = tmp_path / ".env.e2e_test.example"
    e2e_env_example_path.write_text("# KB_LLM_API_KEY=\nKB_GITHUB_TOKEN=\n")
    end_to_end_tests = load_end_to_end_module()

    names_beyond = end_to_end_tests.find_names_beyond_the_end_to_end_tests(
        env_example_path, e2e_env_example_path
    )

    assert names_beyond == {"KB_LLM_API_KEY", "KB_DATA_API_KEY_AI_DIGEST"}
