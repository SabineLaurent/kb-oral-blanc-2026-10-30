"""The sources kb collect runs: the kinds chosen by KB_COLLECT_ONLY, then Zotero, the clips and
GH Archive, created here on a machine that says where each one reads."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from k_b.core.quoting import quote_value
from k_b.etl.raw_store import RawStore
from k_b.etl.source_contract import Source, SourceKind, list_sources
from k_b.etl.sources.clipping import FOLDER_PREFIX as CLIPPING_SOURCE_NAME
from k_b.etl.sources.clipping import ClippingSource
from k_b.etl.sources.gh_archive import FOLDER_PREFIX as GH_ARCHIVE_SOURCE_NAME
from k_b.etl.sources.gh_archive import GhArchiveSource, create_bigquery_client
from k_b.etl.sources.zotero import FOLDER_PREFIX as ZOTERO_SOURCE_NAME
from k_b.etl.sources.zotero import ZoteroSource

# The Zotero data directory, the one that holds zotero.sqlite. Not set: Zotero is skipped.
ZOTERO_DIR_VARIABLE = "KB_ZOTERO_DIR"
# The Obsidian vault of K-B, the one that holds Clippings/. Not set: the clips are skipped. No
# default (.claude/rules/poc.md, rule 2), as KB_ZOTERO_DIR: a folder of the user's machine.
VAULT_DIR_VARIABLE = "KB_VAULT_DIR"
# The path of the BigQuery service account key, read by the client. Not set: GH Archive is skipped.
BIGQUERY_KEY_VARIABLE = "GOOGLE_APPLICATION_CREDENTIALS"
# The sources this module creates, by the variable that says where each one reads.
VARIABLE_BY_SOURCE_NAME = {
    ZOTERO_SOURCE_NAME: ZOTERO_DIR_VARIABLE,
    CLIPPING_SOURCE_NAME: VAULT_DIR_VARIABLE,
    GH_ARCHIVE_SOURCE_NAME: BIGQUERY_KEY_VARIABLE,
}
# The source folders kb collect runs, separated by commas: tldr runs every edition. Not set: no
# filter, every source runs as without it (Zotero, the clips and GH Archive with their variable).
# No default in core/config.py: not set is no filter, not a value.
COLLECT_ONLY_VARIABLE = "KB_COLLECT_ONLY"


@dataclass(frozen=True)
class CollectOnly:
    """The source folders KB_COLLECT_ONLY chose; None: not set, every source is collected."""

    folder_names: frozenset[str] | None

    def is_collected(self, folder_name: str) -> bool:
        return self.folder_names is None or folder_name in self.folder_names


def read_collect_only_or_refusal(
    environ: Mapping[str, str], kinds: Sequence[SourceKind]
) -> CollectOnly | str:
    """The sources kb collect runs, or why the entry is refused, its value quoted."""
    value = environ.get(COLLECT_ONLY_VARIABLE)
    if value is None:
        return CollectOnly(None)
    # Split once: the names checked are the names collected. Zotero is zotero (decision of
    # Sabine, 2026-10-18): a name can only match one of the folders.
    entries = tuple(entry.strip() for entry in value.split(","))
    refusal = find_collect_only_refusal(entries, environ, kinds)
    if refusal is not None:
        return refusal
    return CollectOnly(frozenset(entry.lower() for entry in entries))


def find_collect_only_refusal(
    entries: tuple[str, ...], environ: Mapping[str, str], kinds: Sequence[SourceKind]
) -> str | None:
    """Why the sources chosen are refused, or None; a source chosen without its variable is
    refused, or kb collect could exit 0 without having run anything."""
    # A value empty or made of spaces gives one empty entry: it would choose no source.
    if entries == ("",):
        return f"{COLLECT_ONLY_VARIABLE}: empty, set the source folders separated by commas"
    folder_names = [kind.folder_prefix for kind in kinds]
    for position, entry in enumerate(entries, start=1):
        where = f"{COLLECT_ONLY_VARIABLE}: entry {position} of {len(entries)}"
        name = entry.lower()
        if not entry:
            return f"{where} empty"
        if name not in folder_names:
            return (
                f"{where} {quote_value(entry)} not a source folder;"
                f" folders: {', '.join(folder_names)}"
            )
        variable = VARIABLE_BY_SOURCE_NAME.get(name)
        if variable is not None and not environ.get(variable):
            return f"{where} {quote_value(entry)} chosen, {variable} not set"
    return None


def read_vault_dir(environ: Mapping[str, str]) -> Path | None:
    """The vault of KB_VAULT_DIR, the only reading of its value; None when not set or empty."""
    vault_dir = environ.get(VAULT_DIR_VARIABLE)
    return Path(vault_dir) if vault_dir else None


def list_collected_sources(
    collect_only: CollectOnly,
    environ: Mapping[str, str],
    kinds: Sequence[SourceKind],
    store: RawStore,
) -> list[Source]:
    """The sources of the kinds given that are chosen, then Zotero, the clips and GH Archive,
    each when chosen and its variable set."""
    sources = list_sources(
        [kind for kind in kinds if collect_only.is_collected(kind.folder_prefix)]
    )
    zotero_dir = environ.get(ZOTERO_DIR_VARIABLE)
    if zotero_dir and collect_only.is_collected(ZOTERO_SOURCE_NAME):
        sources.append(ZoteroSource(Path(zotero_dir), store))
    vault_dir = read_vault_dir(environ)
    if vault_dir is not None and collect_only.is_collected(CLIPPING_SOURCE_NAME):
        sources.append(ClippingSource(vault_dir))
    if environ.get(BIGQUERY_KEY_VARIABLE) and collect_only.is_collected(
        GH_ARCHIVE_SOURCE_NAME
    ):
        sources.append(GhArchiveSource(create_bigquery_client))
    return sources
