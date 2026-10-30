"""The reader of the imports: every form of import seen, cut at the level asked, and the files of
a module listed."""

import pytest
from module_imports import (
    COMPOSITION_MODULES,
    ETL_SOURCES_PACKAGE,
    PACKAGE_DIR,
    find_package,
    is_within,
    list_python_files,
    parse_imported_modules,
    reaches,
)


@pytest.mark.parametrize(
    "code",
    [
        "import k_b.etl.sources.arxiv",
        "import k_b.etl.sources.arxiv as source",
        "from k_b.etl import sources",
        "from k_b.etl.sources import arxiv",
        "from k_b.etl.sources.arxiv import ArxivSource",
        "from .etl import sources",
        "from .etl.sources.arxiv import ArxivSource",
        "def f():\n    from k_b.etl.sources.arxiv import ArxivSource",
    ],
)
def test_every_form_of_import_is_seen(code):
    assert parse_imported_modules(code) == {"etl.sources"}


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("from k_b import cli", {"cli"}),
        ("from k_b.items import Item", {"items.Item"}),
        ("from k_b.core.config import TrackedRepository", {"core.config"}),
    ],
)
def test_a_name_is_cut_at_two_levels(code, expected):
    assert parse_imported_modules(code) == expected


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("from k_b import ai_api", {"ai_api"}),
        ("from k_b.ai_api import connectors", {"ai_api.connectors"}),
        (
            "from k_b.ai_api.connectors.llm_client import call_model",
            {"ai_api.connectors.llm_client"},
        ),
        ("from k_b.ai_api.deadline import Deadline", {"ai_api.deadline.Deadline"}),
        ("from .llm_replay import ReplayTransport", {"ai_api.connectors.llm_replay"}),
    ],
)
def test_a_name_is_cut_at_three_levels_when_asked(code, expected):
    imported = parse_imported_modules(code, package="k_b.ai_api.connectors", depth=3)

    assert imported == expected


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("from . import github_api", {"etl.sources"}),
        ("from .github_api import get_from_github_api", {"etl.sources"}),
        ("from .. import source_kinds", {"etl.source_kinds"}),
        ("from ... import cli", {"cli"}),
        ("from ...cli import main", {"cli.main"}),
    ],
)
def test_relative_imports_inside_the_sources_are_resolved(code, expected):
    assert parse_imported_modules(code, package="k_b.etl.sources") == expected


def test_a_relative_import_above_k_b_is_refused():
    with pytest.raises(ValueError, match="level 4 above the package k_b.etl.sources"):
        parse_imported_modules("from .... import cli", package="k_b.etl.sources")


@pytest.mark.parametrize(
    ("name", "expected"),
    [("core", True), ("core.config", True), ("corepart", False), ("items.Item", False)],
)
def test_a_name_is_within_a_module_by_prefix(name, expected):
    assert is_within(name, {"core"}) is expected


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("from k_b import etl", True),
        ("import k_b.etl", True),
        ("from k_b.etl import sources", True),
        ("from k_b.etl import transform", False),
    ],
)
def test_an_import_of_a_package_that_holds_a_forbidden_module_is_seen(code, expected):
    forbidden = {ETL_SOURCES_PACKAGE} | COMPOSITION_MODULES
    imported = parse_imported_modules(code)

    assert any(reaches(name, forbidden) for name in imported) is expected


def test_the_modules_of_a_sub_folder_of_a_package_are_listed():
    paths = list_python_files("database")

    assert PACKAGE_DIR / "database" / "writers" / "paper_writer.py" in paths
    assert find_package(PACKAGE_DIR / "database" / "writers" / "paper_writer.py") == (
        "k_b.database.writers"
    )
