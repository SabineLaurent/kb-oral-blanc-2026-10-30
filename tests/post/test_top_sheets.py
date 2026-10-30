import json
import logging
from functools import partial
from pathlib import Path

import pytest

from k_b.commands.sigterm import StoppedBySigtermError
from k_b.core.config import DIGEST_SUBJECTS, TOPS_DIR_NAME, WATCH_DIR_NAME
from k_b.etl.raw_store import RawStore
from k_b.etl.sources.github import most_forked as github_most_forked
from k_b.etl.sources.github import most_starred as github_most_starred
from k_b.etl.tops import Top
from k_b.post import top_sheets, vault_files
from k_b.post.top_sheets import TopSheetsOutcome, write_top_sheets
from k_b.post.vault_files import VaultEntryRefusedError

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
STARRED_RESPONSE = (FIXTURES_DIR / "github_most_starred_extract.json").read_bytes()
FORKED_RESPONSE = (FIXTURES_DIR / "github_most_forked_extract.json").read_bytes()
# The tops of kb write-tops, read with the size of the recorded responses: 3 repositories each.
STARRED_TOP = Top(
    "most_starred",
    "raw/automated_watch/github_most_starred",
    partial(github_most_starred.read_top_entries, expected_size=3),
    "Étoiles",
    "dépôts GitHub les plus étoilés",
)
FORKED_TOP = Top(
    "most_forked",
    "raw/automated_watch/github_most_forked",
    partial(github_most_forked.read_top_entries, expected_size=3),
    "Forks",
    "dépôts GitHub les plus forkés",
)
TEST_TOPS = (STARRED_TOP, FORKED_TOP)
RECORDED_RESPONSE_BY_TOP = {
    "most_starred": STARRED_RESPONSE,
    "most_forked": FORKED_RESPONSE,
}


def write_ranking(data_dir, top, collection_name, content=None):
    """A ranking of top in raw/, as kb collect writes it."""
    path = data_dir / top.folder_key / f"{collection_name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(RECORDED_RESPONSE_BY_TOP[top.name] if content is None else content)
    return path


def response_with(response, **changes):
    """The recorded response, its first repository changed."""
    changed = json.loads(response)
    changed["items"][0].update(changes)
    return json.dumps(changed).encode()


def find_sheet(vault_dir, top, day):
    return vault_dir / "veille" / "tops" / top.name / f"{day}.md"


def list_files(directory):
    return sorted(
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file()
    )


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path / "data"


