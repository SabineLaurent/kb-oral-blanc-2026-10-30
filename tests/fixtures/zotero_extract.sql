-- A fabricated Zotero library, built by tests/etl/sources/test_zotero.py. No real data: every value
-- is invented, in the forms observed on a real library on 2026-10-11 (keys of 8 characters,
-- dates as 'YYYY-MM-DD HH:MM:SS' in UTC, a paper date as 'YYYY-MM-DD YYYY-MM-DD', an archiveID
-- as 'arXiv:NNNN.NNNNN', a PDF path as 'storage:<file>', a sortIndex as 'NNNNN|NNNNNN|NNNNN').
--
-- The 15 tables the extraction reads (14) or must leave alone (itemNotes), rewritten with every
-- column, type, default, key and UNIQUE of the real base. Left out: its 44 other tables, its
-- indexes and triggers, and the foreign keys that point to a table left out (fieldsCombined,
-- fieldFormats, charsets).

CREATE TABLE libraries (
    libraryID      INTEGER PRIMARY KEY,
    type           TEXT NOT NULL,
    editable       INT NOT NULL,
    filesEditable  INT NOT NULL,
    version        INT NOT NULL DEFAULT 0,
    clientVersion  INT NOT NULL DEFAULT 0,
    storageVersion INT NOT NULL DEFAULT 0,
    lastSync       INT NOT NULL DEFAULT 0,
    archived       INT NOT NULL DEFAULT 0,
    isAdmin        INT NOT NULL DEFAULT 0
);

CREATE TABLE itemTypes (
    itemTypeID         INTEGER PRIMARY KEY,
    typeName           TEXT,
    templateItemTypeID INT,
    display            INT DEFAULT 1
);

CREATE TABLE items (
    itemID             INTEGER PRIMARY KEY,
    itemTypeID         INT NOT NULL,
    dateAdded          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    dateModified       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    clientDateModified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    libraryID          INT NOT NULL,
    key                TEXT NOT NULL,
    version            INT NOT NULL DEFAULT 0,
    clientVersion      INT NOT NULL DEFAULT 0,
    synced             INT NOT NULL DEFAULT 0,
    UNIQUE (libraryID, key),
    FOREIGN KEY (libraryID) REFERENCES libraries (libraryID) ON DELETE CASCADE
);

CREATE TABLE deletedItems (
    itemID      INTEGER PRIMARY KEY,
    dateDeleted DEFAULT CURRENT_TIMESTAMP NOT NULL,
    FOREIGN KEY (itemID) REFERENCES items (itemID) ON DELETE CASCADE
);

CREATE TABLE fields (
    fieldID       INTEGER PRIMARY KEY,
    fieldName     TEXT,
    fieldFormatID INT
);

CREATE TABLE itemDataValues (
    valueID         INTEGER PRIMARY KEY,
    value           UNIQUE,
    valueNormalized TEXT
);

CREATE TABLE itemData (
    itemID  INT,
    fieldID INT,
    valueID,
    PRIMARY KEY (itemID, fieldID),
    FOREIGN KEY (itemID) REFERENCES items (itemID) ON DELETE CASCADE,
    FOREIGN KEY (valueID) REFERENCES itemDataValues (valueID)
);

CREATE TABLE creators (
    creatorID           INTEGER PRIMARY KEY,
    firstName           TEXT,
    lastName            TEXT,
    fieldMode           INT,
    firstNameNormalized TEXT,
    lastNameNormalized  TEXT,
    UNIQUE (lastName, firstName, fieldMode)
);

CREATE TABLE creatorTypes (
    creatorTypeID INTEGER PRIMARY KEY,
    creatorType   TEXT
);

CREATE TABLE itemCreators (
    itemID        INT NOT NULL,
    creatorID     INT NOT NULL,
    creatorTypeID INT NOT NULL DEFAULT 1,
    orderIndex    INT NOT NULL DEFAULT 0,
    PRIMARY KEY (itemID, creatorID, creatorTypeID, orderIndex),
    UNIQUE (itemID, orderIndex),
    FOREIGN KEY (itemID) REFERENCES items (itemID) ON DELETE CASCADE,
    FOREIGN KEY (creatorID) REFERENCES creators (creatorID) ON DELETE CASCADE,
    FOREIGN KEY (creatorTypeID) REFERENCES creatorTypes (creatorTypeID)
);

CREATE TABLE tags (
    tagID          INTEGER PRIMARY KEY,
    name           TEXT NOT NULL UNIQUE,
    nameNormalized TEXT
);

CREATE TABLE itemTags (
    itemID INT NOT NULL,
    tagID  INT NOT NULL,
    type   INT NOT NULL,
    PRIMARY KEY (itemID, tagID),
    FOREIGN KEY (itemID) REFERENCES items (itemID) ON DELETE CASCADE,
    FOREIGN KEY (tagID) REFERENCES tags (tagID) ON DELETE CASCADE
);

