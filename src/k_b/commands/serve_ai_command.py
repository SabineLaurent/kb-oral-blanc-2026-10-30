"""kb serve-ai: serve the digests of the watch by the AI API, beside kb serve-data."""

import logging
from collections.abc import Mapping

import uvicorn

from k_b.ai_api.app import create_app
from k_b.ai_api.connectors.data_api_settings import (
    DATA_API_KEY_VARIABLE,
    read_data_api_settings_or_refusal,
)
from k_b.ai_api.connectors.llm_settings import read_llm_settings_or_refusal
from k_b.ai_api.connectors.metrics_export import (
    build_resource,
    read_metrics_export_settings_or_refusal,
)
from k_b.ai_api.connectors.trace_export import TraceExport
from k_b.ai_api.observability.metric_instruments import ModelMetrics
from k_b.ai_api.settings import read_ai_api_settings_or_refusal
from k_b.commands.exit_codes import ExitCode
from k_b.commands.run_log import LOGGER_NAME, value_or_log_refusal
from k_b.core.api_keys import (
    AI_API_KEY_VARIABLE_PREFIX,
    find_shared_key_refusal,
    read_api_keys_or_refusal,
)
from k_b.core.config import DIGEST_SUBJECTS

logger = logging.getLogger(LOGGER_NAME)


def run_serve_ai(environ: Mapping[str, str]) -> int:
    """Serve the AI API with the keys and the settings of the environment: any refused, nothing
    is served."""
    api_keys = value_or_log_refusal(
        read_api_keys_or_refusal(environ, AI_API_KEY_VARIABLE_PREFIX)
    )
    settings = value_or_log_refusal(read_ai_api_settings_or_refusal(environ))
    llm_settings = value_or_log_refusal(read_llm_settings_or_refusal(environ))
    data_api_settings = value_or_log_refusal(read_data_api_settings_or_refusal(environ))
    # None is a setting here: the export is off.
    export_settings = read_metrics_export_settings_or_refusal(environ)
    if isinstance(export_settings, str):
        logger.error("%s", export_settings)
    if (
        api_keys is None
        or settings is None
        or llm_settings is None
        or data_api_settings is None
        or isinstance(export_settings, str)
    ):
        return ExitCode.NOT_STARTED
    # The data API gives each client every item: a client of the AI API that held the key of
    # AI_DIGEST would read it too (least privilege, code.md). Compared with KB_DATA_API_KEY_AI_DIGEST
    # only, not with the other keys of the data API: on the Mac, kb digest reads the whole .env
    # anyway, and every key of the data API has the same rights; in containers, each service
    # gets its own keys (decision of Sabine of 2026-10-21, second review of #156).
    shared_key_refusal = find_shared_key_refusal(
        AI_API_KEY_VARIABLE_PREFIX,
        api_keys,
        DATA_API_KEY_VARIABLE,
        data_api_settings.api_key,
    )
    if shared_key_refusal is not None:
        logger.error("%s", shared_key_refusal)
        return ExitCode.NOT_STARTED
    logger.info("AI API clients loaded: %s", ", ".join(sorted(api_keys)))
    # The provider and the model, never the URL nor the key: an AI API set on the replay by
    # mistake says it.
    logger.info("LLM provider %s, model %s", llm_settings.provider, llm_settings.model)
    logger.info("digest window by default: %s days", settings.window_days)
    # The environment and the content sent with the traces, never the endpoint nor the headers.
    logger.info(
        "metrics and traces export: %s",
        "off"
        if export_settings is None
        else f"OTLP, environment {export_settings.environment},"
        f" model content {export_settings.trace_content}",
    )
    # One resource for the three providers.
    resource = build_resource(
        None if export_settings is None else export_settings.environment
    )
    uvicorn.run(
        create_app(
            api_keys,
            llm_settings,
            data_api_settings,
            DIGEST_SUBJECTS,
            settings.window_days,
            # Both stopped by the lifespan of the app (ai_api/app.py).
            ModelMetrics.from_settings(export_settings, resource),
            TraceExport.from_settings(export_settings, resource),
        ),
        host=settings.host,
        port=settings.port,
        log_config=None,
        access_log=False,
    )
    return ExitCode.SUCCEEDED
