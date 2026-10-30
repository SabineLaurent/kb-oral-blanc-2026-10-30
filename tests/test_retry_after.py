import pytest

from k_b.core.retry_after import (
    MAX_RETRY_AFTER_DIGITS,
    is_retry_after_too_long,
    read_retry_after,
)


@pytest.mark.parametrize(
    ("value", "seconds"),
    [("0", 0), ("20", 20), ("9" * MAX_RETRY_AFTER_DIGITS, 999999999)],
)
def test_a_retry_after_in_seconds_is_read(value, seconds):
    assert read_retry_after(value) == seconds
    assert not is_retry_after_too_long(value)


@pytest.mark.parametrize(
    "value", [None, "Fri, 31 Dec 1999 23:59:59 GMT", "-1", "1.5", "", "\uff11"]
)
def test_no_header_a_date_or_another_text_is_read_as_no_header(value):
    assert read_retry_after(value) is None
    assert not is_retry_after_too_long(value)


@pytest.mark.parametrize(
    "value", ["1" * (MAX_RETRY_AFTER_DIGITS + 1), "9" * 4301, "9" * 5000]
)
def test_seconds_too_many_to_read_are_too_long_never_read(value):
    """int() refuses 4,301 digits or more: it is never asked."""
    assert is_retry_after_too_long(value)
    assert read_retry_after(value) is None
