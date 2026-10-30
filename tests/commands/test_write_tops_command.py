"""kb write-tops: the sheets of the tops written, the rankings they supersede deleted."""

import re
import signal

import pytest
from commands.cli_helpers import (
    LOG_LINE_DATE,
    list_hidden_files,
    list_sheets,
)
from post.test_top_sheets import TEST_TOPS, write_ranking

from k_b import cli
from k_b.commands import (
    write_tops_command,
)
from k_b.post import vault_files


@pytest.fixture
def test_tops(monkeypatch):
    """The tops of kb write-tops, read with the size of the recorded responses: 3 each."""
    monkeypatch.setattr(write_tops_command, "TOPS", TEST_TOPS)


def write_rankings_of_each_top(data_dir, *collection_names):
    return {
        top.name: [write_ranking(data_dir, top, name) for name in collection_names]
        for top in TEST_TOPS
    }


def test_write_tops_writes_the_sheets_of_each_top_then_deletes_the_older_rankings(
    data_and_vault_dirs, test_tops, capsys
):
    data_dir, vault_dir = data_and_vault_dirs
    rankings = write_rankings_of_each_top(
        data_dir, "2026-10-05T093000Z", "2026-10-12T093000Z"
    )

    exit_code = cli.main(["write-tops"])

    assert exit_code == 0
    assert list_sheets(vault_dir) == [
        "veille/tops/most_forked/2026-10-05.md",
        "veille/tops/most_forked/2026-10-12.md",
        "veille/tops/most_starred/2026-10-05.md",
        "veille/tops/most_starred/2026-10-12.md",
    ]
    for older, last in rankings.values():
        assert (older.exists(), last.exists()) == (False, True)
    output = capsys.readouterr()
    assert output.out == (
        "\nmost_starred: ok, 2 sheets written\n"
        "most_forked: ok, 2 sheets written\n"
        "Older raw files deleted: 2\n"
        "Sheets vault path: veille/tops/\n\n"
    )
    for line in (
        "write-tops started: 2 tops expected",
        r"write-tops finished in \d+ s",
    ):
        assert re.search(
            rf"^{LOG_LINE_DATE} INFO k_b\.cli: {line}$", output.err, re.MULTILINE
        )


def test_write_tops_again_writes_nothing_new(data_and_vault_dirs, test_tops, capsys):
    data_dir, _ = data_and_vault_dirs
    write_rankings_of_each_top(data_dir, "2026-10-05T093000Z")
    cli.main(["write-tops"])
    capsys.readouterr()

    assert cli.main(["write-tops"]) == 0
    assert capsys.readouterr().out.startswith(
        "\nmost_starred: ok, 0 sheets written, the sheet of the day already exists\n"
    )


def test_write_tops_without_ranking_says_so_and_succeeds(
    data_and_vault_dirs, test_tops, capsys
):
    _, vault_dir = data_and_vault_dirs

    assert cli.main(["write-tops"]) == 0
    assert capsys.readouterr().out == (
        "\nmost_starred: no ranking\n"
        "most_forked: no ranking\n"
        "Older raw files deleted: 0\n"
        "Sheets vault path: veille/tops/\n\n"
    )
    assert list_sheets(vault_dir) == []


@pytest.mark.parametrize("vault_dir_variable", [None, ""])
def test_write_tops_without_kb_vault_dir_runs_nothing_and_exits_2(
    monkeypatch, data_and_vault_dirs, test_tops, capsys, vault_dir_variable
):
    data_dir, _ = data_and_vault_dirs
    if vault_dir_variable is None:
        monkeypatch.delenv("KB_VAULT_DIR")
    else:
        monkeypatch.setenv("KB_VAULT_DIR", vault_dir_variable)
    rankings = write_rankings_of_each_top(
        data_dir, "2026-10-05T093000Z", "2026-10-12T093000Z"
    )

    assert cli.main(["write-tops"]) == cli.ExitCode.NOT_STARTED
    assert all(path.exists() for paths in rankings.values() for path in paths)
    log_file_text = (data_dir / "logs" / "kb.log").read_text("utf-8")
    assert re.search(
        rf"^{LOG_LINE_DATE} ERROR k_b\.cli: KB_VAULT_DIR not set; the sheets of the tops"
        " are written in the vault$",
        log_file_text,
        re.MULTILINE,
    )
    assert "write-tops started" not in log_file_text
    assert capsys.readouterr().out == ""


