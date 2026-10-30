"""OpenTelemetry and the HTTP client of its exporter are imported by four modules of the AI API
only, so that the rules of the export are in one place; the private modules of the SDK and of the
exporter, which move from one version to the next, by connectors/trace_export.py only."""

import ast

import pytest
from module_imports import PACKAGE_DIR

ALLOWED_MODULES_BY_LIBRARY = {
    "opentelemetry": {
        "ai_api/connectors/metrics_export.py",
        "ai_api/connectors/trace_export.py",
        "ai_api/observability/metric_instruments.py",
        "ai_api/tracing.py",
    },
    "requests": {"ai_api/connectors/metrics_export.py"},
}
# The private module of the API of the log records, which the façade of the traces uses.
LOGS_API_MODULE = "opentelemetry._logs"
TRACING_MODULE = "ai_api/tracing.py"
TRACE_EXPORT_MODULE = "ai_api/connectors/trace_export.py"


def list_imported_opentelemetry_modules(code: str) -> set[str]:
    """The full names of the modules of opentelemetry that code imports."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(code)):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
    return {name for name in names if name.split(".")[0] == "opentelemetry"}


def is_private(module: str) -> bool:
    return any(part.startswith("_") for part in module.split("."))


def list_imported_top_level_names(code: str) -> set[str]:
    """The top-level names of the absolute imports of code (`opentelemetry`)."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(code)):
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


@pytest.mark.parametrize("library", sorted(ALLOWED_MODULES_BY_LIBRARY))
def test_a_library_of_the_export_is_imported_by_its_modules_only(library):
    importers = {
        path.relative_to(PACKAGE_DIR).as_posix()
        for path in PACKAGE_DIR.rglob("*.py")
        if library in list_imported_top_level_names(path.read_text())
    }

    assert importers == ALLOWED_MODULES_BY_LIBRARY[library]


@pytest.mark.parametrize(
    ("code", "names"),
    [
        ("import opentelemetry.metrics", {"opentelemetry"}),
        ("from opentelemetry.sdk.metrics import MeterProvider", {"opentelemetry"}),
        ("import requests as http", {"requests"}),
        ("from . import metrics", set()),
        (
            "from k_b.ai_api.observability.metric_instruments import ModelMetrics",
            {"k_b"},
        ),
    ],
)
def test_the_reader_of_imports_gives_the_top_level_names(code, names):
    assert list_imported_top_level_names(code) == names


def test_a_private_module_of_opentelemetry_is_imported_by_the_export_of_the_traces_only():
    private_imports = {
        (path.relative_to(PACKAGE_DIR).as_posix(), module)
        for path in PACKAGE_DIR.rglob("*.py")
        for module in list_imported_opentelemetry_modules(path.read_text())
        if is_private(module)
    }

    assert {
        (importer, module)
        for importer, module in private_imports
        if importer != TRACE_EXPORT_MODULE
    } == {(TRACING_MODULE, LOGS_API_MODULE)}


@pytest.mark.parametrize(
    ("module", "is_refused"),
    [
        ("opentelemetry._logs", True),
        ("opentelemetry.sdk._logs.export", True),
        ("opentelemetry.exporter.otlp.proto.http._log_exporter", True),
        ("opentelemetry.sdk.trace", False),
        ("opentelemetry.trace", False),
    ],
)
def test_a_module_is_private_when_one_of_its_parts_starts_with_an_underscore(
    module, is_refused
):
    assert is_private(module) is is_refused
