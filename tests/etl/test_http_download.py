import io
import logging
from urllib.error import HTTPError, URLError

import pytest
from http_helpers import http_error

from k_b.etl import http_download
from k_b.etl.http_download import RedirectedOutsideError, download
from k_b.etl.source_contract import PageRefusedError
from k_b.etl.user_agent import USER_AGENT

BODY = b"<feed>one paper</feed>"
TRUSTED_URL_PREFIX = "https://example.org/"
URL = TRUSTED_URL_PREFIX + "feed?page=1"


class FakeResponse(io.BytesIO):
    def __init__(self, body, url):
        super().__init__(body)
        self.url = url


class FakeUrlopen:
    """Stands for urllib's urlopen: records the request, answers without any network."""

    def __init__(self):
        self.body = BODY
        self.final_url = URL
        self.error = None
        # Raised one by one, before any self.error: a failure, then the answer of the retry.
        self.errors = []
        self.requests = []
        self.timeouts = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        self.timeouts.append(timeout)
        if self.errors:
            raise self.errors.pop(0)
        if self.error:
            raise self.error
        return FakeResponse(self.body, self.final_url)


@pytest.fixture
def fake_urlopen(monkeypatch):
    fake = FakeUrlopen()
    monkeypatch.setattr(http_download, "urlopen", fake)
    return fake


def test_the_body_is_returned_unchanged(fake_urlopen):
    assert download(URL, TRUSTED_URL_PREFIX, max_bytes=1000) == BODY


def test_the_request_names_k_b_and_has_a_timeout(fake_urlopen):
    download(URL, TRUSTED_URL_PREFIX, max_bytes=1000)

    request = fake_urlopen.requests[0]
    assert request.full_url == URL
    assert request.get_header("User-agent") == USER_AGENT
    assert fake_urlopen.timeouts[0] > 0


def test_the_given_headers_are_sent(fake_urlopen):
    download(URL, TRUSTED_URL_PREFIX, max_bytes=1000, headers={"Accept": "text/csv"})

    assert fake_urlopen.requests[0].get_header("Accept") == "text/csv"


def test_an_unredirected_header_is_sent_but_never_forwarded_to_a_redirection(
    fake_urlopen,
):
    download(
        URL,
        TRUSTED_URL_PREFIX,
        max_bytes=1000,
        unredirected_headers={"Authorization": "Bearer test-token"},
    )

    request = fake_urlopen.requests[0]
    assert request.get_header("Authorization") == "Bearer test-token"
    # urllib copies request.headers to a redirection, never the unredirected ones.
    assert "Authorization" not in request.headers


@pytest.mark.parametrize(
    "final_url",
    ["http://example.org/feed?page=1", "https://example.org.example.com/feed?page=1"],
)
def test_a_redirection_away_from_the_trusted_prefix_is_refused(fake_urlopen, final_url):
    fake_urlopen.final_url = final_url

    with pytest.raises(ValueError, match="redirected"):
        download(URL, TRUSTED_URL_PREFIX, max_bytes=1000)


@pytest.mark.parametrize(
    ("url", "trusted_url_prefix"),
    [
        (URL, "https://"),
        ("http://example.org/feed", "http://example.org/"),
        (URL, "https://example.org"),
        (URL, "https://example.com/"),
        ("https://user@example.org/feed", "https://user@example.org/"),
        ("https://@/feed", "https://@/"),
    ],
    ids=[
        "prefix-without-host",
        "prefix-not-https",
        "prefix-without-path",
        "url-outside-prefix",
        "prefix-with-user-info",
        "prefix-with-empty-host",
    ],
)
def test_a_badly_set_prefix_is_refused_before_any_request(
    fake_urlopen, url, trusted_url_prefix
):
    with pytest.raises(ValueError, match="trusted"):
        download(url, trusted_url_prefix, max_bytes=1000)

    assert fake_urlopen.requests == []


