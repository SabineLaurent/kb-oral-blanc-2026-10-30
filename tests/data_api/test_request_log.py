"""One log line per request: the client named, never its key, a parameter value or a raw path;
its id given back in X-Request-ID."""

import logging
import uuid

import pytest

from k_b.core.api_request_log import format_request_line

REQUEST_LOGGER = "k_b.core.api_request_log"


@pytest.fixture
def request_lines(caplog):
    caplog.set_level(logging.INFO, logger=REQUEST_LOGGER)

    def read():
        return [
            record.getMessage()
            for record in caplog.records
            if record.name == REQUEST_LOGGER
        ]

    return read


def test_a_request_line_names_the_client_never_the_key_a_value_or_the_path(
    client, api_keys, authorization, request_lines
):
    response = client.get("/items?q=langchain&limit=3", headers=authorization)

    [line] = request_lines()
    assert line.startswith("request method='GET' route=/items status=200 duration_ms=")
    assert line.endswith(
        f" client=TERMINAL request_id={response.headers['X-Request-ID']}"
    )
    for absent in (api_keys["TERMINAL"].get_secret_value(), "langchain", "limit", "?"):
        assert absent not in line


def test_a_401_is_logged_without_name_nor_received_value(client, request_lines):
    client.get("/items", headers={"Authorization": "Bearer key-received-by-the-api"})

    [line] = request_lines()
    assert "status=401" in line
    assert " client=- request_id=" in line
    assert "key-received-by-the-api" not in line


def test_a_400_of_the_host_is_logged(client, authorization, request_lines):
    client.get("/items", headers={**authorization, "Host": "evil.example"})

    [line] = request_lines()
    assert "status=400" in line
    assert "evil.example" not in line


def test_an_unknown_route_is_logged_without_its_path(
    client, authorization, request_lines
):
    client.get("/secret-path-of-the-test", headers=authorization)

    [line] = request_lines()
    assert "route=- status=404" in line
    assert "secret-path-of-the-test" not in line


@pytest.mark.parametrize("path", ["/docs", "/openapi.json"])
def test_a_public_route_is_logged_by_its_path(client, request_lines, path):
    client.get(path)

    [line] = request_lines()
    assert f"route={path} status=200" in line


def test_a_health_check_that_answers_200_is_not_logged(client, request_lines):
    """A probe every few seconds would bury the requests of the clients."""
    client.get("/health")

    assert request_lines() == []


@pytest.mark.parametrize(
    ("method", "headers", "status"),
    [("GET", {"Host": "evil.example"}, 400), ("POST", {}, 405)],
)
def test_a_health_check_that_fails_is_logged_by_its_path(
    client, request_lines, method, headers, status
):
    """A probe set wrong shows in the log."""
    client.request(method, "/health", headers=headers)

    [line] = request_lines()
    assert f"route=/health status={status}" in line


def test_a_request_without_response_is_logged_without_status():
    line = format_request_line("GET", "/items", None, 3, "-", "id-of-the-test")

    assert line == (
        "request method='GET' route=/items status=- duration_ms=3 client=-"
        " request_id=id-of-the-test"
    )


def test_the_method_is_quoted():
    line = format_request_line("GET\nfake line", "-", 400, 0, "-", "id-of-the-test")

    assert "\n" not in line
    assert line == (
        r"request method='GET\nfake line' route=- status=400 duration_ms=0 client=-"
        " request_id=id-of-the-test"
    )


@pytest.mark.parametrize(
    ("path", "with_key"),
    [("/items", True), ("/items", False), ("/health", False), ("/unknown", True)],
)
def test_each_response_gives_the_id_of_its_request_in_its_log_line(
    client, authorization, request_lines, path, with_key
):
    """The id ties a response, an error included, to the lines of its request."""
    response = client.get(path, headers=authorization if with_key else {})

    request_id = response.headers["X-Request-ID"]
    assert str(uuid.UUID(request_id)) == request_id
    assert all(line.endswith(f" request_id={request_id}") for line in request_lines())


def test_each_request_has_its_own_id(client, authorization):
    first = client.get("/items", headers=authorization)
    second = client.get("/items", headers=authorization)

    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]


def test_a_detail_is_logged_by_its_route_template_never_its_id(
    client, authorization, request_lines
):
    client.get("/items/14", headers=authorization)

    [line] = request_lines()
    assert " route=/items/{item_id} status=200 " in line
    assert "14" not in line.split(" duration_ms=")[0]
