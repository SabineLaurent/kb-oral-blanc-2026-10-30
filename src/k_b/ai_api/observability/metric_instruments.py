"""The measures of the AI API as OpenTelemetry instruments, the same as the JSON line of each
call.

Names after the semantic conventions for generative AI, k_b. otherwise; closed attributes only,
never a text, a title, a subject nor a request id."""

import logging
from collections.abc import Sequence

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource

from k_b.ai_api.connectors.llm_settings import LlmSettings
from k_b.ai_api.connectors.metrics_export import (
    MetricsExportSettings,
    build_meter_provider,
)
from k_b.ai_api.observability.metrics import estimate_cost
from k_b.ai_api.stage_calls import StageCall
from k_b.core.config import METRICS_STOP_TIMEOUT_SECONDS, MILLISECONDS_PER_SECOND

logger = logging.getLogger(__name__)

METER_NAME = "k_b.ai_api"
# The operation of the conventions: K-B calls chat/completions only.
OPERATION_NAME = "chat"
TEXT_MODALITY = "text"
OK_OUTCOME = "ok"
# The buckets of gen_ai.client.inference.duration in the conventions, in seconds; the reading of the
# data API, an HTTP call too, takes them.
CALL_SECONDS_BUCKETS = (
    0.01, 0.02, 0.04, 0.08, 0.16, 0.32, 0.64, 1.28, 2.56, 5.12, 10.24, 20.48, 40.96, 81.92,
)  # fmt: skip
# The checks of an output run in memory: a millisecond to a few seconds.
CHECK_SECONDS_BUCKETS = (
    0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5,
)  # fmt: skip


