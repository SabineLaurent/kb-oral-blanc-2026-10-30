import logging
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from k_b.etl.extract import MAX_QUOTED_FAILURE_LENGTH, collect_all_sources
from k_b.etl.http_download import RedirectedOutsideError
from k_b.etl.raw_store import load_last_collection_dates, save_last_collection_dates
from k_b.etl.source_contract import ExpectedSourceError, PageRefusedError
from k_b.etl.sources import cisa_kev, tldr

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
FIRST_RUN_AT = datetime(2026, 9, 30, 10, 0, tzinfo=UTC)
SECOND_RUN_AT = FIRST_RUN_AT + timedelta(hours=1)


class WorkingSource:
    extension = "json"
    acquisition_dir_name = "automated_watch"

    def __init__(self, name="working"):
        self.name = name
        self.received_last_collected_at = []

    def fetch(self, last_collected_at):
        self.received_last_collected_at.append(last_collected_at)
        return b'{"items": []}'


class BrokenSource:
    name = "broken"
    extension = "json"
    acquisition_dir_name = "automated_watch"

    def fetch(self, last_collected_at):
        raise ConnectionError("source is down")


class FailingSource:
    name = "failing"
    extension = "json"
    acquisition_dir_name = "automated_watch"

    def __init__(self, error):
        self.error = error

    def fetch(self, last_collected_at):
        raise self.error


@pytest.mark.parametrize(
    "error",
    [
        ExpectedSourceError("a failure met in normal operation"),
        PageRefusedError("incomplete search results, refused"),
        URLError("nodename nor servname provided"),
        ConnectionError("source is down"),
        TimeoutError("timed out"),
    ],
)
def test_an_expected_failure_is_logged_on_its_line_without_its_traceback(
    tmp_path, caplog, error
):
    with caplog.at_level(logging.ERROR, logger="k_b.etl.extract"):
        collect_all_sources(
            [FailingSource(error)], tmp_path / "raw", tmp_path, FIRST_RUN_AT
        )

    [record] = caplog.records
    assert record.getMessage().startswith(
        f"collection failed for source 'failing': {type(error).__name__} "
    )
    # False, not None: exc_info=False stays as given.
    assert not record.exc_info


def test_an_expected_failure_is_logged_with_its_long_message_and_notes_whole(
    tmp_path, caplog
):
    # Its line is the only copy of its message: no traceback repeats it.
    error = ExpectedSourceError("m" * 250)
    error.add_note("n" * 250)

    with caplog.at_level(logging.ERROR, logger="k_b.etl.extract"):
        collect_all_sources(
            [FailingSource(error)], tmp_path / "raw", tmp_path, FIRST_RUN_AT
        )

    [record] = caplog.records
    assert record.getMessage().endswith(f"'{'m' * 250}'; '{'n' * 250}'")


def test_a_failure_message_longer_than_the_cut_is_cut_on_its_line(tmp_path, caplog):
    error = ExpectedSourceError("m" * 500)

    with caplog.at_level(logging.ERROR, logger="k_b.etl.extract"):
        collect_all_sources(
            [FailingSource(error)], tmp_path / "raw", tmp_path, FIRST_RUN_AT
        )

    [record] = caplog.records
    quoted = record.getMessage().removeprefix(
        "collection failed for source 'failing': ExpectedSourceError "
    )
    assert quoted == ("'" + "m" * 500)[:MAX_QUOTED_FAILURE_LENGTH]


@pytest.mark.parametrize(
    "error", [ValueError("invalid literal for int()"), KeyError("id")]
)
def test_an_unexpected_failure_is_logged_with_its_traceback(tmp_path, caplog, error):
    with caplog.at_level(logging.ERROR, logger="k_b.etl.extract"):
        collect_all_sources(
            [FailingSource(error)], tmp_path / "raw", tmp_path, FIRST_RUN_AT
        )

    [record] = caplog.records
    assert record.exc_info is not None
    assert record.exc_info[1] is error


def test_a_broken_source_does_not_stop_the_others(tmp_path):
    errors_by_source = collect_all_sources(
        [BrokenSource(), WorkingSource()], tmp_path / "raw", tmp_path, FIRST_RUN_AT
    ).errors_by_source

    assert isinstance(errors_by_source["broken"], ConnectionError)
    assert errors_by_source["working"] is None
    raw_file_path = (
        tmp_path / "raw" / "automated_watch" / "working" / "2026-09-30T100000Z.json"
    )
    assert raw_file_path.read_bytes() == b'{"items": []}'
    assert not (tmp_path / "raw" / "automated_watch" / "broken").exists()


