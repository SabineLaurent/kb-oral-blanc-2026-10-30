"""The settings of the data API the AI API reads: its URL and the key of AI_DIGEST, read from
KB_DATA_API_KEY_AI_DIGEST, without default (a secret: fail closed)."""

from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import SecretStr

from k_b.core.api_keys import API_KEY_VARIABLE_PREFIX
from k_b.core.config import API_KEY_CHARACTERS, DATA_API_URL, MIN_API_KEY_LENGTH
from k_b.core.outbound import find_url_refusal

DATA_API_URL_VARIABLE = "KB_DATA_API_URL"
# The variable kb serve-data reads for its client AI_DIGEST: formed from its prefix, one value for both.
DATA_API_KEY_VARIABLE = f"{API_KEY_VARIABLE_PREFIX}AI_DIGEST"
# No TLS before Azure Container Apps (docs/specs-api.md, OWASP API8): http on the machine and in
# the compose network.
DATA_API_SCHEMES = ("http", "https")


@dataclass(frozen=True)
class DataApiSettings:
    base_url: str
    api_key: SecretStr


def read_data_api_settings_or_refusal(
    environ: Mapping[str, str],
) -> DataApiSettings | str:
    """The settings of the data API, or why a variable is refused: never the key."""
    # Empty is not set, as for the settings of the model.
    base_url = environ.get(DATA_API_URL_VARIABLE) or DATA_API_URL
    api_key = environ.get(DATA_API_KEY_VARIABLE)
    if not api_key:
        return f"{DATA_API_KEY_VARIABLE}: not set"
    refusal = find_url_refusal(
        DATA_API_URL_VARIABLE, base_url, DATA_API_SCHEMES
    ) or find_api_key_refusal(api_key)
    if refusal is not None:
        return refusal
    return DataApiSettings(base_url=base_url, api_key=SecretStr(api_key))


def find_api_key_refusal(api_key: str) -> str | None:
    """Why the key is refused, by the rule kb serve-data applies, or None; the key is never quoted."""
    if len(api_key) < MIN_API_KEY_LENGTH:
        return (
            f"{DATA_API_KEY_VARIABLE}: key of {len(api_key)} characters,"
            f" minimum {MIN_API_KEY_LENGTH}"
        )
    if not API_KEY_CHARACTERS.fullmatch(api_key):
        return (
            f"{DATA_API_KEY_VARIABLE}: key with a character outside"
            f" {API_KEY_CHARACTERS.pattern}"
        )
    return None
