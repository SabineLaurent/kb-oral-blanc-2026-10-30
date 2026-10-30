"""The responses of GET /items and GET /items/{item_id}: the API's own models, never Item, so
that a field added to the reading is not served."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from k_b.items import ItemKind, RansomwareUse

# The dates the response names by where they come from: a date of K-B is the collection that saw
# a catalog row change, not a date of the source.
DATE_OF_K_B = "A date of K-B: the collection that saw the change."


class ListedItemSchema(BaseModel):
    id: int
    kind: ItemKind
    title: str
    # The date of since, as the base writes it: YYYY-MM-DDTHH:MM:SSZ.
    source_date: str
    url: str | None
    withdrawn_at: str | None = Field(
        description=f"Withdrawn from a catalog (vulnerability, Zotero entry). {DATE_OF_K_B}"
    )


class ItemPageSchema(BaseModel):
    items: list[ListedItemSchema]
    # The offset of the next page; null on the last one, or beyond the bound of offset.
    next_offset: int | None


# The detail of each kind: the fields of the list, then those of its kind. kind narrowed to one
# value tells pydantic, and the OpenAPI, which schema a response follows.


class PaperDetailSchema(ListedItemSchema):
    kind: Literal["paper"]
    arxiv_id: str
    authors: list[str]
    summary: str
    arxiv_updated_at: str = Field(
        description="A date of arXiv: the last version of the paper."
    )


class VulnerabilityDetailSchema(ListedItemSchema):
    kind: Literal["vulnerability"]
    cve_id: str
    description: str
    vendor: str
    product: str
    ransomware_use: RansomwareUse
    revised_at: str | None = Field(description=DATE_OF_K_B)


class ReleaseDetailSchema(ListedItemSchema):
    kind: Literal["release"]
    release_id: int
    repository_name: str
    tag_name: str
    body: str | None


class AdvisoryDetailSchema(ListedItemSchema):
    kind: Literal["advisory"]
    ghsa_id: str
    cve_id: str | None
    exploited: bool | None = Field(
        description="The CVE is in the CISA KEV catalog, withdrawn since or not; false says"
        " absent from the catalog, not unexploited; null without CVE."
    )
    packages: list[str]


class NewsletterArticleDetailSchema(ListedItemSchema):
    kind: Literal["newsletter_article"]
    edition: str
    edition_date: str
    rank: int
    summary: str


class HighlightSchema(BaseModel):
    # Read from the highlight of the reading by its attributes: its key is not served.
    model_config = ConfigDict(from_attributes=True)

    text: str | None
    comment: str | None
    highlighted_at: str = Field(description="A date of Zotero: the highlight was made.")
    revised_at: str | None = Field(description=DATE_OF_K_B)
    withdrawn_at: str | None = Field(description=DATE_OF_K_B)


class ZoteroEntryDetailSchema(ListedItemSchema):
    kind: Literal["zotero_entry"]
    zotero_key: str
    item_type: str
    arxiv_id: str | None
    revised_at: str | None = Field(description=DATE_OF_K_B)
    # The withdrawn ones included, by date of creation.
    highlights: list[HighlightSchema]


class ClipDetailSchema(ListedItemSchema):
    kind: Literal["clip"]
    published_at: str | None = Field(
        description="A date of the page kept (published of Web Clipper): a day, at midnight"
        " UTC; null when the page gives none."
    )
    body: str = Field(description="The text kept, Markdown, as Web Clipper wrote it.")


ItemDetailSchema = Annotated[
    PaperDetailSchema
    | VulnerabilityDetailSchema
    | ReleaseDetailSchema
    | AdvisoryDetailSchema
    | NewsletterArticleDetailSchema
    | ZoteroEntryDetailSchema
    | ClipDetailSchema,
    Field(discriminator="kind"),
]
