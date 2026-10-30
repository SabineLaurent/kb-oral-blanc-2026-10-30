"""The fidelity of a claim: each fact, normalized, is in its text and in a field sent of the item it
cites (docs/fiches-veille.md, "Fidélité")."""

import re
import unicodedata
from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from k_b.ai_api.stages.writing import Claim, Fact
from k_b.ai_api.watch_items import MASKED_LOGIN, MASKED_URL, WatchItem, WatchKind
from k_b.core.config import MAX_FIELD_CHARS_FOR_WRITING

# Dashes U+2010 to U+2015 and the minus sign U+2212 become "-", the typographic quotes "'" and '"'.
PUNCTUATION = str.maketrans(
    {
        **{chr(code): "-" for code in (*range(0x2010, 0x2016), 0x2212)},
        chr(0x2018): "'",
        chr(0x2019): "'",
        chr(0x201C): '"',
        chr(0x201D): '"',
    }
)
BLANKS = re.compile(r"\s+")
INVISIBLE_CATEGORY = "Cf"
# Applied to the text of detection (to_detection_text). A separator is any run without letter nor
# digit, between the letters of the prefix too (C.V.E.); a plural s and a number word may follow
# the prefix (CVEs, CVE n°, CVE ID, CVE réf.); o, l and i stand for digits, so that CVE-2026-OOO1 is read and never equals a
# real one. An identifier ends at a limit, as any fact: neither a letter nor a digit after it, nor
# a . or a - followed by one. Decisions of Sabine of 2026-10-24 and of time-knight on the review of
# #207 (docs/fiches-veille.md, "Fidélité").
SEPARATOR = r"[\W_]*"
NUMBER_WORD = (
    r"(?:(?:n|no|nr|num|numero|numéro|number|id|identifiant|ref|réf|reference|référence)"
    rf"(?![^\W\d_]){SEPARATOR})?"
)
DIGIT = r"[\doli]"
END_LIMIT = r"(?![^\W_]|[.-][^\W_])"
CVE = re.compile(
    rf"c{SEPARATOR}v{SEPARATOR}e(?:s(?![^\W\d_]))?{SEPARATOR}{NUMBER_WORD}"
    rf"({DIGIT}{{4}}){SEPARATOR}({DIGIT}{{4,}}){END_LIMIT}"
)
GHSA = re.compile(
    rf"g{SEPARATOR}h{SEPARATOR}s{SEPARATOR}a(?:s(?![^\W\d_]))?{SEPARATOR}{NUMBER_WORD}"
    rf"([0-9a-z]{{4}}){SEPARATOR}([0-9a-z]{{4}}){SEPARATOR}([0-9a-z]{{4}}){END_LIMIT}"
)
# The emphasis of Markdown (**, ==), removed for the detection only: CVE-2026-00**01** displays as
# an identifier.
EMPHASIS = str.maketrans("", "", "*=")
# Letters read as the Latin letters of a prefix, for the detection only: Cyrillic, Greek and Latin
# small capitals, lowercase as normalize leaves them.
PREFIX_LOOKALIKES = str.maketrans(
    {
        **{chr(code): "a" for code in (0x0430, 0x03B1, 0x1D00)},
        **{chr(code): "c" for code in (0x0441, 0x03F2, 0x1D04)},
        **{chr(code): "e" for code in (0x0435, 0x03B5, 0x1D07)},
        **{chr(code): "g" for code in (0x050D, 0x0262)},
        **{chr(code): "h" for code in (0x04BB, 0x029C)},
        **{chr(code): "s" for code in (0x0455, 0xA731)},
        **{chr(code): "v" for code in (0x0475, 0x03BD, 0x1D20)},
    }
)
# What hides or disguises a text once normalized: marks, enclosing marks, private-use and
# unassigned code points, and the Hangul and Braille fillers, displayed as blanks.
HIDING_CATEGORIES = frozenset({"Mn", "Me", "Co", "Cn"})
FILLERS = frozenset(chr(code) for code in (0x115F, 0x1160, 0x3164, 0xFFA0, 0x2800))
# The Greek letters that look like Latin ones: any other may stand next to Latin letters in a word
# (5 μs, as NFKC writes 5 µs), as arXiv titles carry them.
GREEK_LOOKALIKES = frozenset(
    chr(code)
    for code in (
        *(0x0391, 0x0392, 0x0395, 0x0396, 0x0397, 0x0399, 0x039A, 0x039C, 0x039D),
        *(0x039F, 0x03A1, 0x03A4, 0x03A5, 0x03A7, 0x03B9, 0x03BD, 0x03BF, 0x03C1),
        *(0x03C5, 0x03F2),
    )
)
LETTER_RUNS = re.compile(r"[^\W\d_]+")
# The joiner of composed emoji, the one invisible character a claim may hold; any other (a control
# of direction, a tag U+E0041) hides text from the reader.
ZERO_WIDTH_JOINER = chr(0x200D)
# Read in place of a letter, digit or symbol that is no ASCII letter decomposed, for the detection
# only: CVE-2026-ЗЗЗЗ is read, as o counts for a digit, and never equals a real one.
UNKNOWN_SIGN = "o"
SIGN_CATEGORIES = ("L", "N", "S")
MODIFIER_LETTER = "Lm"
# Added after a field cut for the writing: a letter, so that its end is not a limit (…1.2 may be
# the start of 1.2.9).
CUT_END = "a"
# Why a claim is not faithful, a closed code each: a fact not found is written with its position
# in the claim and the item it cites (fact_not_found:0:17).
FACT_NOT_FOUND = "fact_not_found"
UNDECLARED_IDENTIFIER = "undeclared_identifier"
CITED_ITEM_WITHOUT_FACT = "cited_item_without_fact"
DISGUISED_TEXT = "disguised_text"
# The fields where an ordinary fact is searched; a CVE or a GHSA only equals cve_id or ghsa_id.
SEARCHED_FIELDS_BY_KIND: dict[WatchKind, tuple[str, ...]] = {
    "paper": ("title", "summary"),
    "vulnerability": ("title", "description", "vendor", "product", "ransomware_use"),
    "release": ("repository_name", "tag_name", "title", "body"),
    "advisory": ("title", "packages"),
    "newsletter_article": ("title", "summary"),
}