def test_write_tops_with_a_vault_that_does_not_exist_creates_no_folder_and_exits_2(
    monkeypatch, data_and_vault_dirs, test_tops, tmp_path, capsys
):
    data_dir, _ = data_and_vault_dirs
    absent_vault = tmp_path / "typo"
    monkeypatch.setenv("KB_VAULT_DIR", str(absent_vault))
    write_rankings_of_each_top(data_dir, "2026-10-05T093000Z")

    assert cli.main(["write-tops"]) == cli.ExitCode.NOT_STARTED
    assert not absent_vault.exists()
    error_output = capsys.readouterr().err
    assert "KB_VAULT_DIR is not an existing directory; no folder is created" in (
        error_output
    )
    assert str(absent_vault) not in error_output


def test_a_sheet_not_written_keeps_its_rankings_exits_4_and_the_other_top_is_written(
    monkeypatch, data_and_vault_dirs, test_tops, capsys
):
    data_dir, vault_dir = data_and_vault_dirs
    rankings = write_rankings_of_each_top(
        data_dir, "2026-10-05T093000Z", "2026-10-12T093000Z"
    )
    write_file_atomically = vault_files.write_file_atomically

    def refuse_the_most_starred(path, content):
        if "most_starred" in path.parts:
            raise PermissionError(13, "Permission denied", str(path))
        write_file_atomically(path, content)

    monkeypatch.setattr(vault_files, "write_file_atomically", refuse_the_most_starred)

    assert cli.main(["write-tops"]) == cli.ExitCode.PARTIAL_FAILURE
    assert all(path.exists() for path in rankings["most_starred"])
    assert [path.exists() for path in rankings["most_forked"]] == [False, True]
    assert list_sheets(vault_dir) == [
        "veille/tops/most_forked/2026-10-05.md",
        "veille/tops/most_forked/2026-10-12.md",
    ]
    output = capsys.readouterr()
    assert (
        "top sheets failed: most_starred: PermissionError (Permission denied);"
        " run kb write-tops again"
    ) in output.err
    assert str(vault_dir) not in output.err
    # The type alone, never the message, which names the vault; no count of the raw files
    # deleted, as the top stopped may have deleted some.
    assert output.out == (
        "\nmost_starred: failed (PermissionError)\n"
        "most_forked: ok, 2 sheets written\n"
        "Sheets vault path: veille/tops/\n\n"
    )


def test_an_unreadable_ranking_is_kept_and_write_tops_exits_4_with_the_sheets_written(
    data_and_vault_dirs, test_tops, capsys
):
    """Fail closed: a ranking kept unread is never a success, and the sheets written are kept, so
    the code is the one of a partial failure."""
    data_dir, vault_dir = data_and_vault_dirs
    rankings = write_rankings_of_each_top(data_dir, "2026-10-05T093000Z")
    unreadable = write_ranking(data_dir, TEST_TOPS[0], "2026-10-12T093000Z", b"[]")

    assert cli.main(["write-tops"]) == cli.ExitCode.PARTIAL_FAILURE
    assert unreadable.exists()
    assert rankings["most_starred"][0].exists()
    assert len(list_sheets(vault_dir)) == 2
    # The sheet written does not hide the ranking kept unread: the top failed.
    assert capsys.readouterr().out.startswith(
        "\nmost_starred: failed (ranking_unreadable)\nmost_forked: ok, 1 sheet written\n"
    )


