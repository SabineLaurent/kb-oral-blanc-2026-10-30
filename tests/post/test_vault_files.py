"""The files kb writes in the vault: a sheet written once, never through a symbolic link, a text
of a source written as text."""

import pytest

from k_b.post import vault_files
from k_b.post.vault_files import (
    MARKDOWN_SYNTAX_CHARACTERS,
    VaultEntryRefusedError,
    neutralize_text,
    write_new_sheet,
)


@pytest.fixture
def vault_dir(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


@pytest.fixture
def sheets_dir(vault_dir):
    return vault_dir / "veille" / "subject"


def list_files(directory):
    return sorted(
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file()
    )


def test_a_new_sheet_is_written_with_its_folders(vault_dir, sheets_dir):
    sheet_path = sheets_dir / "2026-10-21.md"

    assert write_new_sheet(vault_dir, sheets_dir, sheet_path, "# Fiche\n") is True
    assert sheet_path.read_text("utf-8") == "# Fiche\n"
    assert list_files(vault_dir) == ["veille/subject/2026-10-21.md"]


def test_a_sheet_that_exists_is_never_rewritten(vault_dir, sheets_dir):
    sheet_path = sheets_dir / "2026-10-21.md"
    sheets_dir.mkdir(parents=True)
    sheet_path.write_bytes(b"edited by hand\n")

    assert write_new_sheet(vault_dir, sheets_dir, sheet_path, "# Fiche\n") is False
    assert sheet_path.read_bytes() == b"edited by hand\n"


@pytest.mark.parametrize("linked", ["veille", "veille/subject"])
def test_a_folder_that_is_a_symbolic_link_is_refused_before_the_sheet_is_written(
    vault_dir, sheets_dir, tmp_path, linked
):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link = vault_dir / linked
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(
        VaultEntryRefusedError,
        match=f"^vault_link_refused: {linked} is a symbolic link$",
    ):
        write_new_sheet(vault_dir, sheets_dir, sheets_dir / "2026-10-21.md", "x\n")

    assert list_files(elsewhere) == []


@pytest.mark.parametrize("target_exists", [False, True])
def test_a_sheet_that_is_a_symbolic_link_is_refused_never_read_as_a_sheet(
    vault_dir, sheets_dir, tmp_path, target_exists
):
    outside = tmp_path / "outside.md"
    if target_exists:
        outside.write_bytes(b"not a sheet\n")
    sheet_path = sheets_dir / "2026-10-21.md"
    sheets_dir.mkdir(parents=True)
    sheet_path.symlink_to(outside)

    with pytest.raises(
        VaultEntryRefusedError,
        match="^vault_link_refused: veille/subject/2026-10-21.md is a symbolic link$",
    ):
        write_new_sheet(vault_dir, sheets_dir, sheet_path, "x\n")

    assert sheet_path.is_symlink()
    assert outside.exists() is target_exists


def test_a_folder_named_as_the_sheet_is_refused(vault_dir, sheets_dir):
    sheet_path = sheets_dir / "2026-10-21.md"
    sheet_path.mkdir(parents=True)

    with pytest.raises(
        VaultEntryRefusedError,
        match="^sheet_not_a_file: veille/subject/2026-10-21.md is not a regular file$",
    ):
        write_new_sheet(vault_dir, sheets_dir, sheet_path, "x\n")


def test_a_sheet_not_written_leaves_no_temporary_file(
    vault_dir, sheets_dir, monkeypatch
):
    def fail(path, content):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(vault_files, "write_file_atomically", fail)

    with pytest.raises(PermissionError):
        write_new_sheet(vault_dir, sheets_dir, sheets_dir / "2026-10-21.md", "x\n")

    assert list_files(vault_dir) == []


@pytest.mark.parametrize(
    ("text", "neutralized"),
    [
        ("", ""),
        ("first line\nsecond\r\nthird fourth", "first line second third fourth"),
        ("line\n---\nline", "line --- line"),
        ("[a link](https://example.com)", "\\[a link\\](https://example.com)"),
        ("![embed](x.png) [[wikilink]]", "!\\[embed\\](x.png) \\[\\[wikilink\\]\\]"),
        ("<script>alert(1)</script>", "\\<script\\>alert(1)\\</script\\>"),
        ("a | b", "a \\| b"),
        ("ends with a backslash \\", "ends with a backslash \\\\"),
        ("`code` and #tag", "\\`code\\` and \\#tag"),
        ("%%hidden and save $5 or $10", "\\%\\%hidden and save \\$5 or \\$10"),
        ("R&D &rlm; ?q=a&page=2 &#x202E;", "R&D \\&rlm; ?q=a&page=2 \\&\\#x202E;"),
        ("escape \x1b[31m red", "escape \\[31m red"),
        ("lone \ud800 surrogate", "lone surrogate"),
        ("right\u202eleft\u2066mark\u200f", "right left mark"),
        ("🚀✨ family 👨\u200d👩\u200d👧", "🚀✨ family 👨\u200d👩\u200d👧"),
    ],
)
def test_a_text_is_neutralized_on_one_line(text, neutralized):
    assert neutralize_text(text, MARKDOWN_SYNTAX_CHARACTERS) == neutralized


def test_a_text_escapes_the_characters_it_is_given_and_those_only():
    assert neutralize_text("a~b:c@d [e]", frozenset("~:@")) == "a\\~b\\:c\\@d [e]"