def test_a_refused_redirection_tells_where_it_led(fake_urlopen):
    fake_urlopen.final_url = "https://example.org/landing"

    with pytest.raises(RedirectedOutsideError) as refusal:
        download(URL, URL, max_bytes=1000)

    assert refusal.value.redirect_url == "https://example.org/landing"
    assert isinstance(refusal.value, ValueError)
    assert str(refusal.value) == (
        f"response redirected to https://example.org/landing, outside {URL}, refused"
    )


def test_a_body_larger_than_the_limit_is_refused(fake_urlopen):
    with pytest.raises(PageRefusedError, match="larger than"):
        download(URL, TRUSTED_URL_PREFIX, max_bytes=len(BODY) - 1)


def test_a_body_exactly_at_the_limit_is_returned(fake_urlopen):
    assert download(URL, TRUSTED_URL_PREFIX, max_bytes=len(BODY)) == BODY


def test_an_http_error_is_raised_to_the_caller(fake_urlopen):
    fake_urlopen.error = HTTPError(URL, 304, "Not Modified", {}, None)

    with pytest.raises(HTTPError):
        download(URL, TRUSTED_URL_PREFIX, max_bytes=1000)


def test_a_429_is_retried_once_after_its_retry_after(
    fake_urlopen, no_wait_before_a_retry
):
    fake_urlopen.errors = [http_error(URL, 429, Retry_After="12")]

    assert download(URL, TRUSTED_URL_PREFIX, max_bytes=1000) == BODY
    assert len(fake_urlopen.requests) == 2
    assert no_wait_before_a_retry == [12]


def test_a_429_without_retry_after_waits_the_given_rate_limited_wait(
    fake_urlopen, no_wait_before_a_retry
):
    fake_urlopen.errors = [http_error(URL, 429)]

    download(URL, TRUSTED_URL_PREFIX, max_bytes=1000, rate_limited_wait_seconds=60)

    assert no_wait_before_a_retry == [60]


@pytest.mark.parametrize(
    "error",
    [
        URLError(ConnectionRefusedError(61, "Connection refused")),
        ConnectionResetError(54, "Connection reset by peer"),
    ],
    ids=["refused", "dropped"],
)
def test_a_failed_connection_is_retried(fake_urlopen, no_wait_before_a_retry, error):
    fake_urlopen.errors = [error]

    assert download(URL, TRUSTED_URL_PREFIX, max_bytes=1000) == BODY
    assert no_wait_before_a_retry == [http_download.RETRY_WAIT_SECONDS]


def test_a_second_failure_is_raised_without_a_third_request(
    fake_urlopen, no_wait_before_a_retry
):
    fake_urlopen.error = http_error(URL, 503)

    with pytest.raises(HTTPError):
        download(URL, TRUSTED_URL_PREFIX, max_bytes=1000)

    assert len(fake_urlopen.requests) == 2
    assert no_wait_before_a_retry == [http_download.RETRY_WAIT_SECONDS]


def test_an_answer_not_retried_is_raised_at_once(fake_urlopen, no_wait_before_a_retry):
    fake_urlopen.error = http_error(URL, 429, Retry_After="3600")

    with pytest.raises(HTTPError):
        download(URL, TRUSTED_URL_PREFIX, max_bytes=1000)

    assert len(fake_urlopen.requests) == 1
    assert no_wait_before_a_retry == []


def test_a_rate_limit_with_no_request_left_is_raised_with_its_reset(fake_urlopen):
    fake_urlopen.error = http_error(
        URL, 403, x_ratelimit_remaining="0", x_ratelimit_reset="1792944000"
    )

    with pytest.raises(HTTPError) as failure:
        download(URL, TRUSTED_URL_PREFIX, max_bytes=1000)

    assert failure.value.__notes__ == [
        "rate limit reached, no request left until 2026-10-25T16:00:00+00:00"
    ]


def test_the_retry_is_logged_without_the_query_of_the_url(fake_urlopen, caplog):
    fake_urlopen.errors = [http_error(URL, 502)]

    with caplog.at_level(logging.WARNING):
        download(URL, TRUSTED_URL_PREFIX, max_bytes=1000)

    [record] = caplog.records
    assert record.getMessage() == (
        "GET 'https://example.org/feed' failed: 'HTTP Error 502: error'; one retry in 5 s"
    )
