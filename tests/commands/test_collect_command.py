"""kb collect: the sources run, skipped or chosen, the state written, the GitHub token announced."""

import json
import os
import re
from datetime import UTC, datetime, timedelta

import pytest
from commands.cli_helpers import (
    LOG_LINE_DATE,
    collected_kinds,
)
from etl.test_extract import BrokenSource, WorkingSource
from etl.test_transform import FIXTURES_DIR

from k_b import cli
from k_b.commands import (
    collect_command,
)
from k_b.core import quoting
from k_b.etl import collected_sources
from k_b.etl.source_contract import SourceKind
from k_b.etl.sources import clipping


def declared_kinds(*sources):
    """The kind of collected_kinds, and the three kinds etl/collected_sources.py creates."""
    return collected_kinds(*sources) + tuple(
        SourceKind(name, (), None) for name in ("zotero", "clipping", "gh_archive")
    )


def test_collect_refuses_to_run_without_a_data_directory(monkeypatch, capsys):
    monkeypatch.delenv("KB_DATA_DIR", raising=False)

    exit_code = cli.main(["collect"])

    assert exit_code == 2
    assert "KB_DATA_DIR" in capsys.readouterr().err


def test_collect_refuses_a_data_directory_that_does_not_exist(monkeypatch, tmp_path):
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path / "missing"))

    assert cli.main(["collect"]) == 2


def test_collect_reports_a_failure_but_still_stores_the_other_sources(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        collect_command,
        "SOURCE_KINDS",
        collected_kinds(BrokenSource(), WorkingSource()),
    )

    exit_code = cli.main(["collect"])

    assert exit_code == cli.ExitCode.PARTIAL_FAILURE
    assert list((tmp_path / "raw" / "automated_watch" / "working").iterdir())


def test_collect_shows_the_traceback_of_an_unexpected_failure_only(
    monkeypatch, tmp_path, capsys
):
    class FaultySource(WorkingSource):
        def fetch(self, last_collected_at):
            raise KeyError("a fault of the code")

    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        collect_command,
        "SOURCE_KINDS",
        collected_kinds(BrokenSource(), FaultySource("faulty")),
    )

    assert cli.main(["collect"]) == cli.ExitCode.FAILED
    error_output = capsys.readouterr().err
    # The ConnectionError of broken is expected: its line alone.
    assert error_output.count("Traceback") == 1
    assert "KeyError: 'a fault of the code'" in error_output
    assert "ConnectionError: source is down" not in error_output


def test_collect_writes_its_state_at_the_root_of_the_data_directory_not_in_raw(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        collect_command, "SOURCE_KINDS", collected_kinds(WorkingSource())
    )

    exit_code = cli.main(["collect"])

    assert exit_code == 0
    assert (tmp_path / "collection_state.json").is_file()
    assert not (tmp_path / "raw" / "collection_state.json").exists()


@pytest.mark.skipif(
    os.geteuid() == 0, reason="root writes a file whatever its permissions"
)
def test_collect_still_prints_the_summary_and_exits_1_when_the_state_cannot_be_written(
    monkeypatch, tmp_path, capsys
):
    (tmp_path / "raw" / "automated_watch").mkdir(parents=True)
    # The log folder stays writable: only the state cannot be written.
    (tmp_path / "logs").mkdir()
    tmp_path.chmod(0o500)
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        collect_command, "SOURCE_KINDS", collected_kinds(WorkingSource())
    )

    try:
        exit_code = cli.main(["collect"])
    finally:
        tmp_path.chmod(0o700)

    assert exit_code == 1
    assert (
        capsys.readouterr().out
        == "\nworking: ok\nzotero: skipped (KB_ZOTERO_DIR not set)\n"
        "clipping: skipped (KB_VAULT_DIR not set)\n"
        "gh_archive: skipped (GOOGLE_APPLICATION_CREDENTIALS not set)\n"
        "collection_state: failed (PermissionError)\n\n"
    )


