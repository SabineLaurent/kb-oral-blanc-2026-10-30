"""The fidelity of a claim: its facts normalized, then found in its text and in the item it cites,
between their limits (docs/fiches-veille.md, "Fidélité")."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from k_b.ai_api.stages.fidelity import check_claim, is_found, normalize
from k_b.ai_api.stages.writing import Claim
from k_b.ai_api.watch_items import WatchItem, build_fields
from k_b.core.config import MAX_FIELD_CHARS_FOR_WRITING
from k_b.etl.sources.tldr import read_articles

# Characters written by their code point, never as an escape in the source (MEMORY.md).
NON_BREAKING_HYPHEN = chr(0x2011)
RIGHT_QUOTE = chr(0x2019)
ZERO_WIDTH_SPACE = chr(0x200B)
NO_BREAK_SPACE = chr(0x00A0)


def watch_item(item_id, kind, **values):
    return WatchItem(
        item_id=item_id,
        kind=kind,
        source_date=datetime(2026, 10, 1, tzinfo=UTC),
        url=None,
        source_title="",
        fields=build_fields(kind, values),
    )


# Items of the test base (tests/fixtures/items_database.sql).
RELEASE = watch_item(
    6,
    "release",
    repository_name="anthropics/claude-code",
    tag_name="v2.1.287",
    title="Claude Code 2.1.287",
    body="## What's changed\n\n- Added Claude Mods: plugins may now modify deeper behavior",
)
VULNERABILITY = watch_item(
    3,
    "vulnerability",
    cve_id="CVE-2026-86950",
    title="Apple Multiple Products Out-of-Bounds Write Vulnerability",
    description="Apple iOS, macOS, and iPadOS contain an out-of-bounds write vulnerability in "
    "CoreGraphics that may lead to arbitrary code execution.",
    vendor="Apple",
    product="Multiple Products",
    ransomware_use="Unknown",
)
ADVISORY = watch_item(
    9,
    "advisory",
    ghsa_id="GHSA-fjqc-hq36-qh5p",
    cve_id="CVE-2020-29583",
    title="LangGraph Checkpoint: Unsafe JSON deserialization in checkpoint loading",
    cve_listed_in_cisa_kev=True,
    packages=["langgraph-checkpoint"],
)
ARTICLE = watch_item(
    11,
    "newsletter_article",
    edition="ai",
    title="Mistral Large 4 (9 minute read)",
    summary="Mistral Large 4 is a one-trillion-parameter multimodal model positioned as a "
    "European alternative to leading closed and open AI systems.",
)
# The trapped article of #206, as test_digests.py reads it.
TRAPPED_ARTICLE = read_articles(
    (
        Path(__file__).parent.parent / "fixtures" / "tldr_ai_trapped_extract.html"
    ).read_bytes()
).entries[0]
TRAPPED_REF = "i900"
TRAPPED_ITEM = watch_item(
    900,
    "newsletter_article",
    edition="ai",
    title=TRAPPED_ARTICLE.title,
    summary=TRAPPED_ARTICLE.summary,
)
ITEM_BY_REF = {
    f"i{item.item_id}": item for item in (RELEASE, VULNERABILITY, ADVISORY, ARTICLE)
}


def claim(text, *facts):
    """A claim citing the items of its facts, in their order."""
    refs = list(dict.fromkeys(ref for _, ref in facts))
    return Claim(
        text=text,
        item_ids=refs,
        facts=[{"value": value, "item_id": ref} for value, ref in facts],
    )


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        (f"v2{NON_BREAKING_HYPHEN}1", "v2-1"),
        (f"l{RIGHT_QUOTE}alerte", "l'alerte"),
        ("Claude CODE", "claude code"),
        (f"Lang{ZERO_WIDTH_SPACE}Graph", "langgraph"),
        (f"Apple{NO_BREAK_SPACE}\n\t iOS", "apple ios"),
        (chr(0xFF36) + "2", "v2"),
    ],
    ids=["dash", "apostrophe", "case", "invisible", "blanks", "fullwidth"],
)
def test_a_text_is_normalized_before_any_search(raw, normalized):
    assert normalize(raw) == normalized


@pytest.mark.parametrize(
    ("value", "text", "expected"),
    [
        ("1.2", "version 1.2.", True),
        ("1.2", "version 1.2.9", False),
        ("1.2", "1.2.9 puis 1.2", True),
        ("cve-2026-1234", "cve-2026-12345", False),
        ("1.2.0", "v1.2.0", False),
        ("caf", "café", False),
        ("apple", "(apple)", True),
    ],
)
def test_a_value_is_found_only_between_its_limits(value, text, expected):
    assert is_found(value, text) is expected


def test_a_fact_found_in_its_text_and_its_item_is_checked_and_found():
    fidelity = check_claim(
        claim("Claude Code publie la version v2.1.287.", ("v2.1.287", "i6")),
        ITEM_BY_REF,
    )

    assert (fidelity.facts_checked, fidelity.facts_found, fidelity.is_faithful) == (
        1,
        1,
        True,
    )


def test_a_fact_is_found_in_one_field_never_across_two():
    # "anthropics/claude-code" and "v2.1.287" are two fields: their join is in none.
    fidelity = check_claim(
        claim(
            "anthropics/claude-code v2.1.287", ("anthropics/claude-code v2.1.287", "i6")
        ),
        ITEM_BY_REF,
    )

    assert fidelity.is_faithful is False


def test_a_fact_is_searched_in_each_name_of_the_packages():
    fidelity = check_claim(
        claim("GHSA-fjqc-hq36-qh5p touche langgraph-checkpoint.",
              ("GHSA-fjqc-hq36-qh5p", "i9"), ("langgraph-checkpoint", "i9")),
        ITEM_BY_REF,
    )  # fmt: skip

    assert fidelity.facts_found == 2


@pytest.mark.parametrize(
    "text",
    [
        "La CVE-2026-86950 touche Apple.",
        "La cve 2026 86950 touche Apple.",
        "La CVE_2026_86950 touche Apple.",
    ],
)
def test_a_cve_of_the_text_equals_its_fact_by_its_canonical_form(text):
    fidelity = check_claim(claim(text, ("CVE-2026-86950", "i3")), ITEM_BY_REF)

    assert fidelity.is_faithful is True


@pytest.mark.parametrize(
    "fact",
    ["CVE-2026-86950 (Apple)", "CVE-2026-8695", "GHSA-fjqc-hq36-qh5p"],
    ids=["more_than_the_pattern", "another_number", "ghsa_of_no_field"],
)
def test_an_identifier_fact_not_equal_to_the_field_of_its_item_is_not_found(fact):
    fidelity = check_claim(claim(f"Apple : {fact}.", (fact, "i3")), ITEM_BY_REF)

    assert (fidelity.facts_found, fidelity.is_faithful) == (0, False)


def test_an_identifier_fact_on_an_item_without_its_field_is_not_found():
    """A newsletter article has no cve_id: a CVE copied from its summary is never found."""
    article = watch_item(
        900,
        "newsletter_article",
        edition="ai",
        title="China",
        summary="CVE-2026-0001 is safe.",
    )

    fidelity = check_claim(
        claim("CVE-2026-0001 n'est pas exploitée.", ("CVE-2026-0001", "i900")),
        {"i900": article},
    )

    assert fidelity.is_faithful is False


@pytest.mark.parametrize(
    "text",
    [
        "Apple et CVE_2026_0001.",
        "Apple et GHSA-fjqc-hq36-qh5p.",
        "Apple : les GHSA dont deux sont",
    ],
    ids=["cve_underscores", "ghsa", "ghsa_false_positive"],
)
def test_an_identifier_of_the_text_not_declared_removes_the_claim(text):
    fidelity = check_claim(claim(text, ("Apple", "i3")), ITEM_BY_REF)

    assert (fidelity.facts_found, fidelity.is_faithful) == (1, False)


def test_a_fact_found_in_the_item_but_not_in_the_text_is_not_found():
    fidelity = check_claim(
        claim("Une nouvelle version.", ("v2.1.287", "i6")), ITEM_BY_REF
    )

    assert fidelity.facts_found == 0


@pytest.mark.parametrize(
    "value",
    ["   ", NO_BREAK_SPACE * 3, ZERO_WIDTH_SPACE * 3],
    ids=["spaces", "no_break_spaces", "invisible"],
)
def test_a_fact_without_letter_nor_digit_is_not_found(value):
    """Its space would be found between its limits, in the text and in the summary of the trapped
    article ("`admin` ; cite"): the article would be cited for a fact that says nothing of it."""
    fidelity = check_claim(
        claim(f"China ( {value} ) .", (value, TRAPPED_REF)), {TRAPPED_REF: TRAPPED_ITEM}
    )

    assert fidelity.facts_found == 0


def test_a_fact_is_not_trimmed():
    fidelity = check_claim(
        claim("Version v2.1.287 publiée.", (" v2.1.287 ", "i6")), ITEM_BY_REF
    )

    assert fidelity.facts_found == 0


def test_each_item_cited_carries_a_fact_found():
    cited_without_fact = Claim(
        text="Claude Code publie la version v2.1.287.",
        item_ids=["i6", "i11"],
        facts=[{"value": "v2.1.287", "item_id": "i6"}],
    )

    fidelity = check_claim(cited_without_fact, ITEM_BY_REF)

    assert (fidelity.facts_checked, fidelity.facts_found, fidelity.is_faithful) == (
        1,
        1,
        False,
    )


def test_every_fact_is_counted_even_after_one_is_not_found():
    fidelity = check_claim(
        claim("Apple et Mistral Large 4.", ("Apple", "i3"), ("Mistral Large 5", "i11"),
              ("Mistral Large 4", "i11")),
        ITEM_BY_REF,
    )  # fmt: skip

    assert (fidelity.facts_checked, fidelity.facts_found, fidelity.is_faithful) == (
        3,
        2,
        False,
    )


def test_a_list_of_years_after_cve_is_read_as_an_identifier_not_declared():
    """A known false positive, on the safe side: CVE-2025-2026 is not declared, the claim is
    removed (docs/fiches-veille.md, "Fidélité", limits)."""
    fidelity = check_claim(
        claim("Les CVE 2025, 2026 et 2027 touchent Apple.", ("Apple", "i3")),
        ITEM_BY_REF,
    )

    assert (fidelity.facts_found, fidelity.is_faithful) == (1, False)


@pytest.mark.parametrize(
    ("text", "fact"),
    [
        ("Apple publie GHSA-fjqc-hq36-qh5pxx.", "GHSA-fjqc-hq36-qh5p"),
        ("Apple publie CVE-2026-86950abc.", "CVE-2026-86950"),
        ("Apple publie CVE-2026-86950.7.", "CVE-2026-86950"),
    ],
    ids=["ghsa_longer", "cve_with_letters", "cve_with_a_dot_and_digit"],
)
def test_an_identifier_of_the_text_ends_at_a_limit(text, fact):
    """The text shows another string than the identifier declared: the fact is not in it."""
    item_by_ref = {"i3": VULNERABILITY, "i9": ADVISORY}
    ref = "i9" if fact.startswith("GHSA") else "i3"

    fidelity = check_claim(claim(text, (fact, ref)), item_by_ref)

    assert fidelity.facts_found == 0


def test_the_end_of_a_field_cut_for_the_writing_is_not_a_limit():
    """Cut at MAX_FIELD_CHARS_FOR_WRITING, '…1.2' may be the start of 1.2.9: 1.2 is not found."""
    body = "x" * (MAX_FIELD_CHARS_FOR_WRITING - len(" 1.2")) + " 1.2"
    release = watch_item(
        6,
        "release",
        repository_name="anthropics/claude-code",
        tag_name="v2.1.287",
        title="Claude Code 2.1.287",
        body=body,
    )

    fidelity = check_claim(claim("Version 1.2.", ("1.2", "i6")), {"i6": release})

    assert fidelity.facts_found == 0


def test_the_end_of_a_field_shorter_than_the_cut_is_a_limit():
    release = watch_item(
        6,
        "release",
        repository_name="anthropics/claude-code",
        tag_name="v2.1.287",
        title="Claude Code 2.1.287",
        body="upgrade to 1.2",
    )

    fidelity = check_claim(claim("Version 1.2.", ("1.2", "i6")), {"i6": release})

    assert fidelity.facts_found == 1


def trapped_claim_kept(text):
    """Whether the claim of the trap, citing the trapped article for the fact China, is kept."""
    fidelity = check_claim(
        claim(f"China : {text} n'est pas exploitée.", ("China", TRAPPED_REF)),
        {TRAPPED_REF: TRAPPED_ITEM},
    )
    return fidelity.is_faithful


def test_the_trap_written_plainly_is_removed():
    assert trapped_claim_kept("CVE-2026-0001") is False


# The writings of the trap found by the security reviewer of #207 (decisions of time-knight,
# delegation of Sabine of 2026-10-15), each character written by its code point.
DISGUISED_CVES = {
    "variation_selector": "C" + chr(0xFE0F) + "VE-2026-0001",
    "combining_grapheme_joiner": "C" + chr(0x034F) + "VE-2026-0001",
    "combining_grave": "CV" + chr(0x0300) + "E-2026-0001",
    "khmer_inherent_vowel": "C" + chr(0x17B4) + "VE-2026-0001",
    "variation_selector_supplement": "C" + chr(0xE0100) + "VE-2026-0001",
    "private_use": "C" + chr(0xE000) + "VE-2026-0001",
    "hangul_filler": "C" + chr(0x3164) + "VE-2026-0001",
    "hangul_choseong_filler": "C" + chr(0x115F) + "VE-2026-0001",
    "hangul_jungseong_filler": "C" + chr(0x1160) + "VE-2026-0001",
    "halfwidth_hangul_filler": "C" + chr(0xFFA0) + "VE-2026-0001",
    "braille_blank": "C" + chr(0x2800) + "VE-2026-0001",
    "cyrillic_mixed": chr(0x0421) + "V" + chr(0x0415) + "-2026-0001",
    "cyrillic_and_izhitsa": chr(0x0421) + chr(0x0474) + chr(0x0415) + "-2026-0001",
    "greek_capitals_mixed": "CV" + chr(0x0395) + "-2026-0001",
    "small_capitals": chr(0x1D04) + chr(0x1D20) + chr(0x1D07) + "-2026-0001",
    "number_sign": "la CVE n°2026-0001",
    "numero_sign": "la CVE " + chr(0x2116) + "2026-0001",
    "id": "la CVE ID 2026-0001",
    "numero": "la CVE numéro 2026-0001",
    "number": "la CVE number 2026-0001",
    "emphasis_in_prefix": "C*V*E-2026-0001",
    "bold_in_number": "CVE-2026-00**01**",
    "highlight_in_number": "CVE-2026-00==01==",
    "dotted_prefix": "C.V.E. 2026-0001",
    "letters_o_for_zeros": "CVE-2026-OOO1",
    "letter_l_for_one": "CVE-2026-000l",
    "ghsa_cyrillic": "GHS" + chr(0x0410) + "-fjqc-hq36-qh5p",
    "plural": "les CVEs 2026-0001",
    "identifiant": "la CVE identifiant 2026-0001",
    "ref": "la CVE réf. 2026-0001",
    "reference": "la CVE référence 2026-0001",
    "right_to_left_override": chr(0x202E) + "1000-6202-EVC" + chr(0x202C),
    "right_to_left_isolate": chr(0x2067) + "1000-6202-EVC" + chr(0x2069),
    "cyrillic_o_in_the_number": "CVE-2026-0" + chr(0x043E) + "01",
    "greek_omicron_in_the_year": "CVE-2" + chr(0x03BF) + "26-0001",
    "cyrillic_er_in_a_ghsa_group": "GHSA-fjqc-hq36-qh5" + chr(0x0440),
    "cyrillic_ha_in_a_ghsa_group": "GHSA-" + chr(0x0445) + "2fg-hq36-qh5p",
    "cyrillic_ze_for_digits": "CVE-2026-" + chr(0x0417) * 4,
    "cyrillic_er_group": "GHSA-fjqc-hq36-" + chr(0x0440) * 4,
    "small_capital_o": "CVE-2026-0" + chr(0x1D0F) + "01",
    "dotless_i": "CVE-2026-000" + chr(0x0131),
    "white_circle": "CVE-2026-0" + chr(0x25CB) + "01",
    # Invisible text, carried to the vault: tags U+E0043 U+E0056 U+E0045 spell "CVE".
    "hidden_tags": "rien" + chr(0xE0043) + chr(0xE0056) + chr(0xE0045),
}


@pytest.mark.parametrize("text", DISGUISED_CVES.values(), ids=DISGUISED_CVES.keys())
def test_the_trap_disguised_is_removed(text):
    assert trapped_claim_kept(text) is False


def test_a_cv_in_a_sentence_can_read_as_a_cve_and_remove_the_claim():
    """A known false positive, on the safe side (docs/fiches-veille.md, "Fidélité", limits)."""
    assert trapped_claim_kept("Le CV en 2026, 0001 candidats, et") is False


# Paper 2 of the test base, its title with Greek letters as arXiv titles carry them.
GREEK_PAPER = watch_item(
    2,
    "paper",
    title="Evaluating β-VAE and α-Fold judges in 5 µs",
    summary="Model-based judges support agent security by detecting prompt injections.",
)


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("Le β-VAE juge les injections.", "β-VAE"),
        ("α-Fold juge les injections.", "α-Fold"),
        ("Un juge en 5 µs.", "5 µs"),
    ],
    ids=["beta_vae", "alpha_fold", "micro_second"],
)
def test_greek_letters_next_to_latin_ones_keep_the_claim(text, value):
    fidelity = check_claim(claim(text, (value, "i2")), {"i2": GREEK_PAPER})

    assert fidelity.is_faithful is True


def test_a_modifier_letter_apostrophe_keeps_the_claim():
    """U+02BC is an apostrophe a model may write, not a lookalike of a Latin letter."""
    fidelity = check_claim(
        claim("China : l" + chr(0x02BC) + "équipe recrute.", ("China", TRAPPED_REF)),
        {TRAPPED_REF: TRAPPED_ITEM},
    )

    assert fidelity.is_faithful is True


@pytest.mark.parametrize("value", ["@user", "[url]"])
def test_a_fact_equal_to_a_mark_put_by_k_b_is_not_found(value):
    """The masks of a login and of a URL say nothing of the item."""
    release = watch_item(
        6,
        "release",
        repository_name="anthropics/claude-code",
        tag_name="v2.1.287",
        title="Claude Code 2.1.287",
        body="Thanks @octocat, see https://github.com/anthropics/claude-code/pull/1",
    )

    fidelity = check_claim(claim(f"Voir {value} ici.", (value, "i6")), {"i6": release})

    assert fidelity.facts_found == 0


def test_a_control_of_direction_at_the_start_of_the_text_removes_the_claim():
    fidelity = check_claim(
        claim(chr(0x202E) + "1000-6202-EVC China.", ("China", TRAPPED_REF)),
        {TRAPPED_REF: TRAPPED_ITEM},
    )

    assert fidelity.is_faithful is False


NOT_DISGUISED = {
    "greek_nu_and_digit": "ν2",
    "ideogram_after_digits": "2026年",
    "cyrillic_word_and_digits": "Москва2026",
    "beta_vae": "β-VAE",
    "alpha_fold": "α-Fold",
    "micro_second": "5 µs",
    "beta_and_digit": "β2",
    "emoji_with_joiner": chr(0x1F469) + chr(0x200D) + chr(0x1F4BB),
    "subscript": "H₂O",
    "superscript": "m²",
    "ordinal": "3ᵉ",
    "number_sign": "n°3",
    "accented_word": "été 2026",
}


@pytest.mark.parametrize("phrase", NOT_DISGUISED.values(), ids=NOT_DISGUISED.keys())
def test_a_sign_out_of_ascii_in_an_ordinary_phrase_keeps_the_claim(phrase):
    fidelity = check_claim(
        claim(f"China : {phrase}.", ("China", TRAPPED_REF)), {TRAPPED_REF: TRAPPED_ITEM}
    )

    assert fidelity.is_faithful is True
