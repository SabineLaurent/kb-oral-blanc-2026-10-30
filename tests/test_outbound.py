"""The rules of a connection K-B opens by HTTP: the URL it accepts, and its HTTP client."""

import httpx2
import pytest

from k_b.core.outbound import find_url_refusal, open_http_client

VARIABLE = "KB_TEST_URL"


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:8000", "https://api-donnees:8000/kb", "http://localhost"]
)
def test_a_url_of_an_allowed_scheme_is_accepted(url):
    assert find_url_refusal(VARIABLE, url, ("http", "https")) is None


@pytest.mark.parametrize(
    ("url", "schemes", "refusal"),
    [
        ("ftp://host", ("https",), "KB_TEST_URL: scheme not https"),
        ("ftp://host", ("http", "https"), "KB_TEST_URL: scheme not http nor https"),
        ("http://", ("http",), "KB_TEST_URL: no host"),
        ("http://ho_st", ("http",), "KB_TEST_URL: host not a lowercase host name"),
        ("http://host:99999", ("http",), "KB_TEST_URL: not a URL"),
        ("http://user:secret@host", ("http",), "KB_TEST_URL: with user information"),
        (
            "http://host/?key=secret",
            ("http",),
            "KB_TEST_URL: with a query or a fragment",
        ),
        ("http://host/#secret", ("http",), "KB_TEST_URL: with a query or a fragment"),
    ],
)
def test_a_url_refused_is_named_by_its_variable_never_quoted(url, schemes, refusal):
    assert find_url_refusal(VARIABLE, url, schemes) == refusal


def test_the_client_reads_no_proxy_of_the_environment_and_follows_no_redirection(
    monkeypatch,
):
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example:8080")

    # Without transport: with one, httpx2 never reads the proxies of the environment, so the
    # behavior would prove nothing.
    with open_http_client(
        "http://127.0.0.1:8000", {}, httpx2.Timeout(1.0), transport=None
    ) as http_client:
        assert http_client.trust_env is False
        assert http_client.follow_redirects is False
