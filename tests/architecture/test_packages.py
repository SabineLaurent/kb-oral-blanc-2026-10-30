"""Every module of k_b is listed once, and every package keeps an empty __init__.py."""

import pkgutil

from module_imports import (
    ETL_COMPOSITION_MODULES,
    ETL_PIPELINE_MODULES,
    ETL_SOURCES_PACKAGE,
    PACKAGE_DIR,
    TOP_LEVEL_MODULES,
)


def test_every_module_of_k_b_is_listed():
    modules = {module.name for module in pkgutil.iter_modules([str(PACKAGE_DIR)])}

    assert modules == TOP_LEVEL_MODULES


def test_every_module_of_etl_is_in_exactly_one_list():
    modules = {
        f"etl.{module.name}"
        for module in pkgutil.iter_modules([str(PACKAGE_DIR / "etl")])
    }
    lists = (ETL_COMPOSITION_MODULES, {ETL_SOURCES_PACKAGE}, ETL_PIPELINE_MODULES)

    assert modules == set().union(*lists)
    assert sum(len(modules_of_list) for modules_of_list in lists) == len(modules)


# Every __init__.py stays empty, those of a package created later included (code.md,
# "Séparation des responsabilités"; #251): a package gathers its modules, it never re-exports them.
def test_every_package_of_k_b_has_an_empty_init():
    paths = sorted(PACKAGE_DIR.rglob("__init__.py"))

    assert paths
    assert [path for path in paths if path.read_text() != ""] == []