@pytest.mark.parametrize("zotero_dir_variable", [None, ""])
def test_collect_skips_zotero_and_says_so_without_its_directory(
    monkeypatch, tmp_path, capsys, zotero_dir_variable
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("KB_DATA_DIR", str(data_dir))
    if zotero_dir_variable is not None:
        monkeypatch.setenv("KB_ZOTERO_DIR", zotero_dir_variable)
    monkeypatch.setattr(
        collect_command, "SOURCE_KINDS", collected_kinds(WorkingSource())
    )

    exit_code = cli.main(["collect"])

    assert exit_code == 0
    assert (
        capsys.readouterr().out
        == "\nworking: ok\nzotero: skipped (KB_ZOTERO_DIR not set)\n"
        "clipping: skipped (KB_VAULT_DIR not set)\n"
        "gh_archive: skipped (GOOGLE_APPLICATION_CREDENTIALS not set)\n\n"
    )
    assert not (data_dir / "raw" / "selected_library").exists()


def test_collect_extracts_zotero_into_the_selected_library_with_its_directory(
    monkeypatch, tmp_path, capsys, zotero_dir
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("KB_DATA_DIR", str(data_dir))
    monkeypatch.setenv("KB_ZOTERO_DIR", str(zotero_dir))
    monkeypatch.setattr(collect_command, "SOURCE_KINDS", collected_kinds())

    exit_code = cli.main(["collect"])

    assert exit_code == 0
    assert capsys.readouterr().out == (
        "\nzotero: ok\nclipping: skipped (KB_VAULT_DIR not set)\n"
        "gh_archive: skipped (GOOGLE_APPLICATION_CREDENTIALS not set)\n\n"
    )
    [extract_path] = (data_dir / "raw" / "selected_library" / "zotero").iterdir()
    assert extract_path.suffix == ".json"
    assert [document["key"] for document in json.loads(extract_path.read_bytes())] == [
        "AAAAAAA1",
        "BBBBBBB2",
    ]
    assert "zotero" in json.loads((data_dir / "collection_state.json").read_text())


def test_collect_reports_a_zotero_failure_and_keeps_its_last_collection_date(
    monkeypatch, tmp_path, capsys
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    directory_without_a_library = tmp_path / "Zotero"
    directory_without_a_library.mkdir()
    monkeypatch.setenv("KB_DATA_DIR", str(data_dir))
    monkeypatch.setenv("KB_ZOTERO_DIR", str(directory_without_a_library))
    monkeypatch.setattr(collect_command, "SOURCE_KINDS", collected_kinds())

    exit_code = cli.main(["collect"])

    assert exit_code == 1
    assert capsys.readouterr().out == (
        "\nzotero: failed (FileNotFoundError)\nclipping: skipped (KB_VAULT_DIR not set)\n"
        "gh_archive: skipped (GOOGLE_APPLICATION_CREDENTIALS not set)\n\n"
    )
    assert json.loads((data_dir / "collection_state.json").read_text()) == {}
    assert not (data_dir / "raw").exists()


@pytest.mark.parametrize("vault_dir_variable", [None, ""])
def test_collect_skips_the_clips_and_says_so_without_the_vault(
    monkeypatch, tmp_path, capsys, vault_dir_variable
):
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    if vault_dir_variable is not None:
        monkeypatch.setenv("KB_VAULT_DIR", vault_dir_variable)
    monkeypatch.setattr(
        collect_command, "SOURCE_KINDS", collected_kinds(WorkingSource())
    )

    exit_code = cli.main(["collect"])

    assert exit_code == 0
    assert "clipping: skipped (KB_VAULT_DIR not set)\n" in capsys.readouterr().out
    assert not (tmp_path / "raw" / "selected_library").exists()


def test_collect_copies_the_clips_into_the_selected_library_with_the_vault(
    monkeypatch, tmp_path, capsys
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    clippings_dir = tmp_path / "vault" / "Clippings"
    clippings_dir.mkdir(parents=True)
    clip = (FIXTURES_DIR / "clipping" / "article_with_author.md").read_bytes()
    (clippings_dir / "A clip.md").write_bytes(clip)
    monkeypatch.setenv("KB_DATA_DIR", str(data_dir))
    monkeypatch.setenv("KB_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setattr(collect_command, "SOURCE_KINDS", collected_kinds())
    # A collection later than the margin after the clip written above (CLIP_CHANGE_MARGIN).
    monkeypatch.setattr(
        clipping, "current_time", lambda: datetime.now(UTC) + timedelta(minutes=5)
    )

    exit_code = cli.main(["collect"])

    assert exit_code == 0
    assert "clipping: ok\n" in capsys.readouterr().out
    [collection_dir] = (data_dir / "raw" / "selected_library" / "clipping").iterdir()
    assert [page.read_bytes() for page in collection_dir.iterdir()] == [clip]
    assert (clippings_dir / "A clip.md").read_bytes() == clip
    assert "clipping" in json.loads((data_dir / "collection_state.json").read_text())


@pytest.mark.parametrize("bigquery_key_variable", [None, ""])
def test_collect_skips_gh_archive_and_says_so_without_the_bigquery_key(
    monkeypatch, tmp_path, capsys, bigquery_key_variable
):
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    if bigquery_key_variable is not None:
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", bigquery_key_variable)
    monkeypatch.setattr(collect_command, "SOURCE_KINDS", collected_kinds())

    assert cli.main(["collect"]) == 0
    assert "gh_archive: skipped (GOOGLE_APPLICATION_CREDENTIALS not set)\n" in (
        capsys.readouterr().out
    )


def test_collect_runs_gh_archive_with_the_bigquery_key(monkeypatch, tmp_path, capsys):
    created = []

    class FakeGhArchiveSource(WorkingSource):
        def __init__(self, create_client):
            super().__init__("gh_archive")
            created.append(create_client)

    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/path/to/a/key.json")
    monkeypatch.setattr(collect_command, "SOURCE_KINDS", collected_kinds())
    monkeypatch.setattr(collected_sources, "GhArchiveSource", FakeGhArchiveSource)

    exit_code = cli.main(["collect"])

    assert exit_code == 0
    assert created == [collected_sources.create_bigquery_client]
    output = capsys.readouterr().out
    assert "gh_archive: ok\n" in output
    assert "gh_archive: skipped" not in output
    assert list((tmp_path / "raw" / "automated_watch" / "gh_archive").iterdir())


def test_collect_only_runs_the_sources_chosen_and_keeps_the_date_of_the_others(
    monkeypatch, tmp_path, capsys, zotero_dir
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "collection_state.json").write_text(
        '{"working": "2026-10-17T06:00:00+00:00"}', encoding="utf-8"
    )
    clippings_dir = tmp_path / "vault" / "Clippings"
    clippings_dir.mkdir(parents=True)
    clip = (FIXTURES_DIR / "clipping" / "article_with_author.md").read_bytes()
    (clippings_dir / "A clip.md").write_bytes(clip)
    monkeypatch.setenv("KB_DATA_DIR", str(data_dir))
    monkeypatch.setenv("KB_ZOTERO_DIR", str(zotero_dir))
    monkeypatch.setenv("KB_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("KB_COLLECT_ONLY", "zotero,clipping")
    monkeypatch.setattr(
        collect_command, "SOURCE_KINDS", declared_kinds(WorkingSource())
    )
    # A collection later than the margin after the clip written above (CLIP_CHANGE_MARGIN).
    monkeypatch.setattr(
        clipping, "current_time", lambda: datetime.now(UTC) + timedelta(minutes=5)
    )

    exit_code = cli.main(["collect"])

    assert exit_code == 0
    assert capsys.readouterr().out == (
        "\nzotero: ok\nclipping: ok\ntest: skipped (not in KB_COLLECT_ONLY)\n"
        "gh_archive: skipped (not in KB_COLLECT_ONLY)\n\n"
    )
    assert sorted(path.name for path in (data_dir / "raw").iterdir()) == [
        "selected_library"
    ]
    state = json.loads((data_dir / "collection_state.json").read_text())
    assert state["working"] == "2026-10-17T06:00:00+00:00"
    assert {"zotero", "clipping"} <= state.keys()


def test_collect_without_collect_only_runs_every_source(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        collect_command,
        "SOURCE_KINDS",
        declared_kinds(WorkingSource(), WorkingSource("other")),
    )

    assert cli.main(["collect"]) == 0
    assert capsys.readouterr().out.startswith("\nworking: ok\nother: ok\n")
    assert sorted(
        path.name for path in (tmp_path / "raw" / "automated_watch").iterdir()
    ) == ["other", "working"]


def test_collect_only_prints_one_line_for_a_source_not_chosen(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KB_COLLECT_ONLY", "test")
    monkeypatch.setattr(
        collect_command, "SOURCE_KINDS", declared_kinds(WorkingSource())
    )

    assert cli.main(["collect"]) == 0
    # Not "zotero: skipped (KB_ZOTERO_DIR not set)" as well: Zotero was not chosen.
    assert capsys.readouterr().out == (
        "\nworking: ok\nzotero: skipped (not in KB_COLLECT_ONLY)\n"
        "clipping: skipped (not in KB_COLLECT_ONLY)\n"
        "gh_archive: skipped (not in KB_COLLECT_ONLY)\n\n"
    )


def test_collect_only_takes_the_names_with_spaces_around_in_any_case_and_twice(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KB_COLLECT_ONLY", " Test , TEST,test")
    source = WorkingSource()
    monkeypatch.setattr(collect_command, "SOURCE_KINDS", declared_kinds(source))

    assert cli.main(["collect"]) == 0
    assert source.received_last_collected_at == [None]


def assert_nothing_collected(data_dir, output):
    """Refused before the collection: no raw file, no state, no summary."""
    assert not (data_dir / "raw").exists()
    assert not (data_dir / "collection_state.json").exists()
    assert output == ""


def test_collect_only_refuses_a_name_that_is_not_a_source_folder_and_quotes_it(
    monkeypatch, tmp_path, capsys
):
    unknown_name = "tldr_ai\nERROR k_b.cli: forged line " + "x" * 200
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KB_COLLECT_ONLY", f"test,{unknown_name}")
    monkeypatch.setattr(
        collect_command, "SOURCE_KINDS", declared_kinds(WorkingSource())
    )

    exit_code = cli.main(["collect"])

    assert exit_code == cli.ExitCode.NOT_STARTED
    output = capsys.readouterr()
    assert_nothing_collected(tmp_path, output.out)
    error_output = output.err
    quoted_name = quoting.quote_value(unknown_name)
    assert len(quoted_name) == 100
    assert (
        f"KB_COLLECT_ONLY: entry 2 of 2 {quoted_name} not a source folder;"
        " folders: test, zotero, clipping, gh_archive\n"
    ) in error_output
    assert "\nERROR k_b.cli: forged line" not in error_output


def test_collect_only_quotes_a_name_refused_as_it_was_written(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KB_COLLECT_ONLY", "Test,TLDR_AI")
    monkeypatch.setattr(
        collect_command, "SOURCE_KINDS", declared_kinds(WorkingSource())
    )

    assert cli.main(["collect"]) == cli.ExitCode.NOT_STARTED
    assert "entry 2 of 2 'TLDR_AI' not a source folder" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("collect_only", "refusal"),
    [
        ("", "KB_COLLECT_ONLY: empty"),
        ("  ", "KB_COLLECT_ONLY: empty"),
        ("test,", "KB_COLLECT_ONLY: entry 2 of 2 empty"),
        ("test,,zotero", "KB_COLLECT_ONLY: entry 2 of 3 empty"),
    ],
)
def test_collect_only_refuses_an_empty_value_or_entry(
    monkeypatch, tmp_path, capsys, collect_only, refusal
):
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KB_COLLECT_ONLY", collect_only)
    monkeypatch.setattr(
        collect_command, "SOURCE_KINDS", declared_kinds(WorkingSource())
    )

    assert cli.main(["collect"]) == cli.ExitCode.NOT_STARTED
    output = capsys.readouterr()
    assert refusal in output.err
    assert_nothing_collected(tmp_path, output.out)


@pytest.mark.parametrize(
    ("name", "variable"),
    [
        ("zotero", "KB_ZOTERO_DIR"),
        ("Zotero", "KB_ZOTERO_DIR"),
        ("clipping", "KB_VAULT_DIR"),
        ("gh_archive", "GOOGLE_APPLICATION_CREDENTIALS"),
    ],
)
def test_collect_only_refuses_a_source_chosen_without_its_variable(
    monkeypatch, tmp_path, capsys, name, variable
):
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KB_COLLECT_ONLY", f"test,{name}")
    monkeypatch.setattr(
        collect_command, "SOURCE_KINDS", declared_kinds(WorkingSource())
    )

    assert cli.main(["collect"]) == cli.ExitCode.NOT_STARTED
    output = capsys.readouterr()
    assert f"KB_COLLECT_ONLY: entry 2 of 2 '{name}' chosen, {variable} not set" in (
        output.err
    )
    assert_nothing_collected(tmp_path, output.out)


def test_collect_fails_closed_with_a_clear_message_on_a_corrupted_state(
    monkeypatch, tmp_path, capsys
):
    state_path = tmp_path / "collection_state.json"
    state_path.write_text('{"working": ', encoding="utf-8")
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        collect_command, "SOURCE_KINDS", collected_kinds(WorkingSource())
    )

    exit_code = cli.main(["collect"])

    assert exit_code == 2
    error_output = capsys.readouterr().err
    assert "collection_state.json" in error_output
    assert "Traceback" not in error_output
    assert not (tmp_path / "raw" / "automated_watch").exists()
    assert state_path.read_text(encoding="utf-8") == '{"working": '


def test_collect_prints_nothing_but_the_error_on_a_corrupted_state(
    monkeypatch, tmp_path, capsys
):
    state_path = tmp_path / "collection_state.json"
    state_path.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(collect_command, "SOURCE_KINDS", collected_kinds())

    assert cli.main(["collect"]) == 2
    assert capsys.readouterr().out == ""


@pytest.mark.skipif(
    os.geteuid() == 0, reason="root reads a file whatever its permissions"
)
def test_collect_fails_closed_with_a_clear_message_on_an_unreadable_state(
    monkeypatch, tmp_path, capsys
):
    state_path = tmp_path / "collection_state.json"
    state_path.write_text("{}", encoding="utf-8")
    state_path.chmod(0o000)
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        collect_command, "SOURCE_KINDS", collected_kinds(WorkingSource())
    )

    try:
        exit_code = cli.main(["collect"])
    finally:
        state_path.chmod(0o600)

    assert exit_code == 2
    error_output = capsys.readouterr().err
    assert "collection_state.json" in error_output
    assert "permission" in error_output.lower()
    assert not (tmp_path / "raw" / "automated_watch").exists()


QUOTA_LINE = "KB_GITHUB_TOKEN not set: the GitHub API allows 60 requests per hour instead of 5,000"


def github_kinds():
    """Two kinds of the sources that call the GitHub API, a source each."""
    return tuple(
        SourceKind(name, (WorkingSource(f"{name}_owner_repository"),), None)
        for name in ("github_releases", "github_advisories")
    )


def test_collect_logs_its_start_before_the_first_source_and_its_end_after_the_last(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        collect_command,
        "SOURCE_KINDS",
        declared_kinds(BrokenSource(), WorkingSource()),
    )

    assert cli.main(["collect"]) == cli.ExitCode.PARTIAL_FAILURE
    output = capsys.readouterr()
    error_lines = output.err.splitlines()
    # Not zotero, clipping nor gh_archive: declared, their variable not set.
    [announcement_position] = [
        position
        for position, line in enumerate(error_lines)
        if re.fullmatch(
            rf"{LOG_LINE_DATE} INFO k_b\.cli: collection started: 2 sources selected",
            line,
        )
    ]
    [failure_position] = [
        position
        for position, line in enumerate(error_lines)
        if "collection failed for source 'broken'" in line
    ]
    [end_position] = [
        position
        for position, line in enumerate(error_lines)
        if re.fullmatch(
            rf"{LOG_LINE_DATE} INFO k_b\.cli: collection finished in \d+ s", line
        )
    ]
    assert announcement_position < failure_position < end_position
    assert "source 'working' collected, raw files written: 1" in output.err
    assert "collection started:" not in output.out
    assert "broken: failed (ConnectionError)\n" in output.out


@pytest.mark.parametrize("token", [None, ""])
def test_collect_says_once_without_the_github_token_that_github_allows_60_requests(
    monkeypatch, tmp_path, capsys, token
):
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    if token is not None:
        monkeypatch.setenv("KB_GITHUB_TOKEN", token)
    monkeypatch.setattr(collect_command, "SOURCE_KINDS", github_kinds())

    assert cli.main(["collect"]) == 0
    [quota_line] = [
        line
        for line in capsys.readouterr().err.splitlines()
        if "60 requests per hour" in line
    ]
    assert re.fullmatch(
        rf"{LOG_LINE_DATE} WARNING k_b\.cli: {re.escape(QUOTA_LINE)}", quota_line
    )


def test_collect_says_nothing_of_the_github_token_for_the_search_alone(
    monkeypatch, tmp_path, capsys
):
    # The ranking calls the search, which has its own quota, apart from the 60 per hour.
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        collect_command,
        "SOURCE_KINDS",
        (
            SourceKind(
                "github_most_starred", (WorkingSource("github_most_starred"),), None
            ),
        ),
    )

    assert cli.main(["collect"]) == 0
    error_output = capsys.readouterr().err
    assert "INFO k_b.cli: collection started: 1 source selected\n" in error_output
    assert "KB_GITHUB_TOKEN" not in error_output


def test_collect_says_nothing_of_the_github_token_when_it_is_set(
    monkeypatch, tmp_path, capsys
):
    token_value = "github_pat_" + "x" * 30
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KB_GITHUB_TOKEN", token_value)
    monkeypatch.setattr(collect_command, "SOURCE_KINDS", github_kinds())

    assert cli.main(["collect"]) == 0
    output = capsys.readouterr()
    log_file_text = (tmp_path / "logs" / "kb.log").read_text("utf-8")
    assert "collection started:" in log_file_text
    for text in (output.out, output.err, log_file_text):
        assert "KB_GITHUB_TOKEN" not in text
        assert token_value not in text


def test_collect_only_without_a_github_source_says_nothing_of_the_github_token(
    monkeypatch, tmp_path, capsys, zotero_dir
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (tmp_path / "vault" / "Clippings").mkdir(parents=True)
    monkeypatch.setenv("KB_DATA_DIR", str(data_dir))
    monkeypatch.setenv("KB_ZOTERO_DIR", str(zotero_dir))
    monkeypatch.setenv("KB_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("KB_COLLECT_ONLY", "zotero,clipping")
    # The GitHub kinds are declared: only KB_COLLECT_ONLY leaves them out.
    monkeypatch.setattr(
        collect_command, "SOURCE_KINDS", github_kinds() + declared_kinds()
    )

    assert cli.main(["collect"]) == 0
    error_output = capsys.readouterr().err
    assert "INFO k_b.cli: collection started: 2 sources selected\n" in error_output
    assert "KB_GITHUB_TOKEN" not in error_output
    assert "60 requests per hour" not in error_output
