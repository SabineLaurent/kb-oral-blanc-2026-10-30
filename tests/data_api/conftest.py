"""The data API on the test base of the readers (tests/conftest.py), with two clients."""

import secrets

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from k_b.data_api.app import create_app


@pytest.fixture
def api_keys():
    """The key of each client, made for the test as kb serve-data would read them, never from .env."""
    return {
        "TERMINAL": SecretStr(secrets.token_urlsafe(32)),
        "AGENT": SecretStr(secrets.token_urlsafe(32)),
    }


@pytest.fixture
def make_client():
    """A client of a new app, on localhost, as TrustedHostMiddleware refuses the testserver host
    of TestClient."""

    def make(database_path, api_keys):
        return TestClient(
            create_app(database_path, api_keys),
            base_url="http://localhost",
            raise_server_exceptions=False,
        )

    return make


@pytest.fixture
def client(make_client, items_database, api_keys):
    return make_client(items_database, api_keys)


@pytest.fixture
def authorization(api_keys):
    return {"Authorization": f"Bearer {api_keys['TERMINAL'].get_secret_value()}"}
