"""kb openapi: write the OpenAPI of the data API and of the AI API, each in its file, versioned
with the code."""

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from k_b.ai_api.app import create_app as create_ai_app
from k_b.ai_api.connectors.data_api_settings import DataApiSettings
from k_b.ai_api.connectors.llm_settings import REPLAY_MODEL, LlmSettings
from k_b.ai_api.connectors.metrics_export import build_resource
from k_b.ai_api.connectors.trace_export import TraceExport
from k_b.ai_api.observability.metric_instruments import ModelMetrics
from k_b.commands.exit_codes import ExitCode
from k_b.core.atomic_write import write_file_atomically
from k_b.core.config import (
    DATA_API_URL,
    DIGEST_SUBJECTS,
    DIGEST_WINDOW_DAYS,
    LLM_REPLAY_BASE_URL,
)
from k_b.data_api.app import create_app as create_data_app
from k_b.database.connection import DATABASE_FILE_NAME

# Written from the root of the repository, which versions them.
OPENAPI_DIR = Path("docs") / "api"
DATA_API_OPENAPI_PATH = OPENAPI_DIR / "openapi-donnees.json"
AI_API_OPENAPI_PATH = OPENAPI_DIR / "openapi-ia.json"


def run_openapi() -> int:
    # Run from another folder, it would write documents the repository never sees.
    if not OPENAPI_DIR.is_dir():
        print(
            f"error: {OPENAPI_DIR} is not a directory of the current directory;"
            " run kb openapi at the root of the repository",
            file=sys.stderr,
        )
        return ExitCode.NOT_STARTED
    for path, openapi in (
        (DATA_API_OPENAPI_PATH, describe_data_api()),
        (AI_API_OPENAPI_PATH, describe_ai_api()),
    ):
        content = json.dumps(openapi, indent=2, ensure_ascii=False) + "\n"
        try:
            write_file_atomically(path, content.encode())
        except OSError as error:
            print(f"error: {path} not written: {type(error).__name__}", file=sys.stderr)
            return ExitCode.FAILED
        print(f"OpenAPI written: {path}")
    return ExitCode.SUCCEEDED


def describe_data_api() -> dict[str, Any]:
    # The app is made without base nor key: its document does not depend on them.
    return create_data_app(Path(DATABASE_FILE_NAME), {}).openapi()


def describe_ai_api() -> dict[str, Any]:
    """The OpenAPI of the AI API, made without key nor setting of the environment, as its
    document reads none of them."""
    resource = build_resource(None)
    return create_ai_app(
        {},
        LlmSettings(
            provider="replay",
            base_url=LLM_REPLAY_BASE_URL,
            model=REPLAY_MODEL,
            api_key=None,
            reasoning_effort=None,
            replay_steps=(),
        ),
        DataApiSettings(base_url=DATA_API_URL, api_key=SecretStr("")),
        DIGEST_SUBJECTS,
        DIGEST_WINDOW_DAYS,
        # Without export: no measure nor span leaves while the OpenAPI is written.
        ModelMetrics.from_settings(None, resource),
        TraceExport.from_settings(None, resource),
    ).openapi()
