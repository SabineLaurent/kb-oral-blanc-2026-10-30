"""Normalize the items read, without input or output (docs/specs-collecte.md, step 3)."""

from urllib.parse import urlsplit, urlunsplit

from k_b.items import Item

TRACKING_PARAMETER_PREFIX = "utm_"


def normalize_item(item: Item) -> Item:
    """The same item, with its URL without tracking parameters; its dates are in UTC already."""
    fields = item.model_dump()
    if item.url is not None:
        fields["url"] = remove_tracking_parameters(item.url)
    # Validated again: the normalized item must pass the same checks as the item read.
    return Item(**fields)


def remove_tracking_parameters(url: str) -> str:
    """Remove the utm_* parameters; the others are kept as written, in their order."""
    parts = urlsplit(url)
    query = "&".join(
        parameter
        for parameter in parts.query.split("&")
        if parameter and not parameter.startswith(TRACKING_PARAMETER_PREFIX)
    )
    return urlunsplit(parts._replace(query=query))
