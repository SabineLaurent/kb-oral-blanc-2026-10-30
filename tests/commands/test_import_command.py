"""kb import: the raw items written into the base, the purge and the import log."""

import re
import shutil
import sqlite3
from datetime import UTC, datetime

from commands.cli_helpers import LOG_LINE_DATE
from etl.test_transform import FIXTURES_DIR

from k_b import cli
from k_b.commands import (
    import_command,
)
from k_b.database.writing import KindWithoutTableError, UnknownSourceError
from k_b.etl import pipeline


def test_import_writes_the_raw_items_into_the_database(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    cli.main(["create-db"])
    capsys.readouterr()

    exit_code = cli.main(["import"])

    assert exit_code == 0
    output = capsys.readouterr()
    (log_path,) = tmp_path.glob("import-log-*.jsonl")
    assert re.fullmatch(r"import-log-\d{4}-\d{2}-\d{2}T\d{6}Z\.jsonl", log_path.name)
    assert output.out == (
        "\nimport: 0 raw read, 0 written, 0 trend rows, 0 left out, 0 rejected, 0 withdrawn\n\n"
    )
    for line in (
        "import started",
        r"import finished in \d+ s",
        re.escape(f"import log kept: '{log_path.name}'"),
    ):
        assert re.search(
            rf"^{LOG_LINE_DATE} INFO k_b\.cli: {line}$", output.err, re.MULTILINE
        )


def test_import_writes_the_raw_rankings_of_gh_archive_into_the_trend_table(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    cli.main(["create-db"])
    source_dir = tmp_path / "raw" / "automated_watch" / "gh_archive"
    source_dir.mkdir(parents=True)
    shutil.copyfile(
        FIXTURES_DIR / "gh_archive_extract.json", source_dir / "2026-10-11T153130Z.json"
    )

    exit_code = cli.main(["import"])

    assert exit_code == 0
    assert capsys.readouterr().out.endswith(
        "\nimport: 1 raw read, 0 written, 5 trend rows, 0 left out, 0 rejected, 0 withdrawn\n\n"
    )
    connection = sqlite3.connect(tmp_path / "k-b.sqlite")
    assert connection.execute("SELECT count(*) FROM trend").fetchone() == (5,)
    connection.close()


def write_state_catalogs(data_dir):
    """Two extracts of Zotero and two catalogs of CISA KEV, not imported yet."""
    paths = []
    for collection in ("2026-10-05T093000Z", "2026-10-06T093000Z"):
        extract_path = (
            data_dir / "raw" / "selected_library" / "zotero" / f"{collection}.json"
        )
        extract_path.parent.mkdir(parents=True, exist_ok=True)
        extract_path.write_bytes(b"[]\n")
        catalog_dir = data_dir / "raw" / "automated_watch" / "cisa_kev"
        catalog_dir.mkdir(parents=True, exist_ok=True)
        catalog_path = catalog_dir / f"{collection}.csv"
        shutil.copyfile(FIXTURES_DIR / "cisa_kev_extract.csv", catalog_path)
        paths += [extract_path, catalog_path]
    return paths


def test_import_keeps_only_the_last_extract_and_catalog_imported(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    cli.main(["create-db"])
    older_extract, older_catalog, last_extract, last_catalog = write_state_catalogs(
        tmp_path
    )

    exit_code = cli.main(["import"])

    assert exit_code == 0
    assert "raw purge: 2 superseded files deleted" in capsys.readouterr().err
    assert (older_extract.exists(), older_catalog.exists()) == (False, False)
    assert (last_extract.exists(), last_catalog.exists()) == (True, True)


def test_a_failed_import_deletes_no_raw_file(monkeypatch, tmp_path):
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    cli.main(["create-db"])
    paths = write_state_catalogs(tmp_path)

    def fail_to_import(*arguments):
        raise sqlite3.OperationalError("database is locked")

    def refuse_to_purge(*arguments):
        raise AssertionError("purged after a failed import")

    monkeypatch.setattr(pipeline, "import_items", fail_to_import)
    monkeypatch.setattr(pipeline, "delete_superseded_raw_files", refuse_to_purge)

    assert cli.main(["import"]) == 1
    assert all(path.exists() for path in paths)


def test_a_failed_purge_without_a_reason_names_its_type_only(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    cli.main(["create-db"])
    write_state_catalogs(tmp_path)

    def fail_to_delete(store, key):
        raise OSError("raised by code, without an errno")

    monkeypatch.setattr(pipeline.RawStore, "delete", fail_to_delete)

    assert cli.main(["import"]) == 1
    error_output = capsys.readouterr().err
    assert "import committed, raw purge failed: OSError; run kb import again" in (
        error_output
    )
    assert "None" not in error_output


def test_a_failed_purge_keeps_the_import_exits_1_and_the_next_import_purges(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    cli.main(["create-db"])
    older_extract, older_catalog, _, _ = write_state_catalogs(tmp_path)

    def fail_to_delete(store, key):
        raise PermissionError(13, "Permission denied", str(tmp_path / key))

    with monkeypatch.context() as patch:
        patch.setattr(pipeline.RawStore, "delete", fail_to_delete)
        patch.setattr(import_command, "datetime", FrozenDatetime)
        assert cli.main(["import"]) == 1

    error_output = capsys.readouterr().err
    assert (
        "import committed, raw purge failed: PermissionError (Permission denied)"
        in (error_output)
    )
    assert "2026-10-05T093000Z" not in error_output
    assert older_extract.exists()
    connection = sqlite3.connect(tmp_path / "k-b.sqlite")
    assert connection.execute("SELECT count(*) FROM raw_data").fetchone() == (4,)
    connection.close()

    assert cli.main(["import"]) == 0
    assert (older_extract.exists(), older_catalog.exists()) == (False, False)


def test_a_failed_purge_leaves_the_summary_of_the_import_it_committed(
    monkeypatch, tmp_path, capsys
):
    """The summary is printed before the purge, so that an import that committed says what it
    wrote even when the purge fails."""
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    cli.main(["create-db"])
    write_state_catalogs(tmp_path)
    capsys.readouterr()

    def fail_to_delete(store, key):
        raise PermissionError(13, "Permission denied", str(tmp_path / key))

    monkeypatch.setattr(pipeline.RawStore, "delete", fail_to_delete)

    assert cli.main(["import"]) == 1
    output = capsys.readouterr()
    blank_line, summary_line, last_line = output.out.splitlines()
    assert blank_line == last_line == ""
    assert summary_line.startswith("import: 4 raw read, ")
    assert "import committed, raw purge failed: PermissionError" in output.err


def test_a_purge_stopped_by_ctrl_c_still_prints_the_summary(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    cli.main(["create-db"])
    capsys.readouterr()

    def interrupt(data_dir, folder_keys):
        raise KeyboardInterrupt

    monkeypatch.setattr(import_command, "purge_superseded_raw", interrupt)

    assert cli.main(["import"]) == cli.ExitCode.STOPPED_BY_SIGINT
    assert capsys.readouterr().out == (
        "\nimport: 0 raw read, 0 written, 0 trend rows, 0 left out, 0 rejected, 0 withdrawn\n\n"
    )


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 12, 15, 49, 3, 250000, tzinfo=UTC)


def test_import_refuses_to_rewrite_the_log_of_its_second_and_exits_2(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    cli.main(["create-db"])
    monkeypatch.setattr(import_command, "datetime", FrozenDatetime)
    log_path = tmp_path / "import-log-2026-10-12T154903Z.jsonl"
    log_path.write_text("earlier import\n")

    exit_code = cli.main(["import"])

    assert exit_code == 2
    assert str(log_path) in capsys.readouterr().err
    assert log_path.read_text() == "earlier import\n"


def test_import_refuses_a_missing_database_and_does_not_create_it(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))

    exit_code = cli.main(["import"])

    assert exit_code == 2
    assert "kb create-db" in capsys.readouterr().err
    assert not (tmp_path / "k-b.sqlite").exists()


def test_import_reports_a_failed_import_and_exits_1(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    cli.main(["create-db"])

    def fail_to_import(
        reading,
        trend_reading,
        database_path,
        log_path,
        imported_at,
        tracked_repositories,
    ):
        raise sqlite3.IntegrityError("CHECK constraint failed: publication")

    monkeypatch.setattr(pipeline, "import_items", fail_to_import)

    exit_code = cli.main(["import"])

    assert exit_code == 1
    assert (
        "ERROR k_b.cli: IntegrityError: CHECK constraint failed: publication"
        in capsys.readouterr().err
    )


def test_import_reports_a_source_missing_from_the_database_and_exits_1(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    cli.main(["create-db"])

    def fail_to_import(
        reading,
        trend_reading,
        database_path,
        log_path,
        imported_at,
        tracked_repositories,
    ):
        raise UnknownSourceError("source_code not in the source table: cisa_kev")

    monkeypatch.setattr(pipeline, "import_items", fail_to_import)

    exit_code = cli.main(["import"])

    assert exit_code == 1
    assert (
        "ERROR k_b.cli: UnknownSourceError: source_code not in the source table: cisa_kev"
        in capsys.readouterr().err
    )


def test_import_reports_a_kind_without_table_and_exits_1(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    cli.main(["create-db"])

    def fail_to_import(
        reading,
        trend_reading,
        database_path,
        log_path,
        imported_at,
        tracked_repositories,
    ):
        raise KindWithoutTableError(
            "kind without a table in the base: newsletter_article"
        )

    monkeypatch.setattr(pipeline, "import_items", fail_to_import)

    exit_code = cli.main(["import"])

    assert exit_code == 1
    assert (
        "ERROR k_b.cli: KindWithoutTableError: kind without a table in the base: newsletter_article"
        in capsys.readouterr().err
    )
