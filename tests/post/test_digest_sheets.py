"""The sheets of the digests in the vault, one per subject and per day, a Markdown that only says
text; the responses are made by the schema of the AI API."""

import json
import re
from datetime import UTC, date, datetime

import pytest
from post.test_ai_api_client import (
    AGENT_DIGEST,
    PAPER_SOURCE,
    RELEASE_SOURCE,
    UNTIL,
    make_response,
    to_body,
)

from k_b.ai_api.schemas.digest_schema import ClaimSchema, WindowSchema
from k_b.post import digest_sheets
from k_b.post.ai_api_client import DigestsResponse
from k_b.post.digest_sheets import (
    neutralize_digest_text,
    refuse_written_sheets,
    render_digest_sheet,
    write_digest_sheet,
)
from k_b.post.vault_files import VaultEntryRefusedError

DAY = date(2026, 10, 21)
SUBJECT_NAMES = ("security_alerts", "agent_tools", "research")
# A bidirectional control, written by its code: never as itself in a source file.
RIGHT_TO_LEFT_OVERRIDE = chr(0x202E)


def read(response):
    return DigestsResponse.model_validate_json(to_body(response))


def read_one_digest(**changes):
    response = read(make_response([AGENT_DIGEST.model_copy(update=changes)]))
    return response, response.digests[0]


