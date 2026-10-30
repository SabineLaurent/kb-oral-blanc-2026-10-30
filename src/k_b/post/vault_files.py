"""The files kb writes in the vault, under veille/: written once, never through a symbolic link
kb sees, each text of a source as text, each path named in the vault, never whole.

Limit accepted by Sabine for the POC (#295): a sheet or link appearing between has_sheet and the
rename is replaced or followed (CWE-367, CWE-59); only two overlapping runs can cause it."""

import re
import stat
import unicodedata
from pathlib import Path

from k_b.core.atomic_write import write_file_atomically

# The format characters that reorder a line as it is displayed (Unicode bidirectional controls
# and marks, UAX #9). U+200D, which joins the emojis, is kept.
BIDI_CONTROLS = frozenset(
    "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
)
# The characters a text loses: the controls (Cc) and the lone surrogates (Cs), which UTF-8
# cannot encode.
REMOVED_CATEGORIES = frozenset({"Cc", "Cs"})
# The ASCII punctuation that would make syntax of a text in Markdown or Obsidian: a link, an
# embed or a wikilink ([ ]), HTML (< >), a cell of a table (|), code (`), a tag (#), a comment
# (%%), a formula ($). Each is escaped by a backslash, as CommonMark allows for any ASCII
# punctuation; the backslash too, so that a text ending with one does not escape the character
# that follows it.
MARKDOWN_SYNTAX_CHARACTERS = frozenset("\\[]<>|`#%$")
# The & of a character reference (&rlm;, &copy;, &#x202E;), which CommonMark decodes: &rlm; would
# give back the bidirectional control neutralize_text removes. Escaped in both kinds of sheets;
# another & stays, so that R&D and a bare URL with a query stay as they are (one rule for both
# writers of the vault, decision of Sabine of 2026-10-21). ASCII classes, not \w.
CHARACTER_REFERENCE = re.compile(r"&(?=#|[A-Za-z0-9]+;)")


class VaultEntryRefusedError(Exception):
    """kb refuses an entry of the vault: a symbolic link (vault_link_refused), a sheet that is
    not a regular file (sheet_not_a_file), or missing before the deletion of its ranking
    (sheet_missing)."""


def write_new_sheet(
    vault_dir: Path, sheets_dir: Path, sheet_path: Path, content: str
) -> bool:
    """Write the sheet unless it exists; return whether it was written. Its folders are checked
    again first, as write_file_atomically follows a link."""
    refuse_linked_folders(vault_dir, sheets_dir)
    if has_sheet(sheet_path, vault_dir):
        return False
    write_file_atomically(sheet_path, content.encode("utf-8"))
    return True


def refuse_linked_folders(vault_dir: Path, sheets_dir: Path) -> None:
    """Refuse a symbolic link among the folders from the vault down to sheets_dir."""
    for folder in list_folders_down_to(vault_dir, sheets_dir):
        refuse_symbolic_link(folder, vault_dir)


def list_folders_down_to(vault_dir: Path, folder: Path) -> list[Path]:
    """The folders under vault_dir down to folder, the highest first."""
    parts = folder.relative_to(vault_dir).parts
    return [vault_dir.joinpath(*parts[:depth]) for depth in range(1, len(parts) + 1)]


def refuse_symbolic_link(path: Path, vault_dir: Path) -> None:
    """Raise VaultEntryRefusedError when path is a symbolic link, read by lstat, never its
    target."""
    if path.is_symlink():
        raise VaultEntryRefusedError(
            f"vault_link_refused: {path.relative_to(vault_dir).as_posix()} is a symbolic"
            " link"
        )


def has_sheet(sheet_path: Path, vault_dir: Path) -> bool:
    """Whether the sheet is written: a regular file, read by lstat; a link or another entry under
    its name is refused, as it would stand for a sheet that is not there."""
    try:
        status = sheet_path.lstat()
    except FileNotFoundError:
        return False
    named = sheet_path.relative_to(vault_dir).as_posix()
    if stat.S_ISLNK(status.st_mode):
        raise VaultEntryRefusedError(f"vault_link_refused: {named} is a symbolic link")
    if not stat.S_ISREG(status.st_mode):
        raise VaultEntryRefusedError(f"sheet_not_a_file: {named} is not a regular file")
    return True


def neutralize_text(text: str, escaped_characters: frozenset[str]) -> str:
    """The text on one line, as text: controls, lone surrogates and bidirectional controls become
    spaces, each of escaped_characters and the & of a character reference escaped, in one pass."""
    visible = "".join(
        " "
        if character in BIDI_CONTROLS
        or unicodedata.category(character) in REMOVED_CATEGORIES
        else character
        for character in text
    )
    line = " ".join(visible.split())
    references = {match.start() for match in CHARACTER_REFERENCE.finditer(line)}
    return "".join(
        f"\\{character}"
        if character in escaped_characters or position in references
        else character
        for position, character in enumerate(line)
    )