def test_only_successful_sources_advance_their_last_collection_date(tmp_path):
    collect_all_sources(
        [BrokenSource(), WorkingSource()], tmp_path / "raw", tmp_path, FIRST_RUN_AT
    )

    assert load_last_collection_dates(tmp_path) == {"working": FIRST_RUN_AT}


def test_a_source_receives_its_last_successful_collection_date(tmp_path):
    working_source = WorkingSource()

    collect_all_sources([working_source], tmp_path / "raw", tmp_path, FIRST_RUN_AT)
    collect_all_sources([working_source], tmp_path / "raw", tmp_path, SECOND_RUN_AT)

    assert working_source.received_last_collected_at == [None, FIRST_RUN_AT]


def test_a_second_run_at_the_same_instant_fails_without_overwriting(tmp_path):
    collect_all_sources([WorkingSource()], tmp_path / "raw", tmp_path, FIRST_RUN_AT)

    errors_by_source = collect_all_sources(
        [WorkingSource()], tmp_path / "raw", tmp_path, FIRST_RUN_AT
    ).errors_by_source

    assert isinstance(errors_by_source["working"], FileExistsError)


def test_a_source_name_that_is_not_a_safe_path_segment_is_refused(tmp_path):
    raw_dir = tmp_path / "raw"

    errors_by_source = collect_all_sources(
        [WorkingSource(name="../escape")], raw_dir, tmp_path, FIRST_RUN_AT
    ).errors_by_source

    assert isinstance(errors_by_source["../escape"], ValueError)
    assert not (raw_dir / "escape").exists()
    assert not (raw_dir / "automated_watch").exists()


class LibrarySource(WorkingSource):
    """A source of what the user selected, as Zotero is."""

    acquisition_dir_name = "selected_library"


def test_a_source_of_the_selected_library_is_stored_in_its_own_folder_of_raw(tmp_path):
    errors_by_source = collect_all_sources(
        [LibrarySource(name="library")], tmp_path / "raw", tmp_path, FIRST_RUN_AT
    ).errors_by_source

    assert errors_by_source == {"library": None}
    raw_file_path = (
        tmp_path / "raw" / "selected_library" / "library" / "2026-09-30T100000Z.json"
    )
    assert raw_file_path.read_bytes() == b'{"items": []}'
    assert not (tmp_path / "raw" / "automated_watch").exists()


def test_a_raw_folder_that_is_not_a_safe_path_segment_is_refused(tmp_path):
    source = WorkingSource()
    source.acquisition_dir_name = "../escape"

    errors_by_source = collect_all_sources(
        [source], tmp_path / "raw", tmp_path, FIRST_RUN_AT
    ).errors_by_source

    assert isinstance(errors_by_source["working"], ValueError)
    assert not (tmp_path / "escape").exists()
    assert not (tmp_path / "raw").exists()


def test_a_raw_folder_that_is_not_one_of_the_two_folders_of_raw_is_refused(tmp_path):
    source = WorkingSource()
    source.acquisition_dir_name = "automated_watchs"

    errors_by_source = collect_all_sources(
        [source], tmp_path / "raw", tmp_path, FIRST_RUN_AT
    ).errors_by_source

    assert isinstance(errors_by_source["working"], ValueError)
    assert "automated_watchs" in str(errors_by_source["working"])
    assert not (tmp_path / "raw").exists()


class UnchangedSource:
    name = "unchanged"
    extension = "csv"
    acquisition_dir_name = "automated_watch"

    def fetch(self, last_collected_at):
        return None


def test_a_source_with_nothing_new_writes_no_raw_file_but_succeeds(tmp_path):
    errors_by_source = collect_all_sources(
        [UnchangedSource()], tmp_path / "raw", tmp_path, FIRST_RUN_AT
    ).errors_by_source

    assert errors_by_source == {"unchanged": None}
    assert not (tmp_path / "raw" / "automated_watch").exists()
    assert load_last_collection_dates(tmp_path) == {"unchanged": FIRST_RUN_AT}


