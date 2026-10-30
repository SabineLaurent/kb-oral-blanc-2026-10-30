"""The withdrawals of the CISA catalog, and a base rebuilt from the raw files."""

import shutil

from load_helpers import (
    ADVISORY_CVE_ID,
    AUTOMATED_WATCH,
    CISA_KEV_DIR,
    CVE_ID_IN_ARABIC_INDIC_DIGITS,
    FIRST_COLLECTION,
    FIRST_CVE_ID,
    WITHDRAWN_CVE_ID,
    count_rows,
    dump_database,
    fetch_rows,
    kev_catalog,
    read_discards,
    rebuild_database,
    refuse_in_the_base,
    run_import,
    write_collection,
)

WITHDRAWAL_QUERY = (
    "SELECT withdrawn_at, revised_at, raw_file_path"
    " FROM vulnerability JOIN item ON item.id = vulnerability.item_id"
    " JOIN raw_data ON raw_data.id = item.raw_data_id WHERE cve_id = ?"
)


WITHDRAWN_COUNT_QUERY = (
    "SELECT count(*) FROM vulnerability WHERE withdrawn_at IS NOT NULL"
)


def test_a_kev_row_absent_from_a_newer_catalog_is_withdrawn_at_its_collection(
    data_dir,
):
    run_import(data_dir)
    write_collection(
        data_dir,
        CISA_KEV_DIR,
        "2026-10-06T093000Z",
        kev_catalog(without=(WITHDRAWN_CVE_ID,)),
    )

    summary = run_import(data_dir)

    assert summary.withdrawn_count == 1
    # The item keeps the file of its version: an absence is not a version.
    assert fetch_rows(data_dir, WITHDRAWAL_QUERY, (WITHDRAWN_CVE_ID,)) == [
        (
            "2026-10-06T09:30:00Z",
            None,
            f"{AUTOMATED_WATCH}/{CISA_KEV_DIR}/{FIRST_COLLECTION}.csv",
        )
    ]
    # The rows present, though not written (unchanged), are not withdrawn.
    assert count_rows(data_dir, WITHDRAWN_COUNT_QUERY) == 1
    assert {line["rule"] for line in read_discards(data_dir)} == {"unchanged"}


def test_a_withdrawn_kev_row_back_in_a_newer_catalog_is_written_again_and_dated(
    data_dir,
):
    write_collection(
        data_dir,
        CISA_KEV_DIR,
        "2026-10-06T093000Z",
        kev_catalog(without=(WITHDRAWN_CVE_ID,)),
    )
    run_import(data_dir)
    query = "SELECT item_id FROM vulnerability WHERE cve_id = ?"
    ((item_id,),) = fetch_rows(data_dir, query, (WITHDRAWN_CVE_ID,))
    back = write_collection(data_dir, CISA_KEV_DIR, "2026-10-07T093000Z", kev_catalog())

    summary = run_import(data_dir)

    # Its content is the same: only its return is written.
    assert summary.item_count == 1
    assert fetch_rows(data_dir, query, (WITHDRAWN_CVE_ID,)) == [(item_id,)]
    assert fetch_rows(data_dir, WITHDRAWAL_QUERY, (WITHDRAWN_CVE_ID,)) == [
        (None, "2026-10-07T09:30:00Z", back)
    ]


def test_a_kev_row_absent_from_two_catalogs_keeps_the_date_of_the_first(data_dir):
    for collection in ("2026-10-06T093000Z", "2026-10-07T093000Z"):
        write_collection(
            data_dir,
            CISA_KEV_DIR,
            collection,
            kev_catalog(without=(WITHDRAWN_CVE_ID,)),
        )

    run_import(data_dir)

    assert fetch_rows(data_dir, WITHDRAWAL_QUERY, (WITHDRAWN_CVE_ID,))[0][0] == (
        "2026-10-06T09:30:00Z"
    )


def test_a_catalog_with_a_rejected_row_withdraws_nothing(data_dir):
    run_import(data_dir)
    catalog = kev_catalog(without=(WITHDRAWN_CVE_ID,))
    write_collection(
        data_dir,
        CISA_KEV_DIR,
        "2026-10-06T093000Z",
        catalog.replace("Citrix NetScaler Improper Input Validation Vulnerability", ""),
    )

    summary = run_import(data_dir)

    # A rejected row is in the catalog, not absent from it.
    assert (summary.rejection_count, summary.withdrawn_count) == (1, 0)
    assert count_rows(data_dir, WITHDRAWN_COUNT_QUERY) == 0


