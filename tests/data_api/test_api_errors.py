"""The errors of the data API: a named state, no stack, no SQL, no path, no value received."""

import logging

import pytest

from k_b.data_api.routers import item_router


def test_a_host_other_than_localhost_gives_400(client, authorization):
    response = client.get("/items", headers={**authorization, "Host": "evil.example"})

    assert response.status_code == 400


def test_an_unknown_route_gives_404_route_not_found(client, authorization):
    response = client.get("/secrets", headers=authorization)

    assert response.status_code == 404
    assert response.json() == {"error": "route_not_found"}


def test_a_final_slash_gives_404_not_a_redirection(client, authorization):
    response = client.get("/items/", headers=authorization, follow_redirects=False)

    assert response.status_code == 404
    assert response.json() == {"error": "route_not_found"}


def test_a_missing_database_gives_503_without_its_path(
    make_client, tmp_path, api_keys, authorization
):
    client = make_client(tmp_path / "k-b.sqlite", api_keys)

    response = client.get("/items", headers=authorization)

    assert response.status_code == 503
    assert response.json() == {"error": "database_missing"}


def test_a_database_error_gives_500_without_detail_and_logs_its_type(
    make_client, tmp_path, api_keys, authorization, caplog
):
    database_path = tmp_path / "k-b.sqlite"
    database_path.write_text("not an SQLite base")
    client = make_client(database_path, api_keys)

    with caplog.at_level(logging.ERROR, logger="k_b.data_api"):
        response = client.get("/items", headers=authorization)

    assert response.status_code == 500
    assert response.json() == {"error": "database_error"}
    request_id = response.headers["X-Request-ID"]
    assert f"database_error: DatabaseError request_id={request_id}" in caplog.text


@pytest.fixture
def error_responses(make_client, client, tmp_path, api_keys, authorization):
    """A response of each error status a data route gives."""
    missing = make_client(tmp_path / "missing" / "k-b.sqlite", api_keys)
    broken_path = tmp_path / "k-b.sqlite"
    broken_path.write_text("not an SQLite base")
    broken = make_client(broken_path, api_keys)
    return [
        client.get("/items"),
        client.get("/items?limit=101", headers=authorization),
        missing.get("/items", headers=authorization),
        broken.get("/items", headers=authorization),
    ]


def test_no_error_body_holds_a_raw_file_path_or_a_path(error_responses, tmp_path):
    assert [response.status_code for response in error_responses] == [
        401,
        422,
        503,
        500,
    ]
    for response in error_responses:
        for absent in (
            "raw_file_path",
            "selected_library/",
            str(tmp_path),
            "k-b.sqlite",
        ):
            assert absent not in response.text


def test_an_unexpected_error_gives_500_internal_error_and_logs_its_type_only(
    client, authorization, monkeypatch, caplog
):
    def fail(*_, **__):
        raise RuntimeError("value received by the test")

    monkeypatch.setattr(item_router, "read_items", fail)

    with caplog.at_level(logging.INFO, logger="k_b"):
        response = client.get("/items", headers=authorization)

    assert response.status_code == 500
    assert response.json() == {"error": "internal_error"}
    request_id = response.headers["X-Request-ID"]
    assert f"internal_error: RuntimeError request_id={request_id}" in caplog.text
    assert "status=500" in caplog.text
    assert "value received by the test" not in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.parametrize("path", ["/items", "/items/1", "/trends"])
def test_no_route_answers_cors(client, authorization, path):
    """No CORS: a page of another origin cannot read a response in a browser."""
    origin = {"Origin": "https://evil.example"}

    response = client.get(path, headers={**authorization, **origin})
    preflight = client.options(
        path, headers={**origin, "Access-Control-Request-Method": "GET"}
    )

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-origin" not in preflight.headers


def test_a_body_the_data_api_never_reads_gives_no_400_nor_422(client, authorization):
    """A data route reads no body: the 400 of an unreadable body, answered 422, does not reach
    the data API."""
    response = client.request(
        "GET",
        "/items",
        content=b"\xff",
        headers={**authorization, "Content-Type": "application/json"},
    )

    assert response.status_code == 200