class ScrapedSource:
    """Returns several pages, as a scraped source reading one page per day does."""

    extension = "html"
    acquisition_dir_name = "automated_watch"

    def __init__(self, pages):
        self.name = "scraped"
        self.pages = pages

    def fetch(self, last_collected_at):
        return self.pages


def test_each_page_of_a_source_is_written_as_received_in_a_folder_per_collection(
    tmp_path,
):
    pages = {
        "2026-09-29": b"<html>monday</html>",
        "2026-09-30": b"<html>tuesday</html>",
    }

    errors_by_source = collect_all_sources(
        [ScrapedSource(pages)], tmp_path / "raw", tmp_path, FIRST_RUN_AT
    ).errors_by_source

    assert errors_by_source == {"scraped": None}
    collection_dir = (
        tmp_path / "raw" / "automated_watch" / "scraped" / "2026-09-30T100000Z"
    )
    assert (collection_dir / "2026-09-29.html").read_bytes() == b"<html>monday</html>"
    assert (collection_dir / "2026-09-30.html").read_bytes() == b"<html>tuesday</html>"


def test_each_source_logs_its_start_and_the_raw_files_it_wrote(tmp_path, caplog):
    pages = {
        "2026-09-29": b"<html>monday</html>",
        "2026-09-30": b"<html>tuesday</html>",
    }
    caplog.set_level(logging.INFO, logger="k_b.etl.extract")

    collect_all_sources(
        [WorkingSource(), UnchangedSource(), ScrapedSource(pages), BrokenSource()],
        tmp_path / "raw",
        tmp_path,
        FIRST_RUN_AT,
    )

    collected_lines = [
        record.getMessage()
        for record in caplog.records
        if "collected, raw files written" in record.getMessage()
    ]
    assert collected_lines == [
        "source 'working' collected, raw files written: 1",
        "source 'unchanged' collected, raw files written: 0",
        "source 'scraped' collected, raw files written: 2",
    ]
    started_lines = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().endswith(" started")
    ]
    # The broken source too: its start is logged before its failure.
    assert started_lines == [
        "source 'working' started",
        "source 'unchanged' started",
        "source 'scraped' started",
        "source 'broken' started",
    ]


def test_a_source_failing_at_its_second_page_keeps_the_first_one_written(tmp_path):
    pages = {
        "2026-09-29": b"<html>monday</html>",
        "2026-09-30": b"<html>tuesday</html>",
    }
    collection_dir = (
        tmp_path / "raw" / "automated_watch" / "scraped" / "2026-09-30T100000Z"
    )
    collection_dir.mkdir(parents=True)
    # The second page is there already: its write fails, after the first one.
    (collection_dir / "2026-09-30.html").write_bytes(b"there before")

    outcome = collect_all_sources(
        [ScrapedSource(pages)], tmp_path / "raw", tmp_path, FIRST_RUN_AT
    )

    assert isinstance(outcome.errors_by_source["scraped"], FileExistsError)
    assert (collection_dir / "2026-09-29.html").read_bytes() == b"<html>monday</html>"


def test_a_page_name_that_is_not_a_safe_path_segment_is_refused_before_any_write(
    tmp_path,
):
    pages = {"2026-09-29": b"<html>monday</html>", "../escape": b"<html>evil</html>"}

    errors_by_source = collect_all_sources(
        [ScrapedSource(pages)], tmp_path / "raw", tmp_path, FIRST_RUN_AT
    ).errors_by_source

    assert isinstance(errors_by_source["scraped"], ValueError)
    assert not (tmp_path / "raw" / "automated_watch").exists()
    escaped_page = tmp_path / "raw" / "automated_watch" / "scraped" / "escape.html"
    assert not escaped_page.exists()


@pytest.mark.skipif(
    os.geteuid() == 0, reason="root writes a file whatever its permissions"
)
def test_a_state_that_cannot_be_written_is_returned_and_logged_with_its_path(
    tmp_path, caplog
):
    (tmp_path / "raw" / "automated_watch").mkdir(parents=True)
    tmp_path.chmod(0o500)

    try:
        with caplog.at_level(logging.ERROR, logger="k_b.etl.extract"):
            outcome = collect_all_sources(
                [WorkingSource()], tmp_path / "raw", tmp_path, FIRST_RUN_AT
            )
    finally:
        tmp_path.chmod(0o700)

    assert isinstance(outcome.state_error, PermissionError)
    assert outcome.errors_by_source == {"working": None}
    assert list((tmp_path / "raw" / "automated_watch" / "working").iterdir())
    assert not (tmp_path / "collection_state.json").exists()
    [record] = caplog.records
    assert str(tmp_path / "collection_state.json") in record.getMessage()
    assert record.exc_info is not None


