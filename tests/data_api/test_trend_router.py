"""GET /trends on the test base: the window of 2026-10-10, the latest, and the older one of
2026-10-09."""

import pytest

from k_b.database.creation import create_database

LATEST_WINDOW = {
    "window_end": "2026-10-10",
    "trends": [
        {
            "repository_id": 1209966933,
            "repository_name": "morluto/rea",
            "stars_gained": 1087,
            "forks_gained": 215,
        },
        {
            "repository_id": 1398080271,
            "repository_name": "storytold/photocraft",
            "stars_gained": 449,
            "forks_gained": 64,
        },
        {
            "repository_id": 1322250666,
            "repository_name": "boykopovar/AnyPS5",
            "stars_gained": 318,
            "forks_gained": 17,
        },
        # No star gained: the most forks first.
        {
            "repository_id": 1035334306,
            "repository_name": "wwwwwwworld/solana-trading-bot-v3",
            "stars_gained": 0,
            "forks_gained": 70,
        },
        {
            "repository_id": 928981629,
            "repository_name": "rhtap-perf-test/nodejs-devfile-sample2",
            "stars_gained": 0,
            "forks_gained": 13,
        },
    ],
}


def test_trends_without_window_end_give_the_latest_window(client, authorization):
    response = client.get("/trends", headers=authorization)

    assert response.status_code == 200
    assert response.json() == LATEST_WINDOW


def test_trends_of_a_given_window(client, authorization):
    response = client.get("/trends?window_end=2026-10-09", headers=authorization)

    assert response.status_code == 200
    # The same stars and forks: by repository_id.
    assert response.json() == {
        "window_end": "2026-10-09",
        "trends": [
            {
                "repository_id": 1209966933,
                "repository_name": "morluto/rea",
                "stars_gained": 120,
                "forks_gained": 9,
            },
            {
                "repository_id": 1398080271,
                "repository_name": "storytold/photocraft",
                "stars_gained": 120,
                "forks_gained": 9,
            },
        ],
    }


def test_an_unknown_window_gives_404(client, authorization):
    response = client.get("/trends?window_end=2026-01-01", headers=authorization)

    assert response.status_code == 404
    assert response.json() == {"error": "trend_window_not_found"}


def test_a_base_without_trend_gives_404(make_client, tmp_path, api_keys, authorization):
    database_path = tmp_path / "k-b.sqlite"
    create_database(database_path)
    client = make_client(database_path, api_keys)

    response = client.get("/trends", headers=authorization)

    assert response.status_code == 404
    assert response.json() == {"error": "trend_window_not_found"}


@pytest.mark.parametrize(
    "window_end",
    [
        "",
        "2026-10-1",
        "20261012",
        # A number is not read as a Unix time.
        "0",
        "1760227200",
        "2026-10-12T00:00:00Z",
        "2026-02-30",
        "2026-10-12%0A",
        "%202026-10-12",
    ],
)
def test_a_malformed_window_end_gives_422(client, authorization, window_end):
    response = client.get(f"/trends?window_end={window_end}", headers=authorization)

    assert response.status_code == 422
    assert response.json() == {
        "error": "invalid_parameter",
        "parameters": ["window_end"],
    }