CREATE TABLE itemAttachments (
    itemID                        INTEGER PRIMARY KEY,
    parentItemID                  INT,
    linkMode                      INT,
    contentType                   TEXT,
    charsetID                     INT,
    path                          TEXT,
    syncState                     INT DEFAULT 0,
    storageModTime                INT,
    storageHash                   TEXT,
    lastProcessedModificationTime INT,
    lastRead                      INT,
    FOREIGN KEY (itemID) REFERENCES items (itemID) ON DELETE CASCADE,
    FOREIGN KEY (parentItemID) REFERENCES items (itemID) ON DELETE CASCADE
);

CREATE TABLE itemAnnotations (
    itemID            INTEGER PRIMARY KEY,
    parentItemID      INT NOT NULL,
    type              INTEGER NOT NULL,
    authorName        TEXT,
    text              TEXT,
    textNormalized    TEXT,
    comment           TEXT,
    commentNormalized TEXT,
    color             TEXT,
    pageLabel         TEXT,
    sortIndex         TEXT NOT NULL,
    position          TEXT NOT NULL,
    isExternal        INT NOT NULL,
    FOREIGN KEY (itemID) REFERENCES items (itemID) ON DELETE CASCADE,
    FOREIGN KEY (parentItemID) REFERENCES itemAttachments (itemID)
);

CREATE TABLE itemNotes (
    itemID       INTEGER PRIMARY KEY,
    parentItemID INT,
    note         TEXT,
    title        TEXT,
    FOREIGN KEY (itemID) REFERENCES items (itemID) ON DELETE CASCADE,
    FOREIGN KEY (parentItemID) REFERENCES items (itemID) ON DELETE CASCADE
);

-- Reference rows: only the types, fields and creator types the fabricated library uses.
INSERT INTO libraries (libraryID, type, editable, filesEditable) VALUES
    (1, 'user', 1, 1),
    (2, 'group', 1, 1);

INSERT INTO itemTypes (itemTypeID, typeName) VALUES
    (1, 'annotation'),
    (3, 'attachment'),
    (28, 'note'),
    (31, 'preprint'),
    (40, 'webpage');

INSERT INTO fields (fieldID, fieldName) VALUES
    (1, 'title'),
    (2, 'abstractNote'),
    (6, 'date'),
    (13, 'url'),
    (19, 'archiveID');

INSERT INTO creatorTypes (creatorTypeID, creatorType) VALUES
    (8, 'author'),
    (10, 'editor');

-- Item 1: a paper with two authors and an editor, two tags, a PDF and its highlights.
-- Item 10: a paper with nothing attached that the extraction keeps.
-- Item 20: a web page in the trash. Item 30: a paper of a group library.
INSERT INTO items (itemID, itemTypeID, dateAdded, dateModified, libraryID, key) VALUES
    (1, 31, '2026-10-01 08:15:00', '2026-10-05 09:00:00', 1, 'AAAAAAA1'),
    (10, 31, '2026-10-03 17:40:12', '2026-10-03 17:40:12', 1, 'BBBBBBB2'),
    (20, 40, '2026-09-20 10:00:00', '2026-09-21 10:00:00', 1, 'CCCCCCC3'),
    (30, 31, '2026-09-25 11:30:00', '2026-09-25 11:30:00', 2, 'DDDDDDD4');

INSERT INTO deletedItems (itemID, dateDeleted) VALUES (20, '2026-09-21 10:00:00');

INSERT INTO itemDataValues (valueID, value) VALUES
    (1, 'Prompt injection through tool results'),
    (2, 'An abstract that the extraction does not read.'),
    (3, '2026-09-28 2026-09-28'),
    (4, 'http://arxiv.org/abs/2609.00001'),
    (5, 'arXiv:2609.00001'),
    (6, 'A paper saved without its PDF'),
    (7, '2026-09-30 2026-09-30'),
    (8, 'http://arxiv.org/abs/2609.00002'),
    (9, 'arXiv:2609.00002'),
    (10, 'A web page in the trash'),
    (11, 'A paper of a group library');

INSERT INTO itemData (itemID, fieldID, valueID) VALUES
    (1, 1, 1), (1, 2, 2), (1, 6, 3), (1, 13, 4), (1, 19, 5),
    (10, 1, 6), (10, 6, 7), (10, 13, 8), (10, 19, 9),
    (20, 1, 10),
    (30, 1, 11);

INSERT INTO creators (creatorID, firstName, lastName, fieldMode) VALUES
    (1, 'Blaise', 'Sample', 0),
    (2, 'Ada', 'Example', 0),
    (3, 'Edith', 'Editor', 0);

-- The second author is written first: the order comes from orderIndex.
INSERT INTO itemCreators (itemID, creatorID, creatorTypeID, orderIndex) VALUES
    (1, 1, 8, 1),
    (1, 2, 8, 0),
    (1, 3, 10, 2);