def test_a_state_written_without_error_is_reported_as_such(tmp_path):
    outcome = collect_all_sources(
        [WorkingSource()], tmp_path / "raw", tmp_path, FIRST_RUN_AT
    )

    assert outcome.state_error is None


def test_a_failed_tldr_page_is_logged_once_with_its_day_and_url(
    tmp_path, monkeypatch, caplog
):
    def failing_download(url, trusted_url_prefix, max_bytes):
        raise HTTPError(url, 500, "Internal Server Error", {}, None)

    monkeypatch.setattr(tldr, "download", failing_download)
    monkeypatch.setattr(tldr, "current_day", lambda: date(2026, 10, 4))
    # One day to collect: 2026-10-04.
    save_last_collection_dates(
        tmp_path, {"tldr_ai": datetime(2026, 10, 4, 6, 0, tzinfo=UTC)}
    )

    with caplog.at_level(logging.INFO, logger="k_b"):
        outcome = collect_all_sources(
            [tldr.TldrSource("ai")], tmp_path / "raw", tmp_path, SECOND_RUN_AT
        )

    assert isinstance(outcome.errors_by_source["tldr_ai"], HTTPError)
    [error_record] = [
        record for record in caplog.records if record.levelno >= logging.ERROR
    ]
    assert error_record.name == "k_b.etl.extract"
    assert error_record.getMessage() == (
        "collection failed for source 'tldr_ai': HTTPError "
        "'HTTP Error 500: Internal Server Error'; "
        "'tldr_ai 2026-10-04, from https://tldr.tech/ai/2026-10-04'"
    )


# Collected pages (tests/etl/sources/test_tldr.py): the landing page of the edition, and the
# edition of 2026-10-07 under the date of each test.
TLDR_LANDING_PAGE = (FIXTURES_DIR / "tldr_ai_landing_extract.html").read_bytes()
TLDR_AI_PAGE = (FIXTURES_DIR / "tldr_ai_extract.html").read_bytes()


def tldr_edition_page(day):
    return TLDR_AI_PAGE.replace(
        b"TLDR AI 2026-10-07</h1>", f"TLDR AI {day}</h1>".encode()
    )


def serve_tldr_pages(monkeypatch, today, pages_by_url):
    """Stand for tldr.tech on today: each url its page, any other day redirected to the landing
    page; return the urls asked."""
    urls_asked = []

    def fake_download(url, trusted_url_prefix, max_bytes):
        urls_asked.append(url)
        if url in pages_by_url:
            return pages_by_url[url]
        raise RedirectedOutsideError(url, "https://tldr.tech/ai")

    monkeypatch.setattr(tldr, "download", fake_download)
    monkeypatch.setattr(tldr, "current_day", lambda: today)
    monkeypatch.setattr(tldr, "sleep", lambda seconds: None)
    return urls_asked


def test_a_tldr_page_that_is_not_an_edition_writes_nothing_and_keeps_the_date(
    tmp_path, monkeypatch, caplog
):
    serve_tldr_pages(
        monkeypatch,
        date(2026, 10, 4),
        {
            "https://tldr.tech/ai/2026-10-02": tldr_edition_page("2026-10-02"),
            "https://tldr.tech/ai/2026-10-03": TLDR_LANDING_PAGE,
        },
    )
    last_collected_at = datetime(2026, 10, 2, 6, 0, tzinfo=UTC)
    save_last_collection_dates(tmp_path, {"tldr_ai": last_collected_at})

    outcome = collect_all_sources(
        [tldr.TldrSource("ai")],
        tmp_path / "raw",
        tmp_path,
        datetime(2026, 10, 4, 8, 0, tzinfo=UTC),
    )

    assert isinstance(outcome.errors_by_source["tldr_ai"], ValueError)
    assert load_last_collection_dates(tmp_path) == {"tldr_ai": last_collected_at}
    # The edition of 2026-10-02, read before the page refused, is not written either.
    assert not (tmp_path / "raw").exists()
    [error_record] = [
        record for record in caplog.records if record.levelno >= logging.ERROR
    ]
    assert error_record.getMessage() == (
        "collection failed for source 'tldr_ai': PageRefusedError "
        "'not_the_edition_of_the_day'; "
        "'tldr_ai 2026-10-03, from https://tldr.tech/ai/2026-10-03'; "
        '"first h1 is not an edition title with its date: '
        "'Keep up with AI in 5 minutes'\""
    )


