"""Every source module is built the same way, so that any source reads with the same map: its
identity, then EXTRACT (the Source class and what fetch needs), then TRANSFORM (what is kept, the
reading, the conversion into an item). The LOAD is common to every source: etl/load.py and
database/writers/."""

import ast

import pytest
from module_imports import PACKAGE_DIR

SOURCES_DIR = PACKAGE_DIR / "etl" / "sources"
# Not a source: the client of the GitHub API, shared by the GitHub sources.
HELPER_MODULES = {"github/api"}
SOURCE_MODULES = sorted(
    path.relative_to(SOURCES_DIR).with_suffix("").as_posix()
    for path in SOURCES_DIR.rglob("*.py")
    if path.stem != "__init__"
    and path.relative_to(SOURCES_DIR).with_suffix("").as_posix() not in HELPER_MODULES
)
EXTRACT_BANNER = "# === EXTRACT ==="
TRANSFORM_BANNER = "# === TRANSFORM ==="
READER_NAMES = ("READER", "TREND_READER")


def read_module(name):
    """The lines of the module, its top-level statements, and the line of each banner (1-based)."""
    text = (SOURCES_DIR / f"{name}.py").read_text(encoding="utf-8")
    lines = text.splitlines()
    banner_lines = {
        banner: [number for number, line in enumerate(lines, 1) if line == banner]
        for banner in (EXTRACT_BANNER, TRANSFORM_BANNER)
    }
    return ast.parse(text).body, banner_lines


def defined_name(node):
    if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
        return node.name
    target = (
        node.targets[0]
        if isinstance(node, ast.Assign)
        else getattr(node, "target", None)
    )
    return target.id if isinstance(target, ast.Name) else None


def find_line(body, name):
    return next(node.lineno for node in body if defined_name(node) == name)


def find_reader_functions(body):
    """The functions a reader points to; a ranking has no reader (it does not enter the base),
    its reading is read_ranking."""
    readers = [node for node in body if defined_name(node) in READER_NAMES]
    if not readers:
        return ["read_ranking"]
    return [
        argument.id
        for reader in readers
        for argument in ast.walk(reader.value)
        if isinstance(argument, ast.Name)
        and argument.id.startswith(("read_", "convert_"))
    ]


@pytest.mark.parametrize("name", SOURCE_MODULES)
def test_a_source_module_has_its_extract_banner_then_its_transform_banner_once(name):
    _, banner_lines = read_module(name)

    assert len(banner_lines[EXTRACT_BANNER]) == 1
    assert len(banner_lines[TRANSFORM_BANNER]) == 1
    assert banner_lines[EXTRACT_BANNER][0] < banner_lines[TRANSFORM_BANNER][0]


@pytest.mark.parametrize("name", SOURCE_MODULES)
def test_the_identity_of_a_source_comes_before_its_extract(name):
    body, banner_lines = read_module(name)

    assert find_line(body, "FOLDER_PREFIX") < banner_lines[EXTRACT_BANNER][0]


@pytest.mark.parametrize("name", SOURCE_MODULES)
def test_the_source_class_is_under_extract(name):
    body, banner_lines = read_module(name)
    [extract_line], [transform_line] = banner_lines.values()
    source_classes = [
        node.lineno
        for node in body
        if isinstance(node, ast.ClassDef) and node.name.endswith("Source")
    ]

    assert len(source_classes) == 1
    assert extract_line < source_classes[0] < transform_line


@pytest.mark.parametrize("name", SOURCE_MODULES)
def test_the_reading_the_conversion_and_the_reader_are_under_transform(name):
    body, banner_lines = read_module(name)
    [transform_line] = banner_lines[TRANSFORM_BANNER]
    names = find_reader_functions(body) + [
        defined_name(node)
        for node in body
        if defined_name(node) in READER_NAMES
        or (defined_name(node) or "").startswith("convert_")
    ]

    assert names
    for defined in names:
        assert find_line(body, defined) > transform_line, defined


@pytest.mark.parametrize("name", SOURCE_MODULES)
def test_the_conversion_comes_after_the_reading(name):
    body, banner_lines = read_module(name)
    [transform_line] = banner_lines[TRANSFORM_BANNER]
    functions = [
        (node.lineno, node.name)
        for node in body
        if isinstance(node, ast.FunctionDef) and node.lineno > transform_line
    ]
    readings = [
        line for line, function in functions if function.startswith(("read_", "parse_"))
    ]
    conversions = [
        line for line, function in functions if function.startswith("convert_")
    ]

    assert not conversions or max(readings) < min(conversions)