@dataclass(frozen=True)
class ClaimFidelity:
    facts_checked: int
    facts_found: int
    # The codes of what the claim fails, in the order of the checks; none for a faithful claim.
    refusals: tuple[str, ...]

    @property
    def is_faithful(self) -> bool:
        """Every fact found, every identifier of the text declared, every item cited carrying a
        fact, and nothing that hides or disguises the text."""
        return not self.refusals


def check_claim(claim: Claim, item_by_ref: Mapping[str, WatchItem]) -> ClaimFidelity:
    """The facts of the claim checked, each against the item it cites."""
    text = normalize(claim.text)
    text_identifiers = set(find_identifiers(to_detection_text(text)))
    found = [
        is_fact_found(fact, text, text_identifiers, item_by_ref[fact.item_id])
        for fact in claim.facts
    ]
    declared = {
        to_canonical_identifier(to_detection_text(normalize(fact.value)))
        for fact in claim.facts
    }
    refusals = [
        f"{FACT_NOT_FOUND}:{index}:{item_by_ref[fact.item_id].item_id}"
        for index, (fact, is_found) in enumerate(zip(claim.facts, found, strict=True))
        if not is_found
    ]
    if not text_identifiers <= declared:
        refusals.append(UNDECLARED_IDENTIFIER)
    items_with_fact = {
        fact.item_id
        for fact, is_found in zip(claim.facts, found, strict=True)
        if is_found
    }
    if not set(claim.item_ids) <= items_with_fact:
        refusals.append(CITED_ITEM_WITHOUT_FACT)
    if is_disguised(claim.text):
        refusals.append(DISGUISED_TEXT)
    return ClaimFidelity(
        facts_checked=len(claim.facts),
        facts_found=sum(found),
        refusals=tuple(refusals),
    )


