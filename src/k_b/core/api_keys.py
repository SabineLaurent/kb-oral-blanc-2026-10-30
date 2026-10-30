"""The key of each client of an API, one variable <prefix><CLIENT> per client (docs/specs-api.md,
"Accès par clé")."""

import re
from collections.abc import Mapping

from pydantic import SecretStr

from k_b.core.config import API_KEY_CHARACTERS, MIN_API_KEY_LENGTH
from k_b.core.quoting import quote_value

API_KEY_VARIABLE_PREFIX = "KB_DATA_API_KEY_"
AI_API_KEY_VARIABLE_PREFIX = "KB_AI_API_KEY_"
CLIENT_NAME = re.compile(r"[A-Z0-9_]+")


def read_api_keys_or_refusal(
    environ: Mapping[str, str], prefix: str
) -> dict[str, SecretStr] | str:
    """The key of each client by its name, or why a variable is refused: its name, never its
    value."""
    variables = sorted(name for name in environ if name.startswith(prefix))
    if not variables:
        return f"no API key: set {prefix}<CLIENT>"
    api_keys: dict[str, SecretStr] = {}
    variable_by_key: dict[str, str] = {}
    for variable in variables:
        key = environ[variable]
        refusal = find_api_key_refusal(prefix, variable, key, variable_by_key)
        if refusal is not None:
            return refusal
        variable_by_key[key] = variable
        api_keys[variable.removeprefix(prefix)] = SecretStr(key)
    return api_keys


def find_api_key_refusal(
    prefix: str, variable: str, key: str, variable_by_key: Mapping[str, str]
) -> str | None:
    """Why the variable of a client is refused, or None, naming the variable, never the key;
    variable_by_key holds the keys already read."""
    client_name = variable.removeprefix(prefix)
    if not CLIENT_NAME.fullmatch(client_name):
        return f"{quote_value(variable)}: client name empty or not [A-Z0-9_]+"
    if len(key) < MIN_API_KEY_LENGTH:
        return (
            f"{quote_value(variable)}: key of {len(key)} characters,"
            f" minimum {MIN_API_KEY_LENGTH}"
        )
    if not API_KEY_CHARACTERS.fullmatch(key):
        return f"{quote_value(variable)}: key with a character outside [A-Za-z0-9_-]"
    if key in variable_by_key:
        return (
            f"{quote_value(variable_by_key[key])} and {quote_value(variable)}: same key"
        )
    return None


def find_shared_key_refusal(
    prefix: str,
    api_keys: Mapping[str, SecretStr],
    variable: str,
    key: SecretStr,
) -> str | None:
    """Why a key is refused, or None: the same key as the one of variable, which another API
    reads. Both variables are named, never the key."""
    for client_name, client_key in api_keys.items():
        if client_key.get_secret_value() == key.get_secret_value():
            return f"{quote_value(prefix + client_name)} and {quote_value(variable)}: same key"
    return None
