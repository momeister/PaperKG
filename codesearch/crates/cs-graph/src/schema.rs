//! Database layout and migrations.
//!
//! Two decisions here shape everything above:
//!
//! **Source text is not stored.** The index keeps hashes and byte ranges; the
//! bytes stay on disk. A 400 MB monorepo therefore produces a ~40 MB index rather
//! than a second copy of the repository. Spans are read on demand and validated
//! against the stored hash, which doubles as the staleness check that citation
//! verification needs anyway.
//!
//! **Full-text search is contentless.** `text_index` is an FTS5 table declared
//! `content=''`, so it stores the trigram postings but not the text. A search
//! narrows tens of thousands of files down to a handful of candidates, and those
//! few are then scanned on disk for exact line hits. Fast queries, small file,
//! and results that are literally verified against the current bytes.

use rusqlite::Connection;

/// Bumped whenever the layout changes in a way that makes an existing index
/// unreadable. The app deletes and rebuilds rather than migrating: reindexing is
/// cheap and a half-migrated graph would silently produce wrong answers.
///
/// 2: test-method detection (``parse.rs`` now classifies a ``test_*`` *method*
///    as ``NodeKind::Test``, not just a free function). The layout is unchanged,
///    but an old index holds the wrong ``kind`` for half a test suite — the
///    Auswirkungsanalyse would report „keine Tests" against it.
pub const SCHEMA_VERSION: i32 = 2;