def test_a_catalog_with_a_row_the_base_refuses_withdraws_nothing(data_dir, monkeypatch):
    run_import(data_dir)
    write_collection(
        data_dir,
        CISA_KEV_DIR,
        "2026-10-06T093000Z",
        kev_catalog(without=(WITHDRAWN_CVE_ID,)),
    )
    refuse_in_the_base(
        monkeypatch, FIRST_CVE_ID, {"cve": CVE_ID_IN_ARABIC_INDIC_DIGITS}
    )

    summary = run_import(data_dir)

    # A row the base refuses is in the catalog, as a row rejected when read.
    assert (summary.rejection_count, summary.withdrawn_count) == (1, 0)
    assert count_rows(data_dir, WITHDRAWN_COUNT_QUERY) == 0


def test_a_catalog_older_than_the_latest_imported_withdraws_nothing(data_dir):
    run_import(data_dir)
    write_collection(
        data_dir,
        CISA_KEV_DIR,
        "2026-10-04T093000Z",
        kev_catalog(without=(WITHDRAWN_CVE_ID,)),
    )

    summary = run_import(data_dir)

    assert summary.withdrawn_count == 0
    assert count_rows(data_dir, WITHDRAWN_COUNT_QUERY) == 0


def test_a_cve_only_an_older_catalog_brings_is_written_withdrawn_at_the_latest_catalog(
    data_dir,
):
    write_collection(
        data_dir,
        CISA_KEV_DIR,
        FIRST_COLLECTION,
        kev_catalog(without=(WITHDRAWN_CVE_ID,)),
    )
    run_import(data_dir)
    late = write_collection(data_dir, CISA_KEV_DIR, "2026-10-04T093000Z", kev_catalog())

    run_import(data_dir)

    # The latest catalog does not list it: it is withdrawn, as a rebuild would find it.
    assert fetch_rows(data_dir, WITHDRAWAL_QUERY, (WITHDRAWN_CVE_ID,)) == [
        ("2026-10-05T09:30:00Z", None, late)
    ]


def test_a_cve_of_an_advisory_an_older_catalog_fills_is_written_withdrawn(data_dir):
    run_import(data_dir)
    late = write_collection(
        data_dir,
        CISA_KEV_DIR,
        "2026-10-04T093000Z",
        kev_catalog(renamed={WITHDRAWN_CVE_ID: ADVISORY_CVE_ID}),
    )

    run_import(data_dir)

    assert fetch_rows(data_dir, WITHDRAWAL_QUERY, (ADVISORY_CVE_ID,)) == [
        ("2026-10-05T09:30:00Z", None, late)
    ]


def test_a_rebuilt_database_equals_the_one_kept_import_after_import(data_dir):
    run_import(data_dir)
    write_collection(
        data_dir,
        CISA_KEV_DIR,
        "2026-10-06T093000Z",
        kev_catalog(without=(WITHDRAWN_CVE_ID,)),
    )
    run_import(data_dir)
    write_collection(data_dir, CISA_KEV_DIR, "2026-10-07T093000Z", kev_catalog("Known"))
    run_import(data_dir)
    kept = dump_database(data_dir)

    rebuild_database(data_dir)

    assert dump_database(data_dir) == kept


KEV_CONTENT_QUERY = (
    "SELECT cve_id, name, description, vendor, product, date_added, ransomware_use,"
    " withdrawn_at IS NOT NULL FROM vulnerability ORDER BY cve_id"
)


def test_a_rebuilt_database_has_the_content_and_withdrawals_of_one_kept_with_a_late_catalog(
    data_dir, tmp_path_factory
):
    first_catalog = (
        data_dir / "raw" / AUTOMATED_WATCH / CISA_KEV_DIR / (f"{FIRST_COLLECTION}.csv")
    )
    set_aside = tmp_path_factory.mktemp("set_aside") / first_catalog.name
    shutil.move(first_catalog, set_aside)
    write_collection(
        data_dir,
        CISA_KEV_DIR,
        "2026-10-07T093000Z",
        kev_catalog(without=(WITHDRAWN_CVE_ID,)),
    )
    run_import(data_dir)
    # The first catalog comes late: imported after a newer one.
    shutil.move(set_aside, first_catalog)
    run_import(data_dir)
    kept = fetch_rows(data_dir, KEV_CONTENT_QUERY)

    rebuild_database(data_dir)

    assert fetch_rows(data_dir, KEV_CONTENT_QUERY) == kept
    assert (WITHDRAWN_CVE_ID, True) in [(row[0], bool(row[-1])) for row in kept]