class ModelMetrics:
    """The instruments of the AI API, made once with the app."""

    def __init__(self, meter_provider: MeterProvider) -> None:
        self._meter_provider = meter_provider
        meter = meter_provider.get_meter(METER_NAME)
        self._inference_duration = meter.create_histogram(
            "gen_ai.client.inference.duration",
            unit="s",
            description="Duration of a call to the model, both attempts and the wait between.",
            explicit_bucket_boundaries_advisory=CALL_SECONDS_BUCKETS,
        )
        self._input_tokens = meter.create_counter(
            "gen_ai.client.inference.usage.input_tokens",
            unit="{token}",
            description="Input tokens of the calls, as the provider counts them.",
        )
        self._output_tokens = meter.create_counter(
            "gen_ai.client.inference.usage.output_tokens",
            unit="{token}",
            description="Output tokens of the calls, reasoning tokens included.",
        )
        self._calls = meter.create_counter(
            "k_b.llm.calls",
            unit="{call}",
            description="Calls to the model, by outcome and by schema check.",
        )
        self._check_duration = meter.create_histogram(
            "k_b.llm.check.duration",
            unit="s",
            description="Duration of the checks of the outputs of a call.",
            explicit_bucket_boundaries_advisory=CHECK_SECONDS_BUCKETS,
        )
        self._cost = meter.create_counter(
            "k_b.llm.cost",
            unit="{USD}",
            description="Estimated cost of the calls, at the prices of core/config.py.",
        )
        self._claims_removed = meter.create_counter(
            "k_b.digest.claims_removed",
            unit="{claim}",
            description="Claims removed from the digests by the checks, bounds or fidelity.",
        )
        self._items_removed_by_checks = meter.create_counter(
            "k_b.digest.items_removed_by_checks",
            unit="{item}",
            description="Items no longer cited once claims are removed, by bounds or fidelity.",
        )
        self._items_with_subject = meter.create_counter(
            "k_b.digest.items_with_subject",
            unit="{item}",
            description="Items of the classification given a subject of the list.",
        )
        self._items_no_suitable_subject = meter.create_counter(
            "k_b.digest.items_no_suitable_subject",
            unit="{item}",
            description="Items of the classification that no subject suits.",
        )
        self._items_unanswered = meter.create_counter(
            "k_b.digest.items_unanswered",
            unit="{item}",
            description="Items the classification left without answer.",
        )
        self._facts_checked = meter.create_counter(
            "k_b.digest.facts_checked",
            unit="{fact}",
            description="Facts of the claims checked by the fidelity, a claim removed included.",
        )
        self._facts_found = meter.create_counter(
            "k_b.digest.facts_found",
            unit="{fact}",
            description="Facts found in their claim and in the item they cite.",
        )
        self._data_fetch_duration = meter.create_histogram(
            "k_b.digest.data_fetch.duration",
            unit="s",
            description="Duration of the reading of the window by the data API.",
            explicit_bucket_boundaries_advisory=CALL_SECONDS_BUCKETS,
        )

    @classmethod
    def from_settings(
        cls, settings: MetricsExportSettings | None, resource: Resource
    ) -> "ModelMetrics":
        """The instruments of kb serve-ai: exported if the settings are given, dropped if not."""
        return cls(build_meter_provider(settings, resource))

    def record_calls(
        self, llm_settings: LlmSettings, stage_calls: Sequence[StageCall]
    ) -> None:
        """The measures of each call of a request, those of its JSON line."""
        for stage_call in stage_calls:
            call = stage_call.llm_call
            attributes: dict[str, str | bool] = {
                "gen_ai.operation.name": OPERATION_NAME,
                # What K-B knows: openai_compatible or replay, not the company behind the URL.
                "gen_ai.provider.name": llm_settings.provider,
                "gen_ai.request.model": llm_settings.model,
                "k_b.stage": stage_call.stage,
            }
            self._inference_duration.record(
                call.llm_ms / MILLISECONDS_PER_SECOND,
                attributes
                if call.outcome == OK_OUTCOME
                else attributes | {"error.type": call.outcome},
            )
            token_attributes = attributes | {"gen_ai.token.modality": TEXT_MODALITY}
            self._input_tokens.add(call.tokens_in, token_attributes)
            self._output_tokens.add(call.tokens_out, token_attributes)
            self._calls.add(
                1,
                attributes
                | {"k_b.outcome": call.outcome, "k_b.schema_valid": call.schema_valid},
            )
            # A call none of whose outputs was checked (a timeout, a 5xx, an unreadable answer)
            # has a check_ms of 0 that measures nothing: it would pull the durations down.
            if call.is_checked:
                self._check_duration.record(
                    call.check_ms / MILLISECONDS_PER_SECOND, attributes
                )
            self._cost.add(estimate_cost(call.tokens_in, call.tokens_out), attributes)
            if stage_call.claims_removed is not None:
                self._claims_removed.add(stage_call.claims_removed, attributes)
            if stage_call.items_removed_by_checks is not None:
                self._items_removed_by_checks.add(
                    stage_call.items_removed_by_checks, attributes
                )
            if stage_call.items_with_subject is not None:
                self._items_with_subject.add(stage_call.items_with_subject, attributes)
            if stage_call.items_no_suitable_subject is not None:
                self._items_no_suitable_subject.add(
                    stage_call.items_no_suitable_subject, attributes
                )
            if stage_call.items_unanswered is not None:
                self._items_unanswered.add(stage_call.items_unanswered, attributes)
            if stage_call.facts_checked is not None:
                self._facts_checked.add(stage_call.facts_checked, attributes)
            if stage_call.facts_found is not None:
                self._facts_found.add(stage_call.facts_found, attributes)

    def record_data_fetch(self, data_fetch_ms: int) -> None:
        """The reading of the window of a request, a model called or not."""
        self._data_fetch_duration.record(data_fetch_ms / MILLISECONDS_PER_SECOND)

    def stop(self) -> None:
        """Send what is not sent yet, within METRICS_STOP_TIMEOUT_SECONDS; a failure is logged by
        its type only, as the SDK builds its message from the errors of its readers."""
        try:
            self._meter_provider.shutdown(
                timeout_millis=METRICS_STOP_TIMEOUT_SECONDS * MILLISECONDS_PER_SECOND
            )
        except Exception as error:  # noqa: BLE001 — the SDK raises a bare Exception
            logger.error("metrics: stop failed, %s", type(error).__name__)
