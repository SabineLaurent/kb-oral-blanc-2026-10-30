"""The log of an import names the date each rule of the writers compared the item with."""

from typing import get_args

from k_b.database.writers.discard import DiscardRule
from k_b.etl.import_log import COMPARED_FIELD_BY_RULE


def test_every_discard_rule_has_its_field_in_the_log():
    assert set(COMPARED_FIELD_BY_RULE) == set(get_args(DiscardRule))
