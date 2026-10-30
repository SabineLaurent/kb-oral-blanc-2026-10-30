"""What a writer returns for an item the base left out."""

from typing import Literal, NamedTuple

from k_b.items import Item

# Why an item was left out, as the base observed it.
DiscardRule = Literal[
    "already_seen", "unchanged", "no_newer_version", "revision_not_newer"
]


class Discard(NamedTuple):
    rule: DiscardRule
    item: Item
    # The date of the base the item was compared with, in the format of the base; None when
    # the base has none (a catalog without a latest one imported).
    compared_with: str | None
