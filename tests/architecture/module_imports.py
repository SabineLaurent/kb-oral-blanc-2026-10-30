"""The reader of the imports of k_b and the lists of modules the architecture tests share."""

import ast
from pathlib import Path

import k_b

PACKAGE_DIR = Path(k_b.__file__).parent
TOP_LEVEL_MODULES = {
    "cli",
    "commands",
    "items",
    "core",
    "database",
    "etl",
    "data_api",
    "ai_api",
    "post",
}
# Under k_b.etl, each module is in exactly one of the three.
ETL_COMPOSITION_MODULES = {"etl.source_kinds", "etl.collected_sources"}
ETL_SOURCES_PACKAGE = "etl.sources"
ETL_PIPELINE_MODULES = {
    "etl.pipeline",
    "etl.extract",
    "etl.transform",
    "etl.load",
    "etl.import_log",
    "etl.normalize",
    "etl.raw_files",
    "etl.raw_reading",
    "etl.raw_store",
    "etl.raw_purge",
    "etl.rejections",
    "etl.tops",
    "etl.source_contract",
    "etl.http_download",
    "etl.http_retry",
    "etl.user_agent",
    "etl.file_writing",
    "etl.withdrawals",
}
COMPOSITION_MODULES = {"cli", "commands"} | ETL_COMPOSITION_MODULES


def list_imported_modules(path: Path, package: str, depth: int = 2) -> set[str]:
    """The k_b modules imported by the module at path, of the given package, cut at depth levels."""
    return parse_imported_modules(path.read_text(), package, depth)


def parse_imported_modules(code: str, package: str = "k_b", depth: int = 2) -> set[str]:
    """The k_b modules imported, absolute and relative forms, each named below k_b and cut at
    depth levels (`etl.sources`, `core.config`); a name imported from a module counts as its
    second level.

    A dynamic import (importlib.import_module) is not seen: there is none in k_b."""
    imported: set[str] = set()
    for node in ast.walk(ast.parse(code)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                # A relative import starts from the package, one level up per extra dot.
                package_parts = package.split(".")
                if node.level > len(package_parts):
                    raise ValueError(
                        f"relative import of level {node.level} above the package {package}"
                    )
                base = ".".join(package_parts[: len(package_parts) - node.level + 1])
                origin = f"{base}.{node.module}" if node.module else base
            else:
                origin = node.module or ""
            # The imported name may be a module of the package (`from k_b.etl import sources`).
            names = [f"{origin}.{alias.name}" for alias in node.names]
        else:
            continue
        imported |= {
            ".".join(name.split(".")[1 : 1 + depth])
            for name in names
            if name.startswith("k_b.")
        }
    return imported


def is_within(name: str, modules: set[str]) -> bool:
    """Whether name is one of the modules, or a part of one of them."""
    return any(name == module or name.startswith(f"{module}.") for module in modules)


def reaches(name: str, modules: set[str]) -> bool:
    """Whether importing name reaches one of the modules: name is within one, or a package that
    holds one (`etl` holds `etl.sources`)."""
    return is_within(name, modules) or any(
        module.startswith(f"{name}.") for module in modules
    )


def list_python_files(module: str) -> list[Path]:
    """The Python files of a k_b module or package, those of its sub-folders included."""
    path = PACKAGE_DIR.joinpath(*module.split("."))
    if path.with_suffix(".py").exists():
        return [path.with_suffix(".py")]
    paths = sorted(path.rglob("*.py"))
    assert paths, f"no Python file in {path}"
    return paths


def find_package(path: Path) -> str:
    """The package of the relative imports of the module at path."""
    return ".".join(("k_b", *path.parent.relative_to(PACKAGE_DIR).parts))
