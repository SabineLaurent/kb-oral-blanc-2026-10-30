"""What the tests share: the fabricated Zotero library, the test base of the readers, the
settings of the terminal removed, the end-to-end tests left out, and the HTML report."""

import html
import os
import re
import sqlite3
from pathlib import Path

import pytest

from k_b.database.connection import connect_database
from k_b.database.creation import create_database

FIXTURES_DIR = Path(__file__).parent / "fixtures"
ZOTERO_LIBRARY_SCRIPT = FIXTURES_DIR / "zotero_extract.sql"
ITEMS_DATABASE_SCRIPT = FIXTURES_DIR / "items_database.sql"
# The settings of kb serve-data and of the log, which a terminal may export (a .env loaded).
KB_SETTINGS_VARIABLES = (
    "KB_DATA_API_HOST",
    "KB_DATA_API_PORT",
    "KB_DATA_API_ALLOWED_HOSTS",
    "KB_LOG_TO_FILE",
)
# The marker of the tests that run kb on the real sources (tests/e2e/).
E2E_MARKER = "e2e"
# A marker name of a -m expression, by the rule ident of pytest's grammar
# (_pytest/mark/expression.py:10, pytest 9.1.1): nightly-e2e and e2e.slow are one name each.
MARKER_NAME = re.compile(r"(?:\w|:|\+|-|\.|\[|\]|\\|/)+")
# The HTML report keeps the captured output of every test: the user alone reads it
# (docs/registre-traitements.md, step 10 of the sorting). In the repository, only its folder
# is ignored by git (.gitignore).
REPORT_DIR_NAME = "reports"
REPORT_DIR_MODE = 0o700
REPORT_FILE_MODE = 0o600
# The column of the recorded properties, before the links (the last default column of
# pytest-html 4.2.0: result, test, duration, links).
PROPERTIES_COLUMN = 3


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        f"{E2E_MARKER}: runs kb on a real source; left out unless -m names {E2E_MARKER}",
    )
    restrict_report_to_user(config)


def restrict_report_to_user(config):
    """Create the report of --html in 0600, and its new folder in 0700, before pytest-html writes
    it in place; a report in the repository outside reports/, which git would not ignore, stops
    the run."""
    report_option = config.getoption("htmlpath", default=None)
    if not report_option:
        return
    # Resolved as pytest-html resolves it (BaseReport.__init__, pytest-html 4.2.0).
    report_path = Path.cwd() / Path(os.path.expandvars(report_option)).expanduser()
    resolved_path = report_path.resolve()
    repository_dir = config.rootpath.resolve()
    if resolved_path.is_relative_to(
        repository_dir
    ) and not resolved_path.is_relative_to(repository_dir / REPORT_DIR_NAME):
        pytest.exit(
            f"--html: {resolved_path} is in the repository outside {REPORT_DIR_NAME}/,"
            " which git does not ignore",
            returncode=pytest.ExitCode.USAGE_ERROR,
        )
    report_path.parent.mkdir(mode=REPORT_DIR_MODE, parents=True, exist_ok=True)
    os.close(os.open(report_path, os.O_WRONLY | os.O_CREAT, REPORT_FILE_MODE))
    report_path.chmod(REPORT_FILE_MODE)


@pytest.hookimpl(optionalhook=True)
def pytest_html_results_table_header(cells):
    cells.insert(PROPERTIES_COLUMN, "<th>Properties</th>")


@pytest.hookimpl(optionalhook=True)
def pytest_html_results_table_row(report, cells):
    """The properties the test recorded, one name=value a line, escaped."""
    properties = getattr(report, "user_properties", ())
    lines = "<br>".join(html.escape(f"{name}={value}") for name, value in properties)
    cells.insert(PROPERTIES_COLUMN, f'<td class="col-properties">{lines}</td>')


def pytest_collection_modifyitems(config, items):
    """Leave out the end-to-end tests unless -m names them, so that the CI never calls a real
    source."""
    # The name e2e, not a marker name that holds it (nightly_e2e, nightly-e2e, e2e.slow).
    if E2E_MARKER in MARKER_NAME.findall(config.option.markexpr):
        return
    deselected = [item for item in items if item.get_closest_marker(E2E_MARKER)]
    if not deselected:
        return
    config.hook.pytest_deselected(items=deselected)
    items[:] = [item for item in items if not item.get_closest_marker(E2E_MARKER)]


@pytest.fixture
def connect_with_factory():
    """connect_database, opening a subclass of sqlite3.Connection: the tests change its commit."""

    def connect_with(factory):
        def connect(database_path):
            connection = sqlite3.connect(database_path, factory=factory)
            connection.execute("PRAGMA foreign_keys = ON")
            return connection

        return connect

    return connect_with


@pytest.fixture(autouse=True)
def no_kb_setting_of_the_terminal(monkeypatch):
    """A terminal that exports a setting of kb must not change what the tests check."""
    for name in KB_SETTINGS_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def run_on_library(zotero_dir, script):
    """Run an SQL script on the library of zotero_dir, in WAL mode as Zotero's own base."""
    connection = sqlite3.connect(zotero_dir / "zotero.sqlite")
    try:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(script)
    finally:
        connection.close()


@pytest.fixture
def zotero_dir(tmp_path):
    """A Zotero data directory with the fabricated library, closed cleanly."""
    directory = tmp_path / "Zotero"
    directory.mkdir()
    run_on_library(directory, ZOTERO_LIBRARY_SCRIPT.read_text())
    return directory


@pytest.fixture
def run_on_zotero_library():
    """Change the fabricated library of a test with an SQL script."""
    return run_on_library


@pytest.fixture(scope="session")
def items_database(tmp_path_factory):
    """The path of the test base of the readers, built once, read only; its folder name holds a
    space, #, ? and %, which its URI must escape."""
    database_dir = tmp_path_factory.mktemp("data dir #1 ?%", numbered=False)
    database_path = database_dir / "k-b.sqlite"
    create_database(database_path)
    connection = connect_database(database_path)
    try:
        connection.executescript(ITEMS_DATABASE_SCRIPT.read_text())
    finally:
        connection.close()
    return database_path