def test_a_sigterm_while_a_sheet_is_written_stops_write_tops_and_deletes_no_ranking(
    monkeypatch, data_and_vault_dirs, test_tops, sigterm_handler_of_the_test
):
    data_dir, vault_dir = data_and_vault_dirs
    rankings = write_rankings_of_each_top(
        data_dir, "2026-10-05T093000Z", "2026-10-12T093000Z"
    )
    write_file_atomically = vault_files.write_file_atomically

    def write_then_receive_sigterm(path, content):
        write_file_atomically(path, content)
        signal.raise_signal(signal.SIGTERM)

    monkeypatch.setattr(
        vault_files, "write_file_atomically", write_then_receive_sigterm
    )

    exit_code = cli.main(["write-tops"])

    assert exit_code == cli.ExitCode.STOPPED_BY_SIGTERM
    assert all(path.exists() for paths in rankings.values() for path in paths)
    assert list_sheets(vault_dir) == ["veille/tops/most_starred/2026-10-05.md"]
    assert list_hidden_files(vault_dir) == []
    assert signal.getsignal(signal.SIGTERM) is sigterm_handler_of_the_test


def test_kb_import_writes_nothing_in_the_vault(data_and_vault_dirs, test_tops):
    data_dir, vault_dir = data_and_vault_dirs
    cli.main(["create-db"])
    rankings = write_rankings_of_each_top(
        data_dir, "2026-10-05T093000Z", "2026-10-12T093000Z"
    )

    assert cli.main(["import"]) == 0
    assert list(vault_dir.iterdir()) == []
    assert all(path.exists() for paths in rankings.values() for path in paths)


def test_kb_help_lists_write_tops(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])

    assert re.search(
        r"^    write-tops +write the sheet of each top ranking",
        capsys.readouterr().out,
        re.MULTILINE,
    )


def test_a_folder_of_a_top_linked_elsewhere_is_refused_exits_4_and_the_other_top_is_written(
    data_and_vault_dirs, test_tops, tmp_path, capsys
):
    data_dir, vault_dir = data_and_vault_dirs
    rankings = write_rankings_of_each_top(
        data_dir, "2026-10-05T093000Z", "2026-10-12T093000Z"
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (vault_dir / "veille" / "tops").mkdir(parents=True)
    (vault_dir / "veille" / "tops" / "most_starred").symlink_to(
        elsewhere, target_is_directory=True
    )

    assert cli.main(["write-tops"]) == cli.ExitCode.PARTIAL_FAILURE
    assert all(path.exists() for path in rankings["most_starred"])
    assert list(elsewhere.iterdir()) == []
    assert list_sheets(vault_dir / "veille" / "tops" / "most_forked") == [
        "2026-10-05.md",
        "2026-10-12.md",
    ]
    error_output = capsys.readouterr().err
    assert (
        "top sheets failed: most_starred: vault_link_refused: veille/tops/most_starred is a"
        " symbolic link\n"
    ) in error_output
    assert str(vault_dir) not in error_output


def test_write_tops_exits_1_when_both_tops_are_refused(
    data_and_vault_dirs, test_tops, tmp_path
):
    data_dir, vault_dir = data_and_vault_dirs
    rankings = write_rankings_of_each_top(data_dir, "2026-10-05T093000Z")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (vault_dir / "veille").symlink_to(elsewhere, target_is_directory=True)

    assert cli.main(["write-tops"]) == cli.ExitCode.FAILED
    assert all(path.exists() for paths in rankings.values() for path in paths)
    assert list(elsewhere.iterdir()) == []


def test_write_tops_exits_1_when_its_only_rankings_are_unreadable(
    data_and_vault_dirs, test_tops
):
    data_dir, vault_dir = data_and_vault_dirs
    for top in TEST_TOPS:
        write_ranking(data_dir, top, "2026-10-05T093000Z", b"[]")

    assert cli.main(["write-tops"]) == cli.ExitCode.FAILED
    assert list_sheets(vault_dir) == []


def test_a_top_without_ranking_succeeds_beside_a_top_refused_so_write_tops_exits_4(
    data_and_vault_dirs, test_tops, tmp_path
):
    """A top without ranking ran to its end: as a source with nothing new for kb collect."""
    data_dir, vault_dir = data_and_vault_dirs
    write_ranking(data_dir, TEST_TOPS[0], "2026-10-05T093000Z")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (vault_dir / "veille" / "tops").mkdir(parents=True)
    (vault_dir / "veille" / "tops" / TEST_TOPS[0].name).symlink_to(
        elsewhere, target_is_directory=True
    )

    assert cli.main(["write-tops"]) == cli.ExitCode.PARTIAL_FAILURE