INSERT INTO tags (tagID, name) VALUES
    (1, 'to read'),
    (2, 'Computer Science - Cryptography and Security');

-- type 0: a tag set by hand; type 1: a tag set automatically when the paper was saved.
INSERT INTO itemTags (itemID, tagID, type) VALUES
    (1, 1, 0),
    (1, 2, 1);

-- The attachments, then the annotations and the note: each one is an item too.
INSERT INTO items (itemID, itemTypeID, dateAdded, dateModified, libraryID, key) VALUES
    (2, 3, '2026-10-01 08:15:04', '2026-10-01 08:15:04', 1, 'PDFAAAA1'),
    (3, 3, '2026-10-01 08:15:05', '2026-10-01 08:15:05', 1, 'HTMLAAA1'),
    (4, 1, '2026-10-05 08:50:00', '2026-10-05 08:50:00', 1, 'ANNOTAA1'),
    (5, 1, '2026-10-05 08:42:30', '2026-10-05 08:42:30', 1, 'ANNOTAA2'),
    (6, 1, '2026-10-05 08:55:00', '2026-10-05 08:55:00', 1, 'ANNOTAA3'),
    (7, 1, '2026-10-05 08:56:00', '2026-10-05 08:56:00', 1, 'ANNOTAA4'),
    (8, 28, '2026-10-05 09:00:00', '2026-10-05 09:00:00', 1, 'NOTEAAA1'),
    (9, 1, '2026-10-05 08:57:00', '2026-10-05 08:57:00', 1, 'ANNOTAA5'),
    (11, 3, '2026-10-03 17:40:15', '2026-10-03 17:40:15', 1, 'PDFBBBB1'),
    (12, 1, '2026-10-04 12:00:00', '2026-10-04 12:00:00', 1, 'ANNOTBB1'),
    (13, 3, '2026-10-03 17:41:00', '2026-10-03 17:41:00', 1, 'PDFBBBB2'),
    (14, 1, '2026-10-04 12:05:00', '2026-10-04 12:05:00', 1, 'ANNOTBB2');

-- linkMode 0: a file stored by Zotero; 1: a file downloaded from a URL and stored by Zotero;
-- 2: a file linked where it is, outside Zotero's storage.
INSERT INTO itemAttachments (itemID, parentItemID, linkMode, contentType, path) VALUES
    (2, 1, 0, 'application/pdf', 'storage:Example and Sample - 2026 - Prompt injection.pdf'),
    (3, 1, 1, 'text/html', 'storage:snapshot.html'),
    (11, 10, 0, 'application/pdf', 'storage:A PDF in the trash.pdf'),
    (13, 10, 2, 'application/pdf', '/Users/someone/Papers/A linked PDF.pdf');

-- A PDF in the trash, and a highlight in the trash.
INSERT INTO deletedItems (itemID, dateDeleted) VALUES
    (11, '2026-10-04 13:00:00'),
    (7, '2026-10-05 08:58:00');

-- type 1: a highlight; type 5: an underline. Item 4 comes later in the PDF than item 5.
INSERT INTO itemAnnotations (
    itemID, parentItemID, type, text, comment, color, pageLabel, sortIndex, position, isExternal
) VALUES
    (4, 2, 1, 'A highlighted sentence on the third page.', NULL, '#ffd400', '3',
        '00002|000100|00200', '{"pageIndex":2,"rects":[[70,500,300,512]]}', 0),
    (5, 2, 1, 'A highlighted sentence on the first page.', 'A comment on it.', '#ffd400', '1',
        '00000|000350|00120', '{"pageIndex":0,"rects":[[70,600,300,612]]}', 0),
    (6, 2, 5, 'An underlined sentence.', NULL, '#ffd400', '1',
        '00000|000400|00150', '{"pageIndex":0,"rects":[[70,570,300,582]]}', 0),
    (7, 2, 1, 'A highlight in the trash.', NULL, '#ffd400', '2',
        '00001|000050|00100', '{"pageIndex":1,"rects":[[70,650,300,662]]}', 0),
    (9, 3, 1, 'A highlight on a web page snapshot.', NULL, '#ffd400', '',
        '00000|000010|00000', '{"type":"CssSelector","value":"p"}', 0),
    (12, 11, 1, 'A highlight on a PDF in the trash.', NULL, '#ffd400', '1',
        '00000|000100|00100', '{"pageIndex":0,"rects":[[70,600,300,612]]}', 0),
    (14, 13, 1, 'A highlight on a linked PDF.', NULL, '#ffd400', '1',
        '00000|000100|00100', '{"pageIndex":0,"rects":[[70,600,300,612]]}', 0);

INSERT INTO itemNotes (itemID, parentItemID, note, title) VALUES
    (8, 1, '<p>A note that the extraction does not read.</p>', 'A note');
