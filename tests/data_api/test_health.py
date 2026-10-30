"""GET /health: the probe of the container, served without key, which says ok and nothing else."""


def test_health_answers_ok_without_key(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_refuses_another_method_with_405(client):
    response = client.post("/health")

    assert response.status_code == 405
    assert response.json() == {"error": "method_not_allowed"}


def test_a_host_other_than_the_allowed_ones_gives_400_on_health(client):
    response = client.get("/health", headers={"Host": "evil.example"})

    assert response.status_code == 400