@pytest.fixture
def vault_dir(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


def test_a_ranking_gives_the_sheet_of_its_day_by_rank(data_dir, vault_dir):
    write_ranking(data_dir, FORKED_TOP, "2026-10-05T093000Z")

    outcome = write_top_sheets(RawStore(data_dir), vault_dir, FORKED_TOP)

    assert outcome == TopSheetsOutcome(
        written_count=1, skipped_count=0, deleted_count=0, unreadable_count=0
    )
    assert find_sheet(vault_dir, FORKED_TOP, "2026-10-05").read_text("utf-8") == (
        "---\n"
        "ranking: most_forked\n"
        "collected_at: 2026-10-05T09:30:00Z\n"
        "repositories: 3\n"
        "---\n"
        "\n"
        "# Top 3 des dépôts GitHub les plus forkés, relevé du 2026-10-05\n"
        "\n"
        "| Rang | Dépôt | Forks | Description |\n"
        "|---|---|---|---|\n"
        "| 1 | [firstcontributions/first-contributions]"
        "(https://github.com/firstcontributions/first-contributions) | 111263 |"
        " 🚀✨ Help beginners to contribute to open source projects |\n"
        "| 2 | [ultraworkers/claw-code](https://github.com/ultraworkers/claw-code) | 107886 |"
        " An agent-managed museum exhibit, built in Rust with Gajae-Code / LazyCodex —"
        " developed and maintained with no human intervention. |\n"
        "| 3 | [TheOdinProject/css-exercises](https://github.com/TheOdinProject/css-exercises)"
        " | 94375 |  |\n"
    )


def test_the_sheet_of_the_most_starred_counts_their_stars(data_dir, vault_dir):
    write_ranking(data_dir, STARRED_TOP, "2026-10-05T093000Z")

    write_top_sheets(RawStore(data_dir), vault_dir, STARRED_TOP)

    lines = (
        find_sheet(vault_dir, STARRED_TOP, "2026-10-05").read_text("utf-8").splitlines()
    )
    assert lines[6:9] == [
        "# Top 3 des dépôts GitHub les plus étoilés, relevé du 2026-10-05",
        "",
        "| Rang | Dépôt | Étoiles | Description |",
    ]
    assert lines[10] == (
        "| 1 | [codecrafters-io/build-your-own-x]"
        "(https://github.com/codecrafters-io/build-your-own-x) | 551156 |"
        " Master programming by recreating your favorite technologies from scratch. |"
    )


def test_the_same_ranking_gives_the_same_bytes(tmp_path):
    sheets = []
    for run in ("first", "second"):
        data_dir, vault_dir = tmp_path / run / "data", tmp_path / run / "vault"
        vault_dir.mkdir(parents=True)
        write_ranking(data_dir, STARRED_TOP, "2026-10-05T093000Z")
        write_top_sheets(RawStore(data_dir), vault_dir, STARRED_TOP)
        sheets.append(find_sheet(vault_dir, STARRED_TOP, "2026-10-05").read_bytes())

    assert sheets[0] == sheets[1]


def test_the_url_is_built_from_the_name_never_read_in_html_url(data_dir, vault_dir):
    write_ranking(
        data_dir,
        STARRED_TOP,
        "2026-10-05T093000Z",
        response_with(STARRED_RESPONSE, html_url="https://github.com/someone/else"),
    )

    write_top_sheets(RawStore(data_dir), vault_dir, STARRED_TOP)

    sheet = find_sheet(vault_dir, STARRED_TOP, "2026-10-05").read_text("utf-8")
    assert "someone/else" not in sheet
    assert "(https://github.com/codecrafters-io/build-your-own-x)" in sheet


def test_each_day_of_collection_gets_its_sheet_and_the_older_rankings_are_deleted(
    data_dir, vault_dir
):
    older = write_ranking(data_dir, STARRED_TOP, "2026-10-05T093000Z")
    middle = write_ranking(data_dir, STARRED_TOP, "2026-10-06T093000Z")
    last = write_ranking(data_dir, STARRED_TOP, "2026-10-12T093000Z")

    outcome = write_top_sheets(RawStore(data_dir), vault_dir, STARRED_TOP)

    assert outcome == TopSheetsOutcome(
        written_count=3, skipped_count=0, deleted_count=2, unreadable_count=0
    )
    assert list_files(vault_dir) == [
        "veille/tops/most_starred/2026-10-05.md",
        "veille/tops/most_starred/2026-10-06.md",
        "veille/tops/most_starred/2026-10-12.md",
    ]
    assert (older.exists(), middle.exists(), last.exists()) == (False, False, True)


def test_two_rankings_of_a_day_give_one_sheet_the_one_of_the_first(data_dir, vault_dir):
    first = write_ranking(data_dir, STARRED_TOP, "2026-10-05T093000Z")
    second = write_ranking(
        data_dir,
        STARRED_TOP,
        "2026-10-05T153000Z",
        response_with(STARRED_RESPONSE, stargazers_count=999),
    )

    outcome = write_top_sheets(RawStore(data_dir), vault_dir, STARRED_TOP)

    assert outcome == TopSheetsOutcome(
        written_count=1, skipped_count=1, deleted_count=1, unreadable_count=0
    )
    sheet = find_sheet(vault_dir, STARRED_TOP, "2026-10-05").read_text("utf-8")
    assert "collected_at: 2026-10-05T09:30:00Z" in sheet
    assert "| 551156 |" in sheet
    assert (first.exists(), second.exists()) == (False, True)


def test_a_sheet_that_exists_is_never_rewritten(data_dir, vault_dir):
    write_ranking(data_dir, STARRED_TOP, "2026-10-05T093000Z")
    sheet = find_sheet(vault_dir, STARRED_TOP, "2026-10-05")
    sheet.parent.mkdir(parents=True)
    sheet.write_bytes(b"edited by the user\n")

    outcome = write_top_sheets(RawStore(data_dir), vault_dir, STARRED_TOP)

    assert outcome.skipped_count == 1
    assert outcome.written_count == 0
    assert sheet.read_bytes() == b"edited by the user\n"


def test_a_last_ranking_unreadable_keeps_the_last_readable_one(data_dir, vault_dir):
    readable = write_ranking(data_dir, STARRED_TOP, "2026-10-05T093000Z")
    unreadable = write_ranking(data_dir, STARRED_TOP, "2026-10-06T093000Z", b"not json")

    outcome = write_top_sheets(RawStore(data_dir), vault_dir, STARRED_TOP)

    assert outcome == TopSheetsOutcome(
        written_count=1, skipped_count=0, deleted_count=0, unreadable_count=1
    )
    assert (readable.exists(), unreadable.exists()) == (True, True)
    assert list_files(vault_dir) == ["veille/tops/most_starred/2026-10-05.md"]


def test_a_second_ranking_of_a_day_unreadable_is_read_and_kept(data_dir, vault_dir):
    """The sheet of its day exists, but the ranking is read, so that an unreadable one is never
    taken for the last readable."""
    readable = write_ranking(data_dir, STARRED_TOP, "2026-10-05T093000Z")
    write_top_sheets(RawStore(data_dir), vault_dir, STARRED_TOP)
    unreadable = write_ranking(data_dir, STARRED_TOP, "2026-10-05T153000Z", b"{}")

    outcome = write_top_sheets(RawStore(data_dir), vault_dir, STARRED_TOP)

    assert outcome == TopSheetsOutcome(
        written_count=0, skipped_count=1, deleted_count=0, unreadable_count=1
    )
    assert (readable.exists(), unreadable.exists()) == (True, True)


def test_an_unreadable_ranking_is_logged_as_such(data_dir, vault_dir, caplog):
    write_ranking(data_dir, STARRED_TOP, "2026-10-06T093000Z", b"not json")

    with caplog.at_level(logging.INFO, logger=top_sheets.__name__):
        write_top_sheets(RawStore(data_dir), vault_dir, STARRED_TOP)

    assert (
        "most_starred ranking of 2026-10-06: ranking_unreadable, kept: JSONDecodeError"
        in caplog.text
    )


@pytest.mark.parametrize(
    "file_name", ["2026-10-5T093000Z.json", "notes.json", "2026-10-05T093000Z.json.bak"]
)
def test_a_file_not_named_by_a_collection_time_is_kept_and_its_name_never_logged(
    data_dir, vault_dir, caplog, file_name
):
    readable = write_ranking(data_dir, STARRED_TOP, "2026-10-04T093000Z")
    misnamed = readable.parent / file_name
    misnamed.write_bytes(STARRED_RESPONSE)

    with caplog.at_level(logging.INFO, logger=top_sheets.__name__):
        outcome = write_top_sheets(RawStore(data_dir), vault_dir, STARRED_TOP)

    assert outcome.unreadable_count == 1
    assert (readable.exists(), misnamed.exists()) == (True, True)
    assert "most_starred: ranking_name_invalid, a file of its raw folder kept" in (
        caplog.text
    )
    assert file_name not in caplog.text


def test_a_ranking_written_after_the_folder_is_read_is_neither_read_nor_deleted(
    data_dir, vault_dir
):
    older = write_ranking(data_dir, STARRED_TOP, "2026-10-05T093000Z")
    last_listed = write_ranking(data_dir, STARRED_TOP, "2026-10-06T093000Z")

    class CollectionDuringTheListing(RawStore):
        def list_file_keys(self, folder_key):
            keys = super().list_file_keys(folder_key)
            write_ranking(data_dir, STARRED_TOP, "2026-10-07T093000Z")
            return keys

    outcome = write_top_sheets(
        CollectionDuringTheListing(data_dir), vault_dir, STARRED_TOP
    )

    assert outcome.deleted_count == 1
    assert (older.exists(), last_listed.exists()) == (False, True)
    assert (data_dir / STARRED_TOP.folder_key / "2026-10-07T093000Z.json").exists()
    assert not find_sheet(vault_dir, STARRED_TOP, "2026-10-07").exists()


def test_no_ranking_gives_no_sheet(data_dir, vault_dir):
    outcome = write_top_sheets(RawStore(data_dir), vault_dir, STARRED_TOP)

    assert outcome == TopSheetsOutcome(0, 0, 0, 0)
    assert list_files(vault_dir) == []


@pytest.mark.parametrize(
    "error", [PermissionError(13, "Permission denied"), StoppedBySigtermError()]
)
def test_a_sheet_not_written_deletes_no_ranking(
    data_dir, vault_dir, monkeypatch, error
):
    older = write_ranking(data_dir, STARRED_TOP, "2026-10-05T093000Z")
    last = write_ranking(data_dir, STARRED_TOP, "2026-10-06T093000Z")
    written_paths = []

    def write_then_fail(path, content):
        if written_paths:
            raise error
        written_paths.append(path)

    monkeypatch.setattr(vault_files, "write_file_atomically", write_then_fail)

    with pytest.raises(type(error)):
        write_top_sheets(RawStore(data_dir), vault_dir, STARRED_TOP)

    assert (older.exists(), last.exists()) == (True, True)


def test_no_value_of_github_enters_the_path_of_a_sheet(data_dir, vault_dir):
    write_ranking(
        data_dir,
        STARRED_TOP,
        "2026-10-05T093000Z",
        response_with(STARRED_RESPONSE, description="../../../escaped\n../x.md"),
    )

    write_top_sheets(RawStore(data_dir), vault_dir, STARRED_TOP)

    assert list_files(vault_dir.parent) == [
        "data/raw/automated_watch/github_most_starred/2026-10-05T093000Z.json",
        "vault/veille/tops/most_starred/2026-10-05.md",
    ]


def test_a_trapped_description_stays_in_its_cell(data_dir, vault_dir):
    write_ranking(
        data_dir,
        STARRED_TOP,
        "2026-10-05T093000Z",
        response_with(
            STARRED_RESPONSE, description="x |\n---\n# title\n<b>bold</b> \ud800"
        ),
    )

    write_top_sheets(RawStore(data_dir), vault_dir, STARRED_TOP)

    lines = (
        find_sheet(vault_dir, STARRED_TOP, "2026-10-05").read_text("utf-8").splitlines()
    )
    assert lines[10].endswith(" | x \\| --- \\# title \\<b\\>bold\\</b\\> |")
    assert [line for line in lines if line == "---"] == ["---", "---"]


def test_a_character_reference_in_a_description_is_written_as_text(data_dir, vault_dir):
    """CommonMark decodes a named reference (&rlm;) into a bidirectional control: only the & that
    opens a reference is escaped, R&D and a URL with a query stay."""
    write_ranking(
        data_dir,
        STARRED_TOP,
        "2026-10-05T093000Z",
        response_with(
            STARRED_RESPONSE,
            description="droite&rlm;gauche&lrm; sans&zwnj;liaison c&shy;oupure R&D"
            " https://example.invalid/?q=a&page=2 &copy; &amp; &frac12;",
        ),
    )

    write_top_sheets(RawStore(data_dir), vault_dir, STARRED_TOP)

    lines = (
        find_sheet(vault_dir, STARRED_TOP, "2026-10-05").read_text("utf-8").splitlines()
    )
    assert lines[10].endswith(
        " | droite\\&rlm;gauche\\&lrm; sans\\&zwnj;liaison c\\&shy;oupure R&D"
        " https://example.invalid/?q=a&page=2 \\&copy; \\&amp; \\&frac12; |"
    )


def test_tops_is_a_reserved_name_no_subject_of_the_digests_may_take():
    """The digests of a subject are written in veille/<subject>/: a subject tops would write
    them in the folder of the sheets of the tops."""
    assert (WATCH_DIR_NAME, TOPS_DIR_NAME) == ("veille", "tops")
    assert TOPS_DIR_NAME not in {subject.name for subject in DIGEST_SUBJECTS}


@pytest.mark.parametrize(
    "linked", ["veille", "veille/tops", "veille/tops/most_starred"]
)
def test_a_folder_of_the_sheets_that_is_a_symbolic_link_is_refused_before_any_ranking_is_read(
    data_dir, vault_dir, tmp_path, linked
):
    """K-B never follows a symbolic link in the vault."""
    rankings = [
        write_ranking(data_dir, STARRED_TOP, "2026-10-05T093000Z"),
        write_ranking(data_dir, STARRED_TOP, "2026-10-06T093000Z"),
    ]
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link = vault_dir / linked
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(VaultEntryRefusedError) as refusal:
        write_top_sheets(RawStore(data_dir), vault_dir, STARRED_TOP)

    assert str(refusal.value) == f"vault_link_refused: {linked} is a symbolic link"
    assert all(ranking.exists() for ranking in rankings)
    assert list_files(elsewhere) == []


@pytest.mark.parametrize("target_exists", [False, True])
def test_a_sheet_that_is_a_symbolic_link_refuses_its_top_before_any_sheet_is_written(
    data_dir, vault_dir, tmp_path, target_exists
):
    """All or nothing: the sheet of 2026-10-05 is not written either, and a link to an existing
    file is refused too."""
    older = write_ranking(data_dir, STARRED_TOP, "2026-10-05T093000Z")
    last = write_ranking(data_dir, STARRED_TOP, "2026-10-06T093000Z")
    outside = tmp_path / "outside.md"
    if target_exists:
        outside.write_bytes(b"not a sheet\n")
    sheet = find_sheet(vault_dir, STARRED_TOP, "2026-10-06")
    sheet.parent.mkdir(parents=True)
    sheet.symlink_to(outside)

    with pytest.raises(
        VaultEntryRefusedError,
        match="^vault_link_refused: veille/tops/most_starred/2026-10-06.md is a symbolic link$",
    ):
        write_top_sheets(RawStore(data_dir), vault_dir, STARRED_TOP)

    assert (older.exists(), last.exists()) == (True, True)
    assert not find_sheet(vault_dir, STARRED_TOP, "2026-10-05").exists()
    assert sheet.is_symlink()
    assert outside.exists() is target_exists
    if target_exists:
        assert outside.read_bytes() == b"not a sheet\n"


@pytest.mark.parametrize(
    ("replaced_after_day", "written_days"),
    [
        # Before the second sheet: write_sheet checks its folders again.
        ("2026-10-05", ["2026-10-05.md"]),
        # After the last sheet: the folders are checked again before the deletion.
        ("2026-10-06", ["2026-10-05.md", "2026-10-06.md"]),
    ],
)
def test_a_folder_replaced_by_a_link_while_the_sheets_are_written_deletes_no_ranking(
    data_dir, vault_dir, tmp_path, monkeypatch, replaced_after_day, written_days
):
    older = write_ranking(data_dir, STARRED_TOP, "2026-10-05T093000Z")
    last = write_ranking(data_dir, STARRED_TOP, "2026-10-06T093000Z")
    sheets_dir = find_sheet(vault_dir, STARRED_TOP, "2026-10-05").parent
    moved = tmp_path / "moved"
    write_file_atomically = vault_files.write_file_atomically

    def write_then_replace_the_folder(path, content):
        write_file_atomically(path, content)
        if path.name == f"{replaced_after_day}.md":
            sheets_dir.rename(moved)
            sheets_dir.symlink_to(moved, target_is_directory=True)

    monkeypatch.setattr(
        vault_files, "write_file_atomically", write_then_replace_the_folder
    )

    with pytest.raises(
        VaultEntryRefusedError,
        match="^vault_link_refused: veille/tops/most_starred is a symbolic link$",
    ):
        write_top_sheets(RawStore(data_dir), vault_dir, STARRED_TOP)

    assert (older.exists(), last.exists()) == (True, True)
    assert list_files(moved) == written_days


@pytest.mark.parametrize(
    ("written_name", "replaced_name", "sheets_written"),
    [
        # During the loop: the sheet of 2026-10-06 becomes a link before it is written; the
        # top stops there, the sheet of 2026-10-07 is not written.
        ("2026-10-05.md", "2026-10-06.md", ["2026-10-05.md"]),
        # After the loop: the sheet of 2026-10-05, which justifies its deletion, becomes a link.
        ("2026-10-07.md", "2026-10-05.md", ["2026-10-06.md", "2026-10-07.md"]),
    ],
)
def test_a_sheet_replaced_by_a_link_while_the_sheets_are_written_deletes_no_ranking(
    data_dir,
    vault_dir,
    tmp_path,
    monkeypatch,
    written_name,
    replaced_name,
    sheets_written,
):
    rankings = [
        write_ranking(data_dir, STARRED_TOP, f"2026-10-0{day}T093000Z")
        for day in (5, 6, 7)
    ]
    outside = tmp_path / "outside.md"
    outside.write_bytes(b"not a sheet\n")
    write_file_atomically = vault_files.write_file_atomically

    def write_then_replace_a_sheet(path, content):
        write_file_atomically(path, content)
        if path.name == written_name:
            replaced = path.parent / replaced_name
            replaced.unlink(missing_ok=True)
            replaced.symlink_to(outside)

    monkeypatch.setattr(
        vault_files, "write_file_atomically", write_then_replace_a_sheet
    )

    with pytest.raises(
        VaultEntryRefusedError,
        match=f"^vault_link_refused: veille/tops/most_starred/{replaced_name} is a symbolic"
        " link$",
    ):
        write_top_sheets(RawStore(data_dir), vault_dir, STARRED_TOP)

    assert all(ranking.exists() for ranking in rankings)
    assert outside.read_bytes() == b"not a sheet\n"
    sheets_dir = find_sheet(vault_dir, STARRED_TOP, "2026-10-05").parent
    assert (
        sorted(path.name for path in sheets_dir.iterdir() if not path.is_symlink())
        == sheets_written
    )


def test_a_folder_named_as_a_sheet_refuses_its_top_and_no_ranking_is_deleted(
    data_dir, vault_dir
):
    older = write_ranking(data_dir, STARRED_TOP, "2026-10-05T093000Z")
    last = write_ranking(data_dir, STARRED_TOP, "2026-10-06T093000Z")
    find_sheet(vault_dir, STARRED_TOP, "2026-10-05").mkdir(parents=True)

    with pytest.raises(
        VaultEntryRefusedError,
        match="^sheet_not_a_file: veille/tops/most_starred/2026-10-05.md is not a regular"
        " file$",
    ):
        write_top_sheets(RawStore(data_dir), vault_dir, STARRED_TOP)

    assert (older.exists(), last.exists()) == (True, True)
    assert not find_sheet(vault_dir, STARRED_TOP, "2026-10-06").exists()


def test_a_linked_sheet_of_an_unreadable_ranking_refuses_the_top_too(
    data_dir, vault_dir, tmp_path
):
    """A link in the folder of the sheets is refused whatever the ranking of its day."""
    readable = write_ranking(data_dir, STARRED_TOP, "2026-10-05T093000Z")
    unreadable = write_ranking(data_dir, STARRED_TOP, "2026-10-04T093000Z", b"not json")
    sheet = find_sheet(vault_dir, STARRED_TOP, "2026-10-04")
    sheet.parent.mkdir(parents=True)
    sheet.symlink_to(tmp_path / "outside.md")

    with pytest.raises(VaultEntryRefusedError, match="^vault_link_refused: "):
        write_top_sheets(RawStore(data_dir), vault_dir, STARRED_TOP)

    assert (readable.exists(), unreadable.exists()) == (True, True)
    assert not find_sheet(vault_dir, STARRED_TOP, "2026-10-05").exists()


def test_a_sheet_removed_before_the_deletion_refuses_its_top_and_deletes_no_ranking(
    data_dir, vault_dir, monkeypatch
):
    rankings = [
        write_ranking(data_dir, STARRED_TOP, f"2026-10-0{day}T093000Z")
        for day in (5, 6)
    ]
    write_file_atomically = vault_files.write_file_atomically

    def write_then_remove_the_first_sheet(path, content):
        write_file_atomically(path, content)
        if path.name == "2026-10-06.md":
            (path.parent / "2026-10-05.md").unlink()

    monkeypatch.setattr(
        vault_files, "write_file_atomically", write_then_remove_the_first_sheet
    )

    with pytest.raises(
        VaultEntryRefusedError,
        match="^sheet_missing: veille/tops/most_starred/2026-10-05.md is not there$",
    ):
        write_top_sheets(RawStore(data_dir), vault_dir, STARRED_TOP)

    assert all(ranking.exists() for ranking in rankings)
