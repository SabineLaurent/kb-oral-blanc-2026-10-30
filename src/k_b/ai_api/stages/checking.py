"""The checks of a writing: the claims within their bounds and faithful are kept, and make the
digest of the subject."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from k_b.ai_api.stages.fidelity import ClaimFidelity, check_claim
from k_b.ai_api.stages.writing import Claim, WritingOutput, is_within_claim_bounds
from k_b.ai_api.watch_items import WatchItem, WatchKind, to_item_ref

# A source URL is returned only with one of these schemes (docs/fiches-veille.md, "Réponse").
SOURCE_URL_SCHEMES = ("http://", "https://")
# A claim out of its bounds (writing.is_within_claim_bounds); the codes of the fidelity follow it.
OUT_OF_BOUNDS = "out_of_bounds"


@dataclass(frozen=True)
class DigestClaim:
    text: str
    item_ids: tuple[int, ...]


@dataclass(frozen=True)
class DigestSource:
    """Read from the data API, never from the output of the model."""

    item_id: int
    kind: WatchKind
    title: str
    url: str | None


@dataclass(frozen=True)
class RemovedClaim:
    """A claim of the output the checks removed: its position in the output and the codes of
    why, never its text."""

    index: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Digest:
    subject: str
    claims: tuple[DigestClaim, ...]
    sources: tuple[DigestSource, ...]
    # Sent for this subject and cited by no claim of the model.
    items_not_retained_in_output: tuple[int, ...]
    # Every fact of every claim of the output, a claim removed included.
    facts_checked: int
    facts_found: int
    removed_claims: tuple[RemovedClaim, ...]
    # Cited by the model, no longer cited once its claims are removed.
    items_removed_by_checks: tuple[int, ...]

    @property
    def claims_removed(self) -> int:
        return len(self.removed_claims)


def to_digest(
    subject: str, items_sent: Sequence[WatchItem], output: WritingOutput
) -> Digest:
    """The digest of a writing checked: the claims within their bounds and faithful, and what the
    code computes from them."""
    item_by_ref = {to_item_ref(item.item_id): item for item in items_sent}
    fidelities = [check_claim(claim, item_by_ref) for claim in output.claims]
    reasons_by_claim = [
        list_removal_reasons(claim, fidelity)
        for claim, fidelity in zip(output.claims, fidelities, strict=True)
    ]
    kept = [
        claim
        for claim, reasons in zip(output.claims, reasons_by_claim, strict=True)
        if not reasons
    ]
    cited_by_model = cited_refs(output.claims)
    cited_kept = cited_refs(kept)
    removed_by_checks = cited_by_model - cited_kept
    return Digest(
        subject=subject,
        claims=tuple(to_digest_claim(claim, item_by_ref) for claim in kept),
        sources=tuple(
            to_source(item)
            for item in items_sent
            if to_item_ref(item.item_id) in cited_kept
        ),
        items_not_retained_in_output=tuple(
            item.item_id
            for item in items_sent
            if to_item_ref(item.item_id) not in cited_by_model
        ),
        facts_checked=sum(fidelity.facts_checked for fidelity in fidelities),
        facts_found=sum(fidelity.facts_found for fidelity in fidelities),
        removed_claims=tuple(
            RemovedClaim(index, reasons)
            for index, reasons in enumerate(reasons_by_claim)
            if reasons
        ),
        items_removed_by_checks=tuple(
            item.item_id
            for item in items_sent
            if to_item_ref(item.item_id) in removed_by_checks
        ),
    )


def list_removal_reasons(claim: Claim, fidelity: ClaimFidelity) -> tuple[str, ...]:
    """Why the checks remove the claim, its bounds then its fidelity; none if it is kept."""
    bounds = () if is_within_claim_bounds(claim) else (OUT_OF_BOUNDS,)
    return bounds + fidelity.refusals


def cited_refs(claims: Iterable[Claim]) -> set[str]:
    return {ref for claim in claims for ref in claim.item_ids}


def to_digest_claim(claim: Claim, item_by_ref: Mapping[str, WatchItem]) -> DigestClaim:
    return DigestClaim(
        text=claim.text,
        item_ids=tuple(item_by_ref[ref].item_id for ref in claim.item_ids),
    )


def to_source(item: WatchItem) -> DigestSource:
    url = (
        item.url
        if item.url is not None and item.url.startswith(SOURCE_URL_SCHEMES)
        else None
    )
    return DigestSource(
        item_id=item.item_id, kind=item.kind, title=item.source_title, url=url
    )
