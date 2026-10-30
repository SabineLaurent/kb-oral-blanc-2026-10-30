import io

import pytest
from http_helpers import http_error

from k_b.etl import http_download
from k_b.etl.sources.github import api as github_api
from k_b.etl.sources.github.api import GithubPausedError, get_from_github_api
from k_b.etl.user_agent import USER_AGENT

BODY = b'[{"tag_name": "v1.0.0"}]'
PATH = "repos/octocat/hello-world/releases?per_page=100"


class FakeResponse(io.BytesIO):
    def __init__(self, body, url):
        super().__init__(body)
        self.url = url


class FakeUrlopen:
    """Stands for urllib's urlopen: records the request, answers without any network."""

    def __init__(self):
        self.body = BODY
        self.final_url = github_api.API_URL + PATH
        self.requests = []
        self.timeouts = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        self.timeouts.append(timeout)
        return FakeResponse(self.body, self.final_url)


@pytest.fixture
def fake_urlopen(monkeypatch):
    fake = FakeUrlopen()
    monkeypatch.setattr(http_download, "urlopen", fake)
    monkeypatch.delenv(github_api.TOKEN_VARIABLE, raising=False)
    return fake


def test_the_response_is_returned_unchanged(fake_urlopen):
    assert get_from_github_api(PATH) == BODY


def test_the_request_follows_the_github_conventions_and_has_a_timeout(fake_urlopen):
    get_from_github_api(PATH)

    request = fake_urlopen.requests[0]
    assert request.full_url == "https://api.github.com/" + PATH
    assert request.get_header("User-agent") == USER_AGENT
    assert request.get_header("Accept") == "application/vnd.github+json"
    assert request.get_header("X-github-api-version") == "2026-03-10"
    assert fake_urlopen.timeouts[0] > 0


def test_without_a_token_the_request_is_anonymous(fake_urlopen):
    get_from_github_api(PATH)

    assert not fake_urlopen.requests[0].has_header("Authorization")


def test_the_token_is_sent_but_never_forwarded_to_a_redirection(
    fake_urlopen, monkeypatch
):
    monkeypatch.setenv(github_api.TOKEN_VARIABLE, "test-token")

    get_from_github_api(PATH)

    request = fake_urlopen.requests[0]
    assert request.get_header("Authorization") == "Bearer test-token"
    # urllib copies request.headers to a redirection, never the unredirected ones.
    assert "Authorization" not in request.headers


@pytest.mark.parametrize(
    ("status", "wait"), [(429, github_api.RATE_LIMITED_WAIT_SECONDS), (502, 5)]
)
def test_only_a_rate_limited_request_waits_the_minute_github_asks(
    fake_urlopen, monkeypatch, no_wait_before_a_retry, status, wait
):
    answers = [http_error(github_api.API_URL + PATH, status)]

    def failing_once(request, timeout):
        if answers:
            raise answers.pop()
        return fake_urlopen(request, timeout)

    monkeypatch.setattr(http_download, "urlopen", failing_once)

    assert get_from_github_api(PATH) == BODY
    assert no_wait_before_a_retry == [wait]
    assert github_api.RATE_LIMITED_WAIT_SECONDS == 60


# 2026-10-25T16:00:00Z, in epoch seconds.
RESET = 1792944000


def test_after_a_rate_limit_with_no_request_left_no_request_is_sent_until_its_reset(
    fake_urlopen, monkeypatch
):
    exhausted = http_error(
        github_api.API_URL + PATH,
        403,
        x_ratelimit_remaining="0",
        x_ratelimit_reset=str(RESET),
    )

    def exhausted_once(request, timeout):
        fake_urlopen.requests.append(request)
        raise exhausted

    monkeypatch.setattr(http_download, "urlopen", exhausted_once)
    monkeypatch.setattr(github_api, "time", lambda: RESET - 3600)
    with pytest.raises(type(exhausted)):
        get_from_github_api(PATH)

    monkeypatch.setattr(http_download, "urlopen", fake_urlopen)
    with pytest.raises(
        GithubPausedError, match="until 2026-10-25T16:00:00\\+00:00; none sent"
    ):
        get_from_github_api(PATH)
    assert len(fake_urlopen.requests) == 1

    monkeypatch.setattr(github_api, "time", lambda: RESET)
    assert get_from_github_api(PATH) == BODY
    assert len(fake_urlopen.requests) == 2
