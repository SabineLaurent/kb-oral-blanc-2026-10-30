"""The environment is read in one place, commands/environment.py, called once by cli.py: the commands
and the readers get it as a parameter (.claude/rules/poc.md, rule 2)."""

import ast

import pytest
from module_imports import PACKAGE_DIR

# The only module that reads the environment, and the only one that calls it.
ENVIRONMENT_MODULE = "commands/environment.py"
ENTRY_POINT = "cli.py"
# For now, the GitHub API client reads its token itself: passing it to the GitHub sources waits for
# one way to create every source that needs the environment (ADR of 2026-10-25).
TOLERATED_FOR_NOW = {"etl/sources/github/api.py"}
# os, and posix or nt, the module os takes environ from.
ENVIRONMENT_MODULES = {"os", "posix", "nt"}
OS_ENVIRONMENT_NAMES = {
    "environ",
    "getenv",
    "putenv",
    "unsetenv",
    "environb",
    "getenvb",
}
READ_ENVIRONMENT = "read_environment"


def find_environment_readings(code: str) -> list[int]:
    """The lines where the code reads the environment through os (or posix, nt), under its own name
    or an alias: an attribute, a getattr, or a name imported from it, * included."""
    tree = ast.parse(code)
    module_names = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name in ENVIRONMENT_MODULES
    }
    lines = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in module_names
            and node.attr in OS_ENVIRONMENT_NAMES
        ):
            lines.append(node.lineno)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in module_names
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in OS_ENVIRONMENT_NAMES
        ):
            lines.append(node.lineno)
        if (
            isinstance(node, ast.ImportFrom)
            and node.module in ENVIRONMENT_MODULES
            and any(
                alias.name in OS_ENVIRONMENT_NAMES or alias.name == "*"
                for alias in node.names
            )
        ):
            lines.append(node.lineno)
    return lines


def mentions_read_environment(code: str) -> bool:
    """Whether the code imports, names or calls read_environment, by any way."""
    for node in ast.walk(ast.parse(code)):
        if isinstance(node, ast.Name) and node.id == READ_ENVIRONMENT:
            return True
        if isinstance(node, ast.Attribute) and node.attr == READ_ENVIRONMENT:
            return True
        if isinstance(node, ast.ImportFrom) and any(
            alias.name == READ_ENVIRONMENT for alias in node.names
        ):
            return True
    return False


def read_modules():
    return {
        path.relative_to(PACKAGE_DIR).as_posix(): path.read_text(encoding="utf-8")
        for path in PACKAGE_DIR.rglob("*.py")
    }


def test_only_commands_environment_reads_the_environment():
    readers = {
        path for path, code in read_modules().items() if find_environment_readings(code)
    }

    assert readers == {ENVIRONMENT_MODULE} | TOLERATED_FOR_NOW


def test_only_cli_calls_read_environment():
    callers = {
        path
        for path, code in read_modules().items()
        if mentions_read_environment(code) and path != ENVIRONMENT_MODULE
    }

    assert callers == {ENTRY_POINT}


@pytest.mark.parametrize(
    ("code", "is_reading"),
    [
        ("import os\nos.environ.get('KB_DATA_DIR')", True),
        ("import os\nos.getenv('KB_DATA_DIR')", True),
        ("import os\nvalue = os.environ['KB_DATA_DIR']", True),
        ("import os as system\nsystem.environ['KB_DATA_DIR']", True),
        ("import posix\nposix.environ", True),
        ("import os\ngetattr(os, 'environ')", True),
        ("from os import environ", True),
        ("from os import getenv as read", True),
        ("from os import *", True),
        ("def run(environ):\n    return environ.get('KB_DATA_DIR')", False),
        ("import os\nos.path.join('a', 'b')", False),
        ("import os\ngetattr(os, 'sep')", False),
        ("# os.environ in a comment", False),
    ],
)
def test_the_rule_finds_every_way_to_read_the_environment_and_only_them(
    code, is_reading
):
    assert bool(find_environment_readings(code)) is is_reading


@pytest.mark.parametrize(
    ("code", "is_mentioned"),
    [
        ("from k_b.commands.environment import read_environment", True),
        ("from k_b.commands import environment\nenvironment.read_environment()", True),
        ("from k_b.commands.environment import read_data_dir", False),
    ],
)
def test_the_rule_finds_every_way_to_call_read_environment(code, is_mentioned):
    assert mentions_read_environment(code) is is_mentioned
