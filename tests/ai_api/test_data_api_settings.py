"""The settings of the data API, read from a dict given as the environment: never from .env."""

import secrets

import pytest

from k_b.ai_api.connectors.data_api_settings import (
    DATA_API_KEY_VARIABLE,
    DataApiSettings,
    read_data_api_settings_or_refusal,
)
from k_b.core.api_keys import API_KEY_VARIABLE_PREFIX, read_api_keys_or_refusal
from k_b.core.config import DATA_API_URL

# Made for the test: never a real key.
TEST_API_KEY = secrets.token_urlsafe(32)


def test_without_url_the_settings_take_the_local_default():
    settings = read_data_api_settings_or_refusal(
        {"KB_DATA_API_KEY_AI_DIGEST": TEST_API_KEY}
    )

    assert isinstance(settings, DataApiSettings)
    assert settings.base_url == DATA_API_URL == "http://127.0.0.1:8000"
    assert settings.api_key.get_secret_value() == TEST_API_KEY


def test_the_url_of_the_environment_is_read():
    settings = read_data_api_settings_or_refusal(
        {
            "KB_DATA_API_URL": "http://api-donnees:8000",
            "KB_DATA_API_KEY_AI_DIGEST": TEST_API_KEY,
        }
    )

    assert isinstance(settings, DataApiSettings)
    assert settings.base_url == "http://api-donnees:8000"


def test_an_empty_url_is_unset():
    settings = read_data_api_settings_or_refusal(
        {"KB_DATA_API_URL": "", "KB_DATA_API_KEY_AI_DIGEST": TEST_API_KEY}
    )

    assert isinstance(settings, DataApiSettings)
    assert settings.base_url == DATA_API_URL


def test_a_url_refused_is_named_by_its_variable_never_quoted():
    refusal = read_data_api_settings_or_refusal(
        {
            "KB_DATA_API_URL": f"http://user:{TEST_API_KEY}@api-donnees",
            "KB_DATA_API_KEY_AI_DIGEST": TEST_API_KEY,
        }
    )

    assert refusal == "KB_DATA_API_URL: with user information"


@pytest.mark.parametrize("key", [None, ""])
def test_without_key_the_settings_are_refused(key):
    environ = {} if key is None else {"KB_DATA_API_KEY_AI_DIGEST": key}

    assert (
        read_data_api_settings_or_refusal(environ)
        == "KB_DATA_API_KEY_AI_DIGEST: not set"
    )


@pytest.mark.parametrize(
    ("key", "refusal"),
    [
        (
            "short-key",
            "KB_DATA_API_KEY_AI_DIGEST: key of 9 characters, minimum 32",
        ),
        (
            TEST_API_KEY + " ",
            "KB_DATA_API_KEY_AI_DIGEST: key with a character outside [A-Za-z0-9_-]+",
        ),
    ],
)
def test_a_key_refused_is_never_quoted(key, refusal):
    read = read_data_api_settings_or_refusal({"KB_DATA_API_KEY_AI_DIGEST": key})

    assert read == refusal
    assert key.strip() not in read


def test_the_key_sent_is_the_one_kb_serve_reads_for_its_client_ai_digest():
    """One variable for both sides, formed from the prefix kb serve-data reads."""
    api_keys = read_api_keys_or_refusal(
        {DATA_API_KEY_VARIABLE: TEST_API_KEY}, API_KEY_VARIABLE_PREFIX
    )

    assert isinstance(api_keys, dict)
    assert api_keys["AI_DIGEST"].get_secret_value() == TEST_API_KEY
