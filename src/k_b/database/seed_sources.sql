-- K-B: reference data of the items database, run right after schema.sql.
-- Safe to run again: a source already present is left as is.

INSERT INTO source (source_code, label, source_type, acquisition_mode) VALUES
    ('github',     'API GitHub',              'web_service', 'watch'),
    ('arxiv',      'API arXiv',               'web_service', 'watch'),
    ('cisa_kev',   'Catalogue CISA KEV',      'file',        'watch'),
    ('clipper',    'Obsidian Web Clipper',    'file',        'library'),
    ('tldr',       'tldr.tech',               'scraping',    'watch'),
    ('zotero',     'Zotero',                  'database',    'library'),
    ('gh_archive', 'GH Archive sur BigQuery', 'big_data',    'watch')
ON CONFLICT (source_code) DO NOTHING;
