"""The settings of the model, read from a dict given as the environment: never from .env."""

import json

import pytest

from k_b.ai_api.connectors.llm_settings import (
    LlmSettings,
    read_llm_settings_or_refusal,
)
from k_b.core.config import LLM_REPLAY_BASE_URL

# Made for the test: never a real key.
TEST_API_KEY = "test-key-0123456789"
PROVIDER_ENVIRONMENT = {
    "KB_LLM_PROVIDER": "openai_compatible",
    "KB_LLM_BASE_URL": "https://models.example/openai/v1",
    "KB_LLM_MODEL": "gpt-5.6-luna",
    "KB_LLM_API_KEY": TEST_API_KEY,
}
REPLAY_STEPS = [{"kind": "completion", "content": "{}"}, {"kind": "timeout"}]


@pytest.fixture
def replay_environment(tmp_path):
    replay_file = tmp_path / "replay.json"
    replay_file.write_text(json.dumps(REPLAY_STEPS))
    return {"KB_LLM_PROVIDER": "replay", "KB_LLM_REPLAY_FILE": str(replay_file)}


def test_the_settings_of_a_provider_are_read():
    settings = read_llm_settings_or_refusal(PROVIDER_ENVIRONMENT)

    assert isinstance(settings, LlmSettings)
    assert settings.provider == "openai_compatible"
    assert settings.base_url == "https://models.example/openai/v1"
    assert settings.model == "gpt-5.6-luna"
    assert settings.api_key is not None
    assert settings.api_key.get_secret_value() == TEST_API_KEY
    assert settings.reasoning_effort is None


def test_the_settings_of_the_replay_need_neither_url_nor_key(replay_environment):
    settings = read_llm_settings_or_refusal(replay_environment)

    assert isinstance(settings, LlmSettings)
    assert settings.provider == "replay"
    assert settings.base_url == LLM_REPLAY_BASE_URL
    assert settings.api_key is None
    assert settings.replay_steps is not None
    assert [step.kind for step in settings.replay_steps] == ["completion", "timeout"]


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (None, "unreadable, FileNotFoundError"),
        ('[{"kind": "sleep"}]', "step 1: union_tag_invalid; errors in the file: 1"),
    ],
    ids=["missing", "out-of-the-model"],
)
def test_a_replay_file_missing_or_out_of_the_model_stops_the_start(
    tmp_path, content, message
):
    replay_file = tmp_path / "replay.json"
    if content is not None:
        replay_file.write_text(content)
    environment = {"KB_LLM_PROVIDER": "replay", "KB_LLM_REPLAY_FILE": str(replay_file)}

    refusal = read_llm_settings_or_refusal(environment)

    assert isinstance(refusal, str)
    assert refusal.startswith("KB_LLM_REPLAY_FILE: ")
    assert refusal.endswith(message)


def test_an_empty_reasoning_effort_is_not_set():
    settings = read_llm_settings_or_refusal(
        PROVIDER_ENVIRONMENT | {"KB_LLM_REASONING_EFFORT": ""}
    )

    assert isinstance(settings, LlmSettings)
    assert settings.reasoning_effort is None


