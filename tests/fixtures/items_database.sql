-- The test base of the reading of the items (#183), run on a base created by create_database.
-- Each row is taken from an extract of the collection (tests/fixtures/*_extract.*), as the import
-- would write it: dates in UTC, URLs without their utm_* parameters. A long text is cut, said so
-- where it is. A VARIANT is a value the extract does not have, written for the test named next
-- to it. The ids are given: the tests name them.

INSERT INTO raw_data (id, source_id, raw_file_path, stream, collected_at) VALUES
    (1, (SELECT id FROM source WHERE source_code = 'arxiv'),
        'automated_watch/arxiv_cscr_x_csai/2026-10-05T093000Z.xml', 'arxiv_cscr_x_csai',
        '2026-10-05T09:30:00Z'),
    (2, (SELECT id FROM source WHERE source_code = 'cisa_kev'),
        'automated_watch/cisa_kev/2026-10-05T093000Z.csv', 'cisa_kev', '2026-10-05T09:30:00Z'),
    (3, (SELECT id FROM source WHERE source_code = 'github'),
        'automated_watch/github_releases_anthropics_claude_code/2026-10-05T093000Z.json',
        'github_releases_anthropics_claude_code', '2026-10-05T09:30:00Z'),
    (4, (SELECT id FROM source WHERE source_code = 'github'),
        'automated_watch/github_advisories_pip/2026-10-05T093000Z.json', 'github_advisories_pip',
        '2026-10-05T09:30:00Z'),
    (5, (SELECT id FROM source WHERE source_code = 'tldr'),
        'automated_watch/tldr_ai/2026-10-11T153130Z/2026-10-07.html', 'tldr_ai',
        '2026-10-11T15:31:30Z'),
    (6, (SELECT id FROM source WHERE source_code = 'tldr'),
        'automated_watch/tldr_dev/2026-10-11T153130Z/2026-10-05.html', 'tldr_dev',
        '2026-10-11T15:31:30Z'),
    (7, (SELECT id FROM source WHERE source_code = 'zotero'),
        'selected_library/zotero/2026-10-05T093000Z.json', 'zotero', '2026-10-05T09:30:00Z'),
    (8, (SELECT id FROM source WHERE source_code = 'gh_archive'),
        'automated_watch/gh_archive/2026-10-11T093000Z.json', 'gh_archive',
        '2026-10-11T09:30:00Z'),
    (9, (SELECT id FROM source WHERE source_code = 'clipper'),
        'selected_library/clipping/2026-10-17T093000Z/0123456789abcdef.md', 'clipping',
        '2026-10-17T09:30:00Z');

INSERT INTO item (id, kind, raw_data_id) VALUES
    (1, 'paper', 1),
    (2, 'paper', 1),
    (3, 'vulnerability', 2),
    (4, 'vulnerability', 2),
    (5, 'vulnerability', 2),
    (6, 'release', 3),
    (7, 'release', 3),
    (8, 'release', 3),
    (9, 'advisory', 4),
    (10, 'advisory', 4),
    (11, 'newsletter_article', 5),
    (12, 'newsletter_article', 5),
    (13, 'newsletter_article', 6),
    (14, 'zotero_entry', 7),
    (15, 'zotero_entry', 7),
    (16, 'zotero_entry', 7),
    (17, 'advisory', 4),
    (18, 'clip', 9);

-- arxiv_extract.xml, the first two entries. The summary is cut to its first sentence, the
-- authors to the first name.
INSERT INTO publication (arxiv_id, item_id, title, summary, authors, submitted_at, revised_at)
VALUES
    ('2609.31282', 1,
        'Resource-Optimized and Energy-Aware Agentic AI Framework Anchored on Blockchain for Secure Software Supply Chains',
        'This paper proposes a blockchain-backed agentic security framework designed to safeguard the complete software development lifecycle (SDLC) while also securing the agentic AI components responsible for monitoring it.',
        '["Toqeer Ali Syed"]', '2026-09-25T13:53:50Z', '2026-09-25T13:53:50Z'),
    ('2609.33401', 2,
        'Evaluating System One Models for Agent Security Decisions: Reliability, Calibration, and Selective Automation',
        'Model-based judges support agent security by detecting prompt injections, assessing interaction risks, and screening harmful requests.',
        '["Yixuan Liu"]', '2026-09-27T09:30:13Z', '2026-09-29T16:41:24Z');

-- cisa_kev_extract.csv, lines 2, 3 and 5.
INSERT INTO vulnerability (
    cve_id, item_id, name, description, vendor, product, date_added, ransomware_use, revised_at,
    withdrawn_at
) VALUES
    ('CVE-2026-86950', 3, 'Apple Multiple Products Out-of-Bounds Write Vulnerability',
        'Apple iOS, macOS, and iPadOS contain an out-of-bounds write vulnerability in CoreGraphics that may lead to arbitrary code execution.',
        'Apple', 'Multiple Products', '2026-09-29T00:00:00Z', 'Unknown', NULL, NULL),
    -- VARIANT, revised_at: changed in a later catalog (the detail serves it).
    ('CVE-2026-88772', 4,
        'Citrix NetScaler Improper Restriction of Operations within the Bounds of a Memory Buffer Vulnerability',
        'Citrix NetScaler ADC and NetScaler Gateway contain an improper restriction of operations within the bounds of a memory buffer vulnerability that could allow for remote code execution or denial of service',
        'Citrix', 'NetScaler', '2026-09-27T00:00:00Z', 'Unknown', '2026-10-06T09:30:00Z', NULL),
    -- VARIANT, withdrawn_at: withdrawn from the catalog (a withdrawn row is listed).
    ('CVE-2020-29583', 5, 'Zyxel Multiple Products Use of Hard-Coded Credentials Vulnerability',
        'Zyxel firewalls (ATP, USG, VM) and AP Controllers (NXC2500 and NXC5500) contain a use of hard-coded credentials vulnerability in an undocumented account ("zyfwp") with an unchangeable password.',
        'Zyxel', 'Multiple Products', '2021-11-03T00:00:00Z', 'Unknown', NULL,
        '2026-10-06T09:30:00Z'),
    -- The CVE of advisory 17, cited by GitHub, not in the KEV catalog: no KEV column.
    ('CVE-2026-45134', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL);

-- GITHUB_TRACKED_REPOSITORIES (src/k_b/core/config.py).
INSERT INTO tracked_repository (id, repository_name, ecosystem, packages) VALUES
    (1, 'anthropics/claude-code', 'npm', '["@anthropic-ai/claude-code"]');

-- github_releases_extract.json, its three releases: each name equals its tag there. The bodies
-- are the extract's, cut at 200 characters as they are there.
INSERT INTO release (
    item_id, kind, tracked_repository_id, release_id, tag_name, name, body, published_at, url
) VALUES
    -- VARIANT, name: a name other than the tag (the title takes the name).
    (6, 'release', 1, 401174143, 'v2.1.287', 'Claude Code 2.1.287',
        '## What''s changed

- Added Claude Mods: plugins may now modify deeper behavior
- Added You should know, a built-in mod where a side agent watches your back and flags things you or Claude might miss. T',
        '2026-10-01T18:00:22Z', 'https://github.com/anthropics/claude-code/releases/tag/v2.1.287'),
    -- VARIANT, name: no name (the title takes the tag).
    (7, 'release', 1, 400266224, 'v2.1.286', NULL,
        '## What''s changed

- Added a count such as "2 of 5" to the permission prompt when several permission requests stack up
- Added mouse support for the "N more" rows of lists in fullscreen mode: click on',
        '2026-09-30T19:10:13Z', 'https://github.com/anthropics/claude-code/releases/tag/v2.1.286'),
    -- VARIANT, name: an empty name (the title takes the tag).
    (8, 'release', 1, 399375132, 'v2.1.285', '',
        '## What''s changed

- Added `CLAUDE_CODE_DISABLE_WEB_FETCH` environment variable to turn off the WebFetch tool
- Added `claude --desktop` to open the Claude desktop app on the current directory, or on ',
        '2026-09-29T19:27:30Z', 'https://github.com/anthropics/claude-code/releases/tag/v2.1.285');

-- github_advisories_extract.json, its three advisories; the packages of the third are given once,
-- as the import writes them.
INSERT INTO advisory (
    item_id, kind, ghsa_id, vulnerability_id, summary, published_at, url, packages
) VALUES
    -- VARIANT, the CVE: one of the KEV catalog, withdrawn since (exploited stays true).
    (9, 'advisory', 'GHSA-fjqc-hq36-qh5p',
        (SELECT id FROM vulnerability WHERE cve_id = 'CVE-2020-29583'),
        'LangGraph Checkpoint: Unsafe JSON deserialization in checkpoint loading',
        '2026-06-25T18:25:42Z', 'https://github.com/advisories/GHSA-fjqc-hq36-qh5p',
        '["langgraph-checkpoint"]'),
    -- VARIANT, the CVE: none (cve_id and exploited null).
    (10, 'advisory', 'GHSA-gr75-jv2w-4656', NULL,
        'LangChain: Path traversal and sandbox escape in LangChain file-search middleware and loaders',
        '2026-06-16T15:03:14Z', 'https://github.com/advisories/GHSA-gr75-jv2w-4656',
        '["langchain", "langchain-anthropic"]'),
    (17, 'advisory', 'GHSA-3644-q5cj-c5c7',
        (SELECT id FROM vulnerability WHERE cve_id = 'CVE-2026-45134'),
        'LangSmith SDK: Public prompt pull deserializes untrusted manifests without trust boundary warning',
        '2026-05-13T15:29:30Z', 'https://github.com/advisories/GHSA-3644-q5cj-c5c7',
        '["langsmith", "langchain-classic", "langchain"]');

-- tldr_ai_extract.html (edition of 2026-10-07), ranks 2 and 3: the same day, told apart by their
-- id; tldr_dev_extract.html (edition of 2026-10-05), rank 2.
INSERT INTO newsletter_article (item_id, kind, edition, edition_date, rank, title, summary, url)
VALUES
    (11, 'newsletter_article', 'ai', '2026-10-07T00:00:00Z', 2, 'Mistral Large 4 (9 minute read)',
        'Mistral Large 4 is a one-trillion-parameter multimodal model positioned as a European alternative to leading closed and open AI systems.',
        'https://mistral.ai/news/mistral-large-4/'),
    (12, 'newsletter_article', 'ai', '2026-10-07T00:00:00Z', 3,
        'Sharing AI progress in mathematics (2 minute read)',
        'OpenAI has released a broad range of mathematical results produced by an internal frontier model.',
        'https://openai.com/index/sharing-ai-progress-in-mathematics/'),
    (13, 'newsletter_article', 'dev', '2026-10-05T00:00:00Z', 2,
        'Write your Own Virtual Machine (35 minute read)',
        'This literate tutorial builds an LC-3 virtual machine in about 250 lines of C, covering registers, memory, opcodes, traps, loading programs, and platform-specific input.',
        'https://www.jmeiners.com/lc3-vm/');

-- The papers of the Zotero entries 14 and 15 (their archiveID in zotero_extract.sql), which arXiv
-- has not given: created with their arXiv reference alone, as the import does.
INSERT INTO publication (arxiv_id) VALUES ('2609.00001'), ('2609.00002');

-- zotero_extract.sql, items 1, 10 and 20.
INSERT INTO zotero_entry (
    item_id, kind, zotero_key, item_type, title, url, saved_at, publication_id, revised_at,
    withdrawn_at
) VALUES
    -- VARIANT, revised_at: changed in a later extract (the detail serves it).
    (14, 'zotero_entry', 'AAAAAAA1', 'preprint', 'Prompt injection through tool results',
        'http://arxiv.org/abs/2609.00001', '2026-10-01T08:15:00Z',
        (SELECT id FROM publication WHERE arxiv_id = '2609.00001'), '2026-10-06T09:30:00Z', NULL),
    -- VARIANT, withdrawn_at: absent from a later extract (a withdrawn row is listed).
    (15, 'zotero_entry', 'BBBBBBB2', 'preprint', 'A paper saved without its PDF',
        'http://arxiv.org/abs/2609.00002', '2026-10-03T17:40:12Z',
        (SELECT id FROM publication WHERE arxiv_id = '2609.00002'), NULL, '2026-10-06T09:30:00Z'),
    -- VARIANT: item 20 is in the trash, so not collected; it stands for an entry without URL,
    -- and without arXiv reference.
    (16, 'zotero_entry', 'CCCCCCC3', 'webpage', 'A web page in the trash', NULL,
        '2026-09-20T10:00:00Z', NULL, NULL, NULL);

-- zotero_extract.sql, the highlights of item 1: annotations 4 and 5.
INSERT INTO highlight (zotero_key, text, comment, highlighted_at, revised_at, withdrawn_at, item_id)
VALUES
    -- VARIANT, revised_at: changed in a later extract (the detail serves it).
    ('ANNOTAA1', 'A highlighted sentence on the third page.', NULL, '2026-10-05T08:50:00Z',
        '2026-10-06T09:30:00Z', NULL, 14),
    ('ANNOTAA2', 'A highlighted sentence on the first page.', 'A comment on it.',
        '2026-10-05T08:42:30Z', NULL, NULL, 14),
    -- VARIANT, withdrawn_at: annotation 7, in the trash, so absent from a later extract.
    ('ANNOTAA4', 'A highlight in the trash.', NULL, '2026-10-05T08:56:00Z', NULL,
        '2026-10-06T09:30:00Z', 14);

-- gh_archive_extract.json, its five rows (window of 2026-10-10: two repositories without stars,
-- told apart by their forks). VARIANT: an older window, 2026-10-09, written after the newer one so
-- that the latest is not the last written; its two rows tie on stars and forks, told apart by
-- repository_id.
INSERT INTO trend (
    raw_data_id, window_end, repository_id, repository_name, stars_gained, forks_gained
) VALUES
    (8, '2026-10-10T00:00:00Z', 1209966933, 'morluto/rea', 1087, 215),
    (8, '2026-10-10T00:00:00Z', 1398080271, 'storytold/photocraft', 449, 64),
    (8, '2026-10-10T00:00:00Z', 1322250666, 'boykopovar/AnyPS5', 318, 17),
    (8, '2026-10-10T00:00:00Z', 928981629, 'rhtap-perf-test/nodejs-devfile-sample2', 0, 13),
    (8, '2026-10-10T00:00:00Z', 1035334306, 'wwwwwwworld/solana-trading-bot-v3', 0, 70),
    (8, '2026-10-09T00:00:00Z', 1398080271, 'storytold/photocraft', 120, 9),
    (8, '2026-10-09T00:00:00Z', 1209966933, 'morluto/rea', 120, 9);

-- clipping/article_with_author.md, the clip of the vault of 2026-10-17 cut down; its text cut.
INSERT INTO clip (item_id, kind, url, title, body, saved_at, published_at) VALUES
    (18, 'clip',
        'https://medium.com/skillstuff/7-coding-patterns-i-stole-from-senior-engineers-c95f757e52a6',
        '7 Coding Patterns I Stole From Senior Engineers',
        'Most junior developers think senior engineers know more tricks.',
        '2026-09-17T00:00:00Z', '2026-06-08T00:00:00Z');