pub fn apply(conn: &Connection) -> rusqlite::Result<()> {
    // WAL lets the UI read while a background reindex writes. Without it every
    // query would block behind the indexer and the app would feel frozen.
    conn.pragma_update(None, "journal_mode", "WAL")?;
    conn.pragma_update(None, "synchronous", "NORMAL")?;
    conn.pragma_update(None, "foreign_keys", "ON")?;
    // 64 MB page cache. Generous enough for traversals to stay in memory on a
    // large repo, small enough not to matter on a laptop.
    conn.pragma_update(None, "cache_size", -64_000)?;
    conn.pragma_update(None, "temp_store", "MEMORY")?;

    conn.execute_batch(
        r#"
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    id          INTEGER PRIMARY KEY,   -- FileId, stable hash of the relative path
    path        TEXT    NOT NULL UNIQUE,
    lang        TEXT,                  -- NULL for files we index but cannot parse
    hash        TEXT    NOT NULL,      -- ContentHash hex at time of indexing
    size        INTEGER NOT NULL,
    lines       INTEGER NOT NULL,
    mtime       INTEGER NOT NULL,
    parsed      INTEGER NOT NULL DEFAULT 0,
    -- Why a file was not parsed: too_large, minified, generated, binary,
    -- unsupported. Surfaced in the UI so coverage gaps are visible, not silent.
    skip_reason TEXT
);

CREATE TABLE IF NOT EXISTS nodes (
    id             INTEGER PRIMARY KEY,   -- NodeId, stable across reindex
    file_id        INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    kind           TEXT    NOT NULL,
    lang           TEXT    NOT NULL,
    name           TEXT    NOT NULL,
    qualified      TEXT    NOT NULL,
    start_byte     INTEGER NOT NULL,
    end_byte       INTEGER NOT NULL,
    start_line     INTEGER NOT NULL,
    end_line       INTEGER NOT NULL,
    sig_start_byte INTEGER NOT NULL,
    sig_end_byte   INTEGER NOT NULL,
    sig_start_line INTEGER NOT NULL,
    sig_end_line   INTEGER NOT NULL,
    parent_id      INTEGER,
    visibility     TEXT    NOT NULL,
    doc            TEXT
);

CREATE INDEX IF NOT EXISTS nodes_file    ON nodes(file_id);
CREATE INDEX IF NOT EXISTS nodes_name    ON nodes(name);
CREATE INDEX IF NOT EXISTS nodes_kind    ON nodes(kind);
CREATE INDEX IF NOT EXISTS nodes_parent  ON nodes(parent_id);

CREATE TABLE IF NOT EXISTS edges (
    id            INTEGER PRIMARY KEY,   -- EdgeId
    from_id       INTEGER NOT NULL,
    to_id         INTEGER NOT NULL,
    kind          TEXT    NOT NULL,
    confidence    TEXT    NOT NULL,
    -- Numeric mirror of `confidence`, so an upsert can keep the stronger claim
    -- with a plain comparison instead of decoding the label in SQL.
    conf_rank     INTEGER NOT NULL,
    source        TEXT    NOT NULL,
    -- How many times this exact relationship appears. The edge is unique per
    -- (from, to, kind); a function calling the same target three times is one
    -- edge with three occurrences, and `evidence` points at the first site.
    occurrences   INTEGER NOT NULL DEFAULT 1,
    -- Evidence. NOT NULL throughout: an edge without proof cannot be written.
    ev_file       INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    ev_start_byte INTEGER NOT NULL,
    ev_end_byte   INTEGER NOT NULL,
    ev_start_line INTEGER NOT NULL,
    ev_end_line   INTEGER NOT NULL,
    ev_hash       TEXT    NOT NULL,
    candidates    INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS edges_from ON edges(from_id, kind);
CREATE INDEX IF NOT EXISTS edges_to   ON edges(to_id, kind);
CREATE INDEX IF NOT EXISTS edges_file ON edges(ev_file);

-- Unresolved references and imports, exactly as the parser saw them.
--
-- Storing these is what makes reindexing incremental. Resolution is global — one
-- new file can change what a name in another file refers to — so it must see
-- every reference in the workspace. Keeping them here means editing one file
-- reparses one file and then re-resolves from the database, instead of
-- reparsing the entire repository to rebuild the same information.
CREATE TABLE IF NOT EXISTS refs (
    file_id    INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    from_id    INTEGER NOT NULL,
    name       TEXT    NOT NULL,
    receiver   TEXT,
    kind       TEXT    NOT NULL,
    start_byte INTEGER NOT NULL,
    end_byte   INTEGER NOT NULL,
    start_line INTEGER NOT NULL,
    end_line   INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS refs_file ON refs(file_id);
CREATE INDEX IF NOT EXISTS refs_name ON refs(name);

CREATE TABLE IF NOT EXISTS imports (
    file_id    INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    module     TEXT    NOT NULL,
    symbol     TEXT,
    alias      TEXT,
    start_byte INTEGER NOT NULL,
    end_byte   INTEGER NOT NULL,
    start_line INTEGER NOT NULL,
    end_line   INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS imports_file ON imports(file_id);

-- Variables bound to a constructor call; see cs_core::RawBinding. Stored for
-- the same reason as refs: resolution must run from the database, not from a
-- reparse of the whole workspace.
CREATE TABLE IF NOT EXISTS bindings (
    file_id     INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    variable    TEXT    NOT NULL,
    constructor TEXT    NOT NULL,
    start_byte  INTEGER NOT NULL,
    end_byte    INTEGER NOT NULL,
    start_line  INTEGER NOT NULL,
    end_line    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS bindings_file ON bindings(file_id);

CREATE TABLE IF NOT EXISTS facts (
    node_id INTEGER PRIMARY KEY REFERENCES nodes(id) ON DELETE CASCADE,
    json    TEXT NOT NULL
);

-- Ranking signals, recomputed as a whole after indexing. Split from nodes so a
-- rank pass never rewrites the (much larger) node rows.
CREATE TABLE IF NOT EXISTS metrics (
    node_id      INTEGER PRIMARY KEY REFERENCES nodes(id) ON DELETE CASCADE,
    pagerank     REAL    NOT NULL DEFAULT 0,
    fan_in       INTEGER NOT NULL DEFAULT 0,
    fan_out      INTEGER NOT NULL DEFAULT 0,
    reach_depth  INTEGER,               -- hops from the nearest entry point
    churn        INTEGER NOT NULL DEFAULT 0,
    risk         INTEGER NOT NULL DEFAULT 0,
    authors      INTEGER NOT NULL DEFAULT 0,
    last_touched INTEGER,
    coverage     REAL,
    hits         INTEGER,               -- phase 2: observed calls
    relevance    REAL    NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS metrics_relevance ON metrics(relevance DESC);

-- User annotations. Keyed by the stable NodeId so they survive reindexing.
CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id    INTEGER NOT NULL,
    body       TEXT    NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS notes_node ON notes(node_id);

-- Model prose, cached per (symbol, content, model, level) so a re-explain is
-- free and an edited function invalidates only its own entry.
CREATE TABLE IF NOT EXISTS explanations (
    node_id      INTEGER NOT NULL,
    content_hash TEXT    NOT NULL,
    model        TEXT    NOT NULL,
    level        TEXT    NOT NULL,
    body         TEXT    NOT NULL,
    created_at   INTEGER NOT NULL,
    PRIMARY KEY (node_id, content_hash, model, level)
);

CREATE TABLE IF NOT EXISTS trails (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT    NOT NULL,
    question   TEXT    NOT NULL,
    steps_json TEXT    NOT NULL,
    created_at INTEGER NOT NULL
);

-- Symbol lookup. Trigram tokenisation makes `disc` match `apply_discount`,
-- which plain FTS5 word tokenisation would miss.
CREATE VIRTUAL TABLE IF NOT EXISTS symbol_search USING fts5(
    name, qualified, path,
    tokenize = 'trigram',
    prefix = '2 3'
);

-- Contentless: postings only, no copy of the source. Rowid is the file id.
-- `contentless_delete` is what makes incremental reindexing possible at all —
-- without it a contentless table can only be deleted from by supplying the
-- original text, which by design we no longer have.
CREATE VIRTUAL TABLE IF NOT EXISTS text_index USING fts5(
    body,
    content = '',
    contentless_delete = 1,
    tokenize = 'trigram'
);
"#,
    )?;

    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?1)",
        [SCHEMA_VERSION.to_string()],
    )?;
    Ok(())
}

/// Reads the schema version of an existing database. `None` means the file is
/// not one of ours or predates versioning.
pub fn version_of(conn: &Connection) -> Option<i32> {
    conn.query_row("SELECT value FROM meta WHERE key = 'schema_version'", [], |row| {
        row.get::<_, String>(0)
    })
    .ok()
    .and_then(|s| s.parse().ok())
}
