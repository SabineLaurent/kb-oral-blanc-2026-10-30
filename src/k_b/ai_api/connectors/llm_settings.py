"""The settings of the model, read from the environment: the provider, its URL, its model, its
key.

No default for the provider, its URL nor its model: a default would send the watch to a service
nobody chose, or make up digests (fail closed)."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import SecretStr

from k_b.ai_api.connectors.llm_replay import (
    InvalidReplayFileError,
    ReplayStep,
    read_replay_steps,
)
from k_b.core.config import LLM_REASONING_EFFORT, LLM_REPLAY_BASE_URL, MODEL_NAME
from k_b.core.outbound import find_url_refusal
from k_b.core.quoting import quote_value

PROVIDER_VARIABLE = "KB_LLM_PROVIDER"
BASE_URL_VARIABLE = "KB_LLM_BASE_URL"
MODEL_VARIABLE = "KB_LLM_MODEL"
API_KEY_VARIABLE = "KB_LLM_API_KEY"
REASONING_EFFORT_VARIABLE = "KB_LLM_REASONING_EFFORT"
REPLAY_FILE_VARIABLE = "KB_LLM_REPLAY_FILE"

type LlmProvider = Literal["openai_compatible", "replay"]
PROVIDERS: tuple[LlmProvider, ...] = ("openai_compatible", "replay")
# The model of the replay, in the metrics: no deployment answers.
REPLAY_MODEL = "replay"
# Printable ASCII without space: the key goes into an HTTP header as it is.
API_KEY_CHARACTERS = re.compile(r"[!-~]+")


@dataclass(frozen=True)
class LlmSettings:
    provider: LlmProvider
    base_url: str
    model: str
    # None for the replay, which sends no key.
    api_key: SecretStr | None
    reasoning_effort: Literal["none"] | None
    # The steps of the replay, read with the settings: a file missing or out of the model stops
    # the start, not the first call. None for a provider.
    replay_steps: tuple[ReplayStep, ...] | None


def read_llm_settings_or_refusal(environ: Mapping[str, str]) -> LlmSettings | str:
    """The settings of the model, or why a variable is refused: never the URL nor the key."""
    provider = environ.get(PROVIDER_VARIABLE)
    if not provider:
        return f"{PROVIDER_VARIABLE}: not set, {' or '.join(PROVIDERS)}"
    # Empty is not set, as for every variable here.
    reasoning_effort = environ.get(REASONING_EFFORT_VARIABLE) or None
    if reasoning_effort is not None and reasoning_effort != "none":
        return f"{REASONING_EFFORT_VARIABLE}: {quote_value(reasoning_effort)} not none"
    effort: Literal["none"] | None = (
        "none" if reasoning_effort else LLM_REASONING_EFFORT
    )
    if provider == "replay":
        return read_replay_settings(environ, effort)
    if provider == "openai_compatible":
        return read_provider_settings(environ, effort)
    return f"{PROVIDER_VARIABLE}: {quote_value(provider)} not {' nor '.join(PROVIDERS)}"


def read_replay_settings(
    environ: Mapping[str, str], reasoning_effort: Literal["none"] | None
) -> LlmSettings | str:
    replay_file = environ.get(REPLAY_FILE_VARIABLE)
    if not replay_file:
        return (
            f"{REPLAY_FILE_VARIABLE}: not set, required by {PROVIDER_VARIABLE}=replay"
        )
    # The path comes from the environment: quoted.
    where = f"{REPLAY_FILE_VARIABLE}: {quote_value(replay_file)}"
    try:
        replay_steps = read_replay_steps(Path(replay_file))
    except OSError as error:
        return f"{where} unreadable, {type(error).__name__}"
    except InvalidReplayFileError as error:
        return f"{where} {error}"
    return LlmSettings(
        provider="replay",
        base_url=LLM_REPLAY_BASE_URL,
        model=REPLAY_MODEL,
        api_key=None,
        reasoning_effort=reasoning_effort,
        replay_steps=tuple(replay_steps),
    )


def read_provider_settings(
    environ: Mapping[str, str], reasoning_effort: Literal["none"] | None
) -> LlmSettings | str:
    for variable in (BASE_URL_VARIABLE, MODEL_VARIABLE, API_KEY_VARIABLE):
        if not environ.get(variable):
            return f"{variable}: not set, required by {PROVIDER_VARIABLE}=openai_compatible"
    base_url = environ[BASE_URL_VARIABLE]
    model = environ[MODEL_VARIABLE]
    api_key = environ[API_KEY_VARIABLE]
    refusal = (
        find_url_refusal(BASE_URL_VARIABLE, base_url, ("https",))
        or find_model_refusal(model)
        or find_api_key_refusal(api_key)
    )
    if refusal is not None:
        return refusal
    return LlmSettings(
        provider="openai_compatible",
        base_url=base_url,
        model=model,
        api_key=SecretStr(api_key),
        reasoning_effort=reasoning_effort,
        replay_steps=None,
    )


def find_model_refusal(model: str) -> str | None:
    if MODEL_NAME.fullmatch(model):
        return None
    return f"{MODEL_VARIABLE}: {quote_value(model)} not {MODEL_NAME.pattern}"


def find_api_key_refusal(api_key: str) -> str | None:
    """Why the key is refused, or None; the key is never quoted."""
    if API_KEY_CHARACTERS.fullmatch(api_key):
        return None
    return f"{API_KEY_VARIABLE}: a character outside printable ASCII, or a space"