def test_a_past_tldr_day_never_an_edition_stops_failing_once_a_later_edition_is_read(
    tmp_path, monkeypatch, caplog
):
    refused_url = "https://tldr.tech/ai/2026-10-02"
    save_last_collection_dates(
        tmp_path, {"tldr_ai": datetime(2026, 10, 2, 6, 0, tzinfo=UTC)}
    )

    def collect_on(today, pages_by_url):
        urls_asked = serve_tldr_pages(monkeypatch, today, pages_by_url)
        outcome = collect_all_sources(
            [tldr.TldrSource("ai")],
            tmp_path / "raw",
            tmp_path,
            datetime.combine(today, datetime.min.time(), UTC) + timedelta(hours=8),
        )
        return outcome.errors_by_source["tldr_ai"], urls_asked

    # Saturday: no edition after the page refused, the source fails.
    error, _ = collect_on(date(2026, 10, 3), {refused_url: TLDR_LANDING_PAGE})
    assert isinstance(error, ValueError)
    assert load_last_collection_dates(tmp_path)["tldr_ai"].date() == date(2026, 10, 2)

    # Monday: the edition of the day is read, the day refused is given up.
    monday_url = "https://tldr.tech/ai/2026-10-05"
    with caplog.at_level(logging.WARNING, logger="k_b"):
        error, _ = collect_on(
            date(2026, 10, 5),
            {
                refused_url: TLDR_LANDING_PAGE,
                monday_url: tldr_edition_page("2026-10-05"),
            },
        )
    assert error is None
    assert load_last_collection_dates(tmp_path)["tldr_ai"].date() == date(2026, 10, 5)
    [page] = (tmp_path / "raw" / "automated_watch" / "tldr_ai").glob("*/*.html")
    assert page.name == "2026-10-05.html"
    assert "2026-10-02: not_the_edition_of_the_day, given up" in caplog.text

    # Tuesday: the day given up is not asked again.
    error, urls_asked = collect_on(date(2026, 10, 6), {refused_url: TLDR_LANDING_PAGE})
    assert error is None
    assert refused_url not in urls_asked


def test_the_error_and_the_notes_of_a_failed_source_are_quoted_escaped(
    tmp_path, caplog
):
    class FailingSource(WorkingSource):
        def fetch(self, last_collected_at):
            error = ValueError("a value\nERROR forged")
            error.add_note("a note\nERROR forged")
            raise error

    with caplog.at_level(logging.ERROR, logger="k_b.etl.extract"):
        collect_all_sources(
            [FailingSource("failing")], tmp_path / "raw", tmp_path, FIRST_RUN_AT
        )

    [record] = caplog.records
    assert record.getMessage() == (
        "collection failed for source 'failing': ValueError "
        "'a value\\nERROR forged'; 'a note\\nERROR forged'"
    )


def test_a_source_whose_entries_are_all_invalid_writes_no_raw_and_keeps_its_date(
    tmp_path, monkeypatch, caplog
):
    catalog = (FIXTURES_DIR / "cisa_kev_extract.csv").read_text()
    for date_added in ("2026-09-29", "2026-09-27", "2021-11-03"):
        catalog = catalog.replace(f",{date_added},", ",not a date,")
    monkeypatch.setattr(cisa_kev, "download", lambda *arguments: catalog.encode())
    save_last_collection_dates(tmp_path, {"cisa_kev": FIRST_RUN_AT})

    with caplog.at_level(logging.ERROR):
        outcome = collect_all_sources(
            [cisa_kev.CisaKevSource()], tmp_path / "raw", tmp_path, SECOND_RUN_AT
        )

    assert str(outcome.errors_by_source["cisa_kev"]).startswith(
        "all_items_invalid: 4 entries, all rejected; first: line 2 ('CVE-2026-86950')"
    )
    assert not (tmp_path / "raw" / "automated_watch" / "cisa_kev").exists()
    assert load_last_collection_dates(tmp_path) == {"cisa_kev": FIRST_RUN_AT}
    [record] = caplog.records
    assert "all_items_invalid" in record.getMessage()
