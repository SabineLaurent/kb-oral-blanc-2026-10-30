from dataclasses import fields
from typing import get_args

from k_b.core.config import GITHUB_ECOSYSTEMS, Ecosystem, TrackedRepository


def test_the_ecosystems_are_written_once_the_list_and_the_type_hold_the_same_values():
    [ecosystem_field] = [
        field for field in fields(TrackedRepository) if field.name == "ecosystem"
    ]

    assert ecosystem_field.type is Ecosystem
    assert GITHUB_ECOSYSTEMS == get_args(Ecosystem.__value__) == ("pip", "npm")
