"""The key of each client (docs/specs-api.md, "Accès par clé")."""

import secrets

import pytest
from fastapi.routing import iter_route_contexts

from k_b.data_api.app import create_app

INVALID_API_KEY = {"error": "invalid_api_key"}
# Served without key: they hold no data.
PUBLIC_ROUTES = {"/docs", "/openapi.json", "/health"}


def list_routes(items_database, api_keys):
    """Every route of the app, read by the walk FastAPI makes for its OpenAPI, so that a route
    added later is tested as well."""
    app = create_app(items_database, api_keys)
    return [route.path for route in iter_route_contexts(app.routes)]


def list_data_routes(items_database, api_keys):
    return [
        path
        for path in list_routes(items_database, api_keys)
        if path not in PUBLIC_ROUTES
    ]


def test_the_routes_are_listed(items_database, api_keys):
    assert sorted(list_routes(items_database, api_keys)) == [
        "/docs",
        "/health",
        "/items",
        "/items/{item_id}",
        "/openapi.json",
        "/trends",
    ]


def test_only_the_public_routes_answer_without_a_key(client, items_database, api_keys):
    answered_without_key = {
        path
        for path in list_routes(items_database, api_keys)
        if client.get(path).status_code != 401
    }

    assert answered_without_key == PUBLIC_ROUTES


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong-key-of-the-test"},
        {"Authorization": "Basic dGVzdDp0ZXN0"},
        {"Authorization": "Bearer"},
    ],
)
def test_every_data_route_refuses_a_missing_and_a_wrong_key_with_the_same_body(
    client, items_database, api_keys, headers
):
    for path in list_data_routes(items_database, api_keys):
        response = client.get(path, headers=headers)

        assert response.status_code == 401
        assert response.json() == INVALID_API_KEY
        assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("client_name", ["TERMINAL", "AGENT"])
def test_each_client_is_served_with_its_key(client, api_keys, client_name):
    key = api_keys[client_name].get_secret_value()

    response = client.get("/items", headers={"Authorization": f"Bearer {key}"})

    assert response.status_code == 200


def test_a_non_ascii_key_gives_401_not_500(client):
    # httpx sends a header given as bytes as it is: é in Latin-1, as a client could.
    response = client.get(
        "/items", headers={"Authorization": "Bearer clé".encode("latin-1")}
    )

    assert response.status_code == 401
    assert response.json() == INVALID_API_KEY


def test_a_removed_key_gives_401_and_the_other_clients_are_still_served(
    make_client, items_database, api_keys
):
    agent_key = api_keys["AGENT"].get_secret_value()
    terminal_key = api_keys["TERMINAL"].get_secret_value()
    # The app restarted without the variable of the agent.
    client = make_client(items_database, {"TERMINAL": api_keys["TERMINAL"]})

    agent_response = client.get(
        "/items", headers={"Authorization": f"Bearer {agent_key}"}
    )
    terminal_response = client.get(
        "/items", headers={"Authorization": f"Bearer {terminal_key}"}
    )

    assert agent_response.status_code == 401
    assert terminal_response.status_code == 200


def test_an_app_without_key_refuses_every_data_route(make_client, items_database):
    client = make_client(items_database, {})

    response = client.get(
        "/items", headers={"Authorization": f"Bearer {secrets.token_urlsafe(32)}"}
    )

    assert response.status_code == 401


def test_without_a_key_a_wrong_parameter_gives_401(client):
    response = client.get("/items?limit=101")

    assert response.status_code == 401
    assert response.json() == INVALID_API_KEY


def test_with_a_key_a_wrong_parameter_and_no_database_gives_503(
    make_client, tmp_path, api_keys, authorization
):
    """The connection is a dependency: it is opened before the parameters are checked."""
    client = make_client(tmp_path / "k-b.sqlite", api_keys)

    response = client.get("/items?limit=101", headers=authorization)

    assert response.status_code == 503


def test_docs_openapi_and_health_are_public_and_no_other_page_is_served(client):
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/health").status_code == 200
    assert client.get("/redoc").status_code == 404
    assert client.get("/docs/oauth2-redirect").status_code == 404