@pytest.fixture
def vault_dir(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


def list_files(directory):
    return sorted(
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file()
    )


def test_a_digest_gives_the_sheet_of_its_subject_and_of_the_day_of_its_window(
    vault_dir,
):
    response = read(make_response())

    sheet = write_digest_sheet(vault_dir, response, response.digests[0])

    assert sheet == "veille/agent_tools/2026-10-21.md"
    assert list_files(vault_dir) == [sheet]
    assert (vault_dir / sheet).read_text("utf-8") == (
        "---\n"
        "subject: agent_tools\n"
        "generated: 2026-10-21T16:30:00Z\n"
        'model: "gpt-6-luna"\n'
        "window_days: 3\n"
        "since: 2026-10-18T16:30:00Z\n"
        "until: 2026-10-21T16:30:00Z\n"
        "items_capped: false\n"
        "capped_kind: null\n"
        "covered_after: null\n"
        "request_id: 1b4e28ba-2fa1-4d2e-9b5c-6c8e1f0a7d3e\n"
        "---\n"
        "\n"
        "# Veille agent_tools du 2026-10-21\n"
        "\n"
        "- Claude Code publie la version v2.1.287. (source 1)\n"
        "- Un cadre d'agents ancré sur une blockchain protège la chaîne logicielle."
        " (sources 1, 2)\n"
        "\n"
        "## Sources\n"
        "\n"
        "1. Claude Code 2.1.287"
        " — <https://github.com/anthropics/claude-code/releases/tag/v2.1.287>\n"
        "2. Resource-Optimized and Energy-Aware Agentic AI Framework Anchored on Blockchain"
        " for Secure Software Supply Chains — <https://arxiv.org/abs/2609.31282>\n"
    )


def test_the_same_response_gives_the_same_bytes():
    response = read(make_response())

    assert render_digest_sheet(response, response.digests[0]) == render_digest_sheet(
        read(make_response()), read(make_response()).digests[0]
    )


def test_a_capped_window_names_the_kind_cut_and_the_date_after_which_it_is_read():
    response = read(
        make_response(
            window=WindowSchema(
                days=3,
                since=datetime(2026, 10, 18, 16, 30, tzinfo=UTC),
                until=UNTIL,
                capped_kind="vulnerability",
                covered_after=datetime(2026, 10, 19, 8, 0, tzinfo=UTC),
            ),
            items_capped=True,
        )
    )

    assert (
        "\nitems_capped: true\n"
        "capped_kind: vulnerability\n"
        "covered_after: 2026-10-19T08:00:00Z\n"
    ) in render_digest_sheet(response, response.digests[0])


def test_the_day_of_the_sheet_is_the_utc_day_of_the_end_of_the_window(vault_dir):
    body = json.loads(to_body(make_response()))
    body["window"]["until"] = "2026-10-22T01:30:00+02:00"
    response = DigestsResponse.model_validate_json(json.dumps(body))

    sheet = write_digest_sheet(vault_dir, response, response.digests[0])

    assert sheet == "veille/agent_tools/2026-10-21.md"
    assert "\ngenerated: 2026-10-21T23:30:00Z\n" in (vault_dir / sheet).read_text(
        "utf-8"
    )


def test_a_digest_without_claim_says_so_and_lists_no_source():
    response, digest = read_one_digest(claims=[], sources=[])

    assert render_digest_sheet(response, digest).endswith(
        "# Veille agent_tools du 2026-10-21\n\nAucune affirmation retenue.\n"
    )


def test_the_facts_of_a_claim_are_never_written():
    body = json.loads(to_body(make_response()))
    body["digests"][0]["claims"][0]["facts"] = [
        {"value": "FACT-OF-THE-TEST", "item_id": "i6"}
    ]
    response = DigestsResponse.model_validate_json(json.dumps(body))

    assert "FACT-OF-THE-TEST" not in render_digest_sheet(response, response.digests[0])


def test_a_trapped_title_is_written_as_text():
    title = (
        "[x](javascript:alert(1)) <img src=x onerror=alert(1)> ![a](b) [[note]] #tag"
        ' %%hidden%% $x$ `$= "TÉMOIN POSITIF"` key:: value ~~~ www.evil.example.invalid'
        " WWW.evil.example.invalid https://evil.example.invalid/p mail@example.invalid"
        f"\n---\n# heading {RIGHT_TO_LEFT_OVERRIDE}end"
    )
    response, digest = read_one_digest(
        claims=[ClaimSchema(text="Une affirmation.", item_ids=[6])],
        sources=[RELEASE_SOURCE.model_copy(update={"title": title})],
    )

    lines = render_digest_sheet(response, digest).splitlines()

    assert lines[-1] == (
        "1. \\[x\\](javascript\\:alert(1)) \\<img src=x onerror=alert(1)\\> !\\[a\\](b)"
        ' \\[\\[note\\]\\] \\#tag \\%\\%hidden\\%\\% \\$x\\$ \\`\\$= "TÉMOIN POSITIF"\\`'
        " key\\: \\: value \\~\\~\\~ www\\.evil.example.invalid WWW\\.evil.example.invalid"
        " https\\://evil.example.invalid/p mail\\@example.invalid --- \\# heading end"
        " — <https://github.com/anthropics/claude-code/releases/tag/v2.1.287>"
    )
    assert [line for line in lines if line == "---"] == ["---", "---"]


def test_a_trapped_claim_is_written_as_text_on_its_own_line():
    text = (
        '```dataviewjs\ndv.paragraph("TÉMOIN POSITIF")\n```\n~~~\n---\n> [!note] callout'
        ' [[note]] ![a](b) <b>bold</b> `$= "TÉMOIN POSITIF"` key:: value'
        " http://evil.example.invalid www.evil.example.invalid"
    )
    response, digest = read_one_digest(
        claims=[ClaimSchema(text=text, item_ids=[6])], sources=[RELEASE_SOURCE]
    )

    lines = render_digest_sheet(response, digest).splitlines()

    assert lines[15] == (
        '- \\`\\`\\`dataviewjs dv.paragraph("TÉMOIN POSITIF") \\`\\`\\` \\~\\~\\~ ---'
        " \\> \\[!note\\] callout \\[\\[note\\]\\] !\\[a\\](b) \\<b\\>bold\\</b\\>"
        ' \\`\\$= "TÉMOIN POSITIF"\\` key\\: \\: value http\\://evil.example.invalid'
        " www\\.evil.example.invalid (source 1)"
    )
    assert lines[14:17] == ["", lines[15], ""]


@pytest.mark.parametrize(
    ("text", "neutralized"),
    [
        ("www.example.invalid", "www\\.example.invalid"),
        ("Www.example.invalid and wWw.x", "Www\\.example.invalid and wWw\\.x"),
        ("www\\.example.invalid", "www\\\\.example.invalid"),
        (
            "mailto:a@b.invalid xmpp:a@b.invalid",
            "mailto\\:a\\@b.invalid xmpp\\:a\\@b.invalid",
        ),
        ("C++ 50 % #209 < 2.3.1", "C++ 50 \\% \\#209 \\< 2.3.1"),
    ],
)
def test_a_text_of_a_digest_escapes_the_autolinks_too(text, neutralized):
    assert neutralize_digest_text(text) == neutralized


@pytest.mark.parametrize(
    ("text", "neutralized"),
    [
        ("- urgent", "\\- urgent"),
        ("+ plus", "\\+ plus"),
        ("* étoile", "\\* étoile"),
        ("-", "\\-"),
        ("3.", "3\\."),
        ("2026. Year in review", "2026\\. Year in review"),
        ("1) premier", "1\\) premier"),
        ("\n  - après un retour à la ligne", "\\- après un retour à la ligne"),
        ("1234567890. dix chiffres", "1234567890. dix chiffres"),
        ("-tiret collé", "-tiret collé"),
        ("2026 Year", "2026 Year"),
        ("a - b", "a - b"),
        ("---", "---"),
        ("***", "***"),
        ("___", "___"),
        ("===", "==="),
    ],
)
def test_a_list_marker_at_the_start_of_a_text_is_escaped(text, neutralized):
    """A claim starts a list item, a title a numbered line: its own marker would nest a list
    (CommonMark 0.31.2, 5.2 and 5.3)."""
    assert neutralize_digest_text(text) == neutralized


@pytest.mark.parametrize("text", ["---", "***", "___", "==="])
def test_a_rule_or_an_underline_alone_stays_text_on_the_line_of_its_claim_or_source(
    text,
):
    """Never alone on its line: the claim ends with its sources, the title with its URL."""
    response, digest = read_one_digest(
        claims=[ClaimSchema(text=text, item_ids=[6])],
        sources=[RELEASE_SOURCE.model_copy(update={"title": text})],
    )

    lines = render_digest_sheet(response, digest).splitlines()

    assert f"- {text} (source 1)" in lines
    assert lines[-1] == f"1. {text} — <{RELEASE_SOURCE.url}>"


def render_escapes(markdown):
    """The text CommonMark renders from a line of escaped text, where Dataview reads its inline
    fields in reading mode."""
    return re.sub(r"\\([!-/:-@\[-`{-~])", r"\1", markdown)


@pytest.mark.parametrize(
    ("text", "neutralized"),
    [
        ("[champ:: valeur]", "\\[champ\\: \\: valeur\\]"),
        ("(clé:: valeur)", "(clé\\: \\: valeur)"),
        ("clé:: valeur", "clé\\: \\: valeur"),
        ("std::string", "std\\: \\:string"),
        ("a:::b", "a\\: \\: \\:b"),
        ("note: une seule", "note\\: une seule"),
    ],
)
def test_a_double_colon_is_never_rendered(text, neutralized):
    """Dataview takes [key:: value], (key:: value) and key:: value for a field, looking for :: in
    the rendered text, where the escapes are gone."""
    assert neutralize_digest_text(text) == neutralized
    assert "::" not in render_escapes(neutralize_digest_text(text))


@pytest.mark.parametrize(
    "field", ["[champ:: valeur]", "(clé:: valeur)", "clé:: valeur"]
)
def test_no_field_of_dataview_is_rendered_from_a_title_or_a_claim(field):
    response, digest = read_one_digest(
        claims=[ClaimSchema(text=field, item_ids=[6])],
        sources=[RELEASE_SOURCE.model_copy(update={"title": field})],
    )

    sheet = render_digest_sheet(response, digest)

    assert "::" not in sheet
    assert "::" not in render_escapes(sheet)


@pytest.mark.parametrize(
    ("text", "neutralized"),
    [
        # The # of a numeric reference was escaped already; a named one needs the &.
        ("droite&#x202E;gauche", "droite\\&\\#x202E;gauche"),
        ("droite&#8238;gauche", "droite\\&\\#8238;gauche"),
        ("marque&rlm;et&lrm;fin", "marque\\&rlm;et\\&lrm;fin"),
        ("&copy; &amp; &frac12;", "\\&copy; \\&amp; \\&frac12;"),
        # Only the & of a reference: one rule for both writers of the vault (decision of
        # Sabine of 2026-10-21, relayed by time-knight).
        ("R&D", "R&D"),
        (
            "https://example.invalid/?q=a&page=2",
            "https\\://example.invalid/?q=a&page=2",
        ),
    ],
)
def test_a_character_reference_is_written_as_text(text, neutralized):
    """CommonMark decodes &#x202E; or &rlm; into a bidirectional control: the & that opens a
    reference is escaped, another & stays."""
    assert neutralize_digest_text(text) == neutralized


def test_a_character_reference_in_a_title_and_a_claim_is_written_as_text():
    response, digest = read_one_digest(
        claims=[
            ClaimSchema(text="Une affirmation &#x202E;inversée&rlm;.", item_ids=[6])
        ],
        sources=[
            RELEASE_SOURCE.model_copy(update={"title": "Titre &#x202E;inversé&rlm;"})
        ],
    )

    lines = render_digest_sheet(response, digest).splitlines()

    assert "- Une affirmation \\&\\#x202E;inversée\\&rlm;. (source 1)" in lines
    assert lines[-1] == (
        f"1. Titre \\&\\#x202E;inversé\\&rlm; — <{RELEASE_SOURCE.url}>"
    )


@pytest.mark.parametrize(
    "url",
    [
        None,
        "javascript:alert(1)",
        "ftp://example.invalid/x",
        "https://example.invalid/a b",
        "https://example.invalid/a>b",
        "https://example.invalid/a<b",
        "https://example.invalid/a\\b",
        "https://example.invalid/a`b`",
        "https://example.invalid/a|b",
        "https://example.invalid/%%comment",
        "https://example.invalid/[[note]]",
        "https://example.invalid/$x$",
        # A character reference, or a percent-encoding of a control or format character,
        # would show U+202E in the link text (second code review, finding 2): CWE-451.
        "https://docs.python.org&#x202E;@evil.example/",
        "https://example.invalid/&#8238;a",
        "https://example.invalid/&rlm;a",
        "https://example.invalid/%E2%80%AEgpj.exe",
        "https://example.invalid/%E2%80%8Fa",
        "https://example.invalid/%00a",
        "https://example.invalid/%0Aa",
        "https://example.invalid/%ED%A0%80a",
        "https://example.invalid/%FFa",
        # Dataview would take (a::b) in the rendered line for a field (security review).
        "https://example.invalid/(a::b)",
        "https://evil.example.invalid/a(status::done)",
        "https://exämple.invalid/",
        f"https://example.invalid/{RIGHT_TO_LEFT_OVERRIDE}a",
        "HTTPS://example.invalid/",
    ],
)
def test_a_url_out_of_the_rule_is_not_written(url):
    response, digest = read_one_digest(
        claims=[ClaimSchema(text="Une affirmation.", item_ids=[6])],
        sources=[RELEASE_SOURCE.model_copy(update={"url": url})],
    )

    assert render_digest_sheet(response, digest).endswith(
        "\n1. Claude Code 2.1.287 (sans URL)\n"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/advisories/GHSA-2c8m-cx5r-f9x8",
        "http://example.invalid/a?b=c&d=%20e;f=(g)#h",
        "https://example.invalid/~user/@y/!'*+,=",
        "https://example.invalid/search?a=1&b=2",
        "https://example.invalid/a%20b/%C3%A9t%C3%A9",
        "https://fr.wikipedia.org/wiki/Python_(langage)",
    ],
)
def test_a_url_of_the_rule_is_written_as_an_autolink(url):
    response, digest = read_one_digest(
        claims=[ClaimSchema(text="Une affirmation.", item_ids=[6])],
        sources=[RELEASE_SOURCE.model_copy(update={"url": url})],
    )

    assert render_digest_sheet(response, digest).endswith(
        f"\n1. Claude Code 2.1.287 — <{url}>\n"
    )