def normalize(text: str) -> str:
    """NFKC, casefold, invisible characters removed, dashes and quotes unified, blanks reduced."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    visible = "".join(
        char for char in folded if unicodedata.category(char) != INVISIBLE_CATEGORY
    )
    return BLANKS.sub(" ", visible.translate(PUNCTUATION))


def to_detection_text(normalized: str) -> str:
    """The normalized text without emphasis, its lookalikes of a prefix read as Latin letters,
    any other sign out of ASCII read as o."""
    read = normalized.translate(EMPHASIS).translate(PREFIX_LOOKALIKES)
    return "".join(UNKNOWN_SIGN if is_unknown_sign(char) else char for char in read)


def is_unknown_sign(char: str) -> bool:
    """Whether the character is a letter, digit or symbol out of ASCII that is not an ASCII letter
    with an accent (é stays é, so that numéro is read)."""
    if char.isascii() or not unicodedata.category(char).startswith(SIGN_CATEGORIES):
        return False
    base = unicodedata.normalize("NFD", char)[0]
    return not (base.isascii() and base.isalpha())


def is_disguised(text: str) -> bool:
    """Whether the text holds, once normalized, a character that hides, or a word mixing Latin
    letters with a letter that may look like one."""
    if any(
        unicodedata.category(char) == INVISIBLE_CATEGORY and char != ZERO_WIDTH_JOINER
        for char in text
    ):
        return True
    if any(
        unicodedata.category(char) in HIDING_CATEGORIES or char in FILLERS
        for char in normalize(text)
    ):
        return True
    # Before casefold: a Greek capital Ε looks like E, its lowercase ε does not.
    words = LETTER_RUNS.findall(unicodedata.normalize("NFKC", text))
    return any(mixes_lookalikes(word) for word in words)


def mixes_lookalikes(word: str) -> bool:
    latin = [char for char in word if is_latin(char)]
    # A modifier letter (U+02BC, an apostrophe) looks like no Latin letter.
    others = [
        char
        for char in word
        if not is_latin(char) and unicodedata.category(char) != MODIFIER_LETTER
    ]
    return bool(latin) and any(not is_free_greek(char) for char in others)


def is_latin(char: str) -> bool:
    return unicodedata.name(char, "").startswith("LATIN")


def is_free_greek(char: str) -> bool:
    return (
        unicodedata.name(char, "").startswith("GREEK") and char not in GREEK_LOOKALIKES
    )


def is_fact_found(
    fact: Fact, text: str, text_identifiers: set[str], item: WatchItem
) -> bool:
    value = normalize(fact.value)
    detection_value = to_detection_text(value)
    if CVE.search(detection_value) or GHSA.search(detection_value):
        identifier = to_canonical_identifier(detection_value)
        return (
            identifier is not None
            and identifier in text_identifiers
            and any(
                to_canonical_identifier(to_detection_text(normalize(field)))
                == identifier
                for field in list_identifier_fields(item)
            )
        )
    # A value without letter nor digit says nothing of the item, and a space is found anywhere.
    if not any(char.isalnum() for char in value):
        return False
    return is_found(value, text) and any(
        is_found(value, to_searched_text(field)) for field in list_searched_fields(item)
    )


def to_searched_text(field: str) -> str:
    """The field normalized, without the masks K-B put in it; at the length of the cut, its end is
    not a limit."""
    is_cut = len(field) >= MAX_FIELD_CHARS_FOR_WRITING
    unmasked = field.replace(MASKED_URL, " ").replace(MASKED_LOGIN, " ")
    return normalize(unmasked) + (CUT_END if is_cut else "")


def find_identifiers(text: str) -> Iterator[str]:
    """The CVE and GHSA of a text of detection, in their canonical form."""
    for cve in CVE.finditer(text):
        yield to_canonical_cve(cve)
    for ghsa in GHSA.finditer(text):
        yield to_canonical_ghsa(ghsa)


def to_canonical_identifier(value: str) -> str | None:
    """CVE-YYYY-NNNN or GHSA-xxxx-xxxx-xxxx if the value of detection is one, and nothing more."""
    if cve := CVE.fullmatch(value):
        return to_canonical_cve(cve)
    if ghsa := GHSA.fullmatch(value):
        return to_canonical_ghsa(ghsa)
    return None


def to_canonical_cve(cve: re.Match[str]) -> str:
    return f"CVE-{cve[1]}-{cve[2]}"


def to_canonical_ghsa(ghsa: re.Match[str]) -> str:
    return f"GHSA-{ghsa[1]}-{ghsa[2]}-{ghsa[3]}"


def list_identifier_fields(item: WatchItem) -> list[str]:
    values = (getattr(item.fields, name, None) for name in ("cve_id", "ghsa_id"))
    return [value for value in values if value is not None]


def list_searched_fields(item: WatchItem) -> list[str]:
    """Each text of the fields searched, each name of the packages apart; a null field skipped."""
    texts: list[str] = []
    for name in SEARCHED_FIELDS_BY_KIND[item.kind]:
        value = getattr(item.fields, name)
        if isinstance(value, list):
            texts.extend(value)
        elif value is not None:
            texts.append(value)
    return texts


def is_found(value: str, text: str) -> bool:
    """Whether the value is in the text at least once between its limits."""
    start = text.find(value)
    while start != -1:
        end = start + len(value)
        if not joins_a_word(text, start - 1, -1) and not joins_a_word(text, end, 1):
            return True
        start = text.find(value, start + 1)
    return False


def joins_a_word(text: str, position: int, step: int) -> bool:
    """Whether the character at position is a letter or a digit, or a . or a - with one beyond it."""
    if not 0 <= position < len(text):
        return False
    char = text[position]
    if char.isalnum():
        return True
    beyond = position + step
    return char in ".-" and 0 <= beyond < len(text) and text[beyond].isalnum()