def test_reasoning_effort_none_is_read():
    settings = read_llm_settings_or_refusal(
        PROVIDER_ENVIRONMENT | {"KB_LLM_REASONING_EFFORT": "none"}
    )

    assert isinstance(settings, LlmSettings)
    assert settings.reasoning_effort == "none"


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        ({}, "KB_LLM_PROVIDER: not set, openai_compatible or replay"),
        (
            {"KB_LLM_PROVIDER": "openai"},
            "KB_LLM_PROVIDER: 'openai' not openai_compatible nor replay",
        ),
        (
            {"KB_LLM_PROVIDER": "replay"},
            "KB_LLM_REPLAY_FILE: not set, required by KB_LLM_PROVIDER=replay",
        ),
        (
            PROVIDER_ENVIRONMENT | {"KB_LLM_BASE_URL": ""},
            "KB_LLM_BASE_URL: not set, required by KB_LLM_PROVIDER=openai_compatible",
        ),
        (
            {k: v for k, v in PROVIDER_ENVIRONMENT.items() if k != "KB_LLM_MODEL"},
            "KB_LLM_MODEL: not set, required by KB_LLM_PROVIDER=openai_compatible",
        ),
        (
            {k: v for k, v in PROVIDER_ENVIRONMENT.items() if k != "KB_LLM_API_KEY"},
            "KB_LLM_API_KEY: not set, required by KB_LLM_PROVIDER=openai_compatible",
        ),
        (
            PROVIDER_ENVIRONMENT | {"KB_LLM_BASE_URL": "http://models.example/v1"},
            "KB_LLM_BASE_URL: scheme not https",
        ),
        (
            PROVIDER_ENVIRONMENT | {"KB_LLM_BASE_URL": "https:///v1"},
            "KB_LLM_BASE_URL: no host",
        ),
        (
            PROVIDER_ENVIRONMENT | {"KB_LLM_BASE_URL": "https://[::1/v1"},
            "KB_LLM_BASE_URL: not a URL",
        ),
        (
            PROVIDER_ENVIRONMENT
            | {"KB_LLM_BASE_URL": "https://res.openai.azure.com:99999/openai/v1"},
            "KB_LLM_BASE_URL: not a URL",
        ),
        (
            PROVIDER_ENVIRONMENT | {"KB_LLM_BASE_URL": "https://models.example:abc/v1"},
            "KB_LLM_BASE_URL: not a URL",
        ),
        (
            PROVIDER_ENVIRONMENT | {"KB_LLM_BASE_URL": "https://models_example!/v1"},
            "KB_LLM_BASE_URL: host not a lowercase host name",
        ),
        (
            PROVIDER_ENVIRONMENT | {"KB_LLM_MODEL": "gpt 5\n"},
            r"KB_LLM_MODEL: 'gpt 5\n' not [A-Za-z0-9][A-Za-z0-9._:-]{0,99}",
        ),
        (
            PROVIDER_ENVIRONMENT | {"KB_LLM_REASONING_EFFORT": "low"},
            "KB_LLM_REASONING_EFFORT: 'low' not none",
        ),
    ],
    ids=[
        "no-provider",
        "unknown-provider",
        "replay-without-file",
        "empty-url",
        "no-model",
        "no-key",
        "http",
        "no-host",
        "not-a-url",
        "port-out-of-range",
        "port-not-digits",
        "host-out-of-pattern",
        "model-out-of-pattern",
        "reasoning-effort-other-than-none",
    ],
)
def test_a_setting_refused_gives_its_reason(environment, message):
    assert read_llm_settings_or_refusal(environment) == message


@pytest.mark.parametrize(
    ("base_url", "message"),
    [
        (
            f"https://user:{TEST_API_KEY}@models.example/v1",
            "KB_LLM_BASE_URL: with user information",
        ),
        (
            f"https://models.example/v1?api-key={TEST_API_KEY}",
            "KB_LLM_BASE_URL: with a query or a fragment",
        ),
        (
            f"https://models.example/v1#{TEST_API_KEY}",
            "KB_LLM_BASE_URL: with a query or a fragment",
        ),
    ],
    ids=["user-information", "query", "fragment"],
)
def test_a_url_refused_is_never_quoted_since_it_may_carry_a_key(base_url, message):
    environment = PROVIDER_ENVIRONMENT | {"KB_LLM_BASE_URL": base_url}

    refusal = read_llm_settings_or_refusal(environment)

    assert refusal == message
    assert TEST_API_KEY not in refusal


@pytest.mark.parametrize(
    "api_key",
    [" key", "key with space", "clé", "key\n"],
    ids=["space-before", "space", "non-ascii", "line-break"],
)
def test_a_key_refused_is_never_quoted(api_key):
    environment = PROVIDER_ENVIRONMENT | {"KB_LLM_API_KEY": api_key}

    refusal = read_llm_settings_or_refusal(environment)

    assert refusal == "KB_LLM_API_KEY: a character outside printable ASCII, or a space"
    assert api_key.strip() not in refusal


def test_the_key_is_not_in_the_representation_of_the_settings():
    settings = read_llm_settings_or_refusal(PROVIDER_ENVIRONMENT)

    assert TEST_API_KEY not in repr(settings)