def test_no_sheet_of_the_day_lets_the_request_go_and_creates_nothing(vault_dir):
    refuse_written_sheets(vault_dir, SUBJECT_NAMES, DAY)

    assert list(vault_dir.iterdir()) == []


def test_a_sheet_of_the_day_refuses_the_request(vault_dir):
    sheet = vault_dir / "veille" / "research" / "2026-10-21.md"
    sheet.parent.mkdir(parents=True)
    sheet.write_bytes(b"read by Sabine\n")

    with pytest.raises(
        VaultEntryRefusedError,
        match="^sheet_exists: veille/research/2026-10-21.md is there;"
        " kb digest never rewrites a sheet$",
    ):
        refuse_written_sheets(vault_dir, SUBJECT_NAMES, DAY)

    assert sheet.read_bytes() == b"read by Sabine\n"


def test_a_sheet_of_another_day_lets_the_request_go(vault_dir):
    sheet = vault_dir / "veille" / "research" / "2026-10-20.md"
    sheet.parent.mkdir(parents=True)
    sheet.write_bytes(b"yesterday\n")

    refuse_written_sheets(vault_dir, SUBJECT_NAMES, DAY)


@pytest.mark.parametrize("linked", ["veille", "veille/agent_tools"])
def test_a_folder_linked_elsewhere_refuses_the_request(vault_dir, tmp_path, linked):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link = vault_dir / linked
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(
        VaultEntryRefusedError,
        match=f"^vault_link_refused: {linked} is a symbolic link$",
    ):
        refuse_written_sheets(vault_dir, SUBJECT_NAMES, DAY)


