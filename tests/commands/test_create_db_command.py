"""kb create-db: the database created once, in the data directory."""

import os

import pytest

from k_b import cli


def test_create_db_creates_the_database_in_the_data_directory(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))

    exit_code = cli.main(["create-db"])

    assert exit_code == 0
    assert (tmp_path / "k-b.sqlite").is_file()
    assert "k-b.sqlite" in capsys.readouterr().out


def test_create_db_run_again_refuses_and_keeps_the_database(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    cli.main(["create-db"])
    database_content = (tmp_path / "k-b.sqlite").read_bytes()

    exit_code = cli.main(["create-db"])

    assert exit_code == 2
    assert "already exists" in capsys.readouterr().err
    assert (tmp_path / "k-b.sqlite").read_bytes() == database_content


def test_create_db_refuses_to_run_without_a_data_directory(monkeypatch, capsys):
    monkeypatch.delenv("KB_DATA_DIR", raising=False)

    assert cli.main(["create-db"]) == 2
    assert "KB_DATA_DIR" in capsys.readouterr().err


@pytest.mark.skipif(
    os.geteuid() == 0, reason="root writes a file whatever its permissions"
)
def test_create_db_reports_a_database_that_cannot_be_written_and_exits_1(
    monkeypatch, tmp_path, capsys
):
    read_only_dir = tmp_path / "read-only"
    # The log folder stays writable: only the database cannot be written.
    (read_only_dir / "logs").mkdir(parents=True)
    read_only_dir.chmod(0o500)
    monkeypatch.setenv("KB_DATA_DIR", str(read_only_dir))

    try:
        exit_code = cli.main(["create-db"])
    finally:
        read_only_dir.chmod(0o700)

    assert exit_code == 1
    assert "ERROR k_b.cli: " in capsys.readouterr().err
