"""The writers called without the import: what each one refuses by itself."""

from datetime import timedelta

import pytest
from test_items import COLLECTED_AT, vulnerability_fields, zotero_entry_fields

from k_b.database.connection import connect_database
from k_b.database.creation import create_database
from k_b.database.writers import (
    shared_rows,
    vulnerability_writer,
    zotero_entry_writer,
)
from k_b.items import Item

LATER_COLLECTED_AT = COLLECTED_AT + timedelta(days=1)


@pytest.fixture
def connection(tmp_path):
    database_path = tmp_path / "k-b.sqlite"
    create_database(database_path)
    connection = connect_database(database_path)
    yield connection
    connection.close()


def test_a_cisa_kev_withdrawal_without_cve_withdraws_nothing(connection):
    item = Item(**vulnerability_fields())
    raw_data_id = shared_rows.write_raw_data(
        connection, "cisa_kev", item.raw_file_path, "cisa_kev", COLLECTED_AT
    )
    vulnerability_writer.write(connection, item, raw_data_id)
    later_raw_data_id = shared_rows.write_raw_data(
        connection,
        "cisa_kev",
        "automated_watch/cisa_kev/2026-10-06T093000Z.csv",
        "cisa_kev",
        LATER_COLLECTED_AT,
    )

    withdrawn_count = vulnerability_writer.mark_withdrawn(
        connection, "cisa_kev", LATER_COLLECTED_AT, later_raw_data_id, []
    )

    assert withdrawn_count == 0
    assert connection.execute("SELECT withdrawn_at FROM vulnerability").fetchall() == [
        (None,)
    ]


def test_a_zotero_withdrawal_without_entry_withdraws_nothing(connection):
    item = Item(**zotero_entry_fields())
    raw_data_id = shared_rows.write_raw_data(
        connection, "zotero", item.raw_file_path, "zotero", COLLECTED_AT
    )
    zotero_entry_writer.write(connection, item, raw_data_id)
    later_raw_data_id = shared_rows.write_raw_data(
        connection,
        "zotero",
        "selected_library/zotero/2026-10-06T093000Z.json",
        "zotero",
        LATER_COLLECTED_AT,
    )

    withdrawn_count = zotero_entry_writer.mark_withdrawn(
        connection, "zotero", LATER_COLLECTED_AT, later_raw_data_id, []
    )

    assert withdrawn_count == 0
    assert connection.execute("SELECT withdrawn_at FROM zotero_entry").fetchall() == [
        (None,)
    ]
    assert connection.execute("SELECT withdrawn_at FROM highlight").fetchall() == [
        (None,)
    ]