def test_a_sheet_linked_elsewhere_refuses_the_request(vault_dir, tmp_path):
    sheet = vault_dir / "veille" / "agent_tools" / "2026-10-21.md"
    sheet.parent.mkdir(parents=True)
    sheet.symlink_to(tmp_path / "outside.md")

    with pytest.raises(VaultEntryRefusedError, match="^vault_link_refused: "):
        refuse_written_sheets(vault_dir, SUBJECT_NAMES, DAY)


def test_a_file_in_place_of_the_folder_of_a_subject_raises_an_os_error(vault_dir):
    (vault_dir / "veille").mkdir()
    (vault_dir / "veille" / "research").write_bytes(b"a file\n")

    with pytest.raises(NotADirectoryError):
        refuse_written_sheets(vault_dir, SUBJECT_NAMES, DAY)


def test_a_sheet_written_meanwhile_is_never_rewritten(vault_dir):
    response = read(make_response())
    sheet = vault_dir / "veille" / "agent_tools" / "2026-10-21.md"
    sheet.parent.mkdir(parents=True)
    sheet.write_bytes(b"written meanwhile\n")

    with pytest.raises(
        VaultEntryRefusedError,
        match="^sheet_exists: veille/agent_tools/2026-10-21.md is there;"
        " kb digest never rewrites a sheet$",
    ):
        write_digest_sheet(vault_dir, response, response.digests[0])

    assert sheet.read_bytes() == b"written meanwhile\n"


def test_a_sheet_seen_by_the_writing_is_refused_without_a_second_look(
    vault_dir, monkeypatch
):
    """write_new_sheet saw the sheet: kb never says it wrote it, even if it is gone since."""
    response = read(make_response())
    monkeypatch.setattr(digest_sheets, "write_new_sheet", lambda *arguments: False)

    with pytest.raises(VaultEntryRefusedError, match="^sheet_exists: "):
        write_digest_sheet(vault_dir, response, response.digests[0])

    assert list_files(vault_dir) == []


def test_a_folder_linked_after_the_check_is_refused_before_the_sheet(
    vault_dir, tmp_path
):
    response = read(make_response())
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (vault_dir / "veille").symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(VaultEntryRefusedError, match="^vault_link_refused: veille "):
        write_digest_sheet(vault_dir, response, response.digests[0])

    assert list_files(elsewhere) == []


def test_the_sources_are_numbered_in_their_order_and_cited_by_number():
    response, digest = read_one_digest(
        claims=[ClaimSchema(text="Deux sources.", item_ids=[1, 6])],
        sources=[PAPER_SOURCE, RELEASE_SOURCE],
    )

    assert "\n- Deux sources. (sources 1, 2)\n" in render_digest_sheet(response, digest)
