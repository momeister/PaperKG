//! The code graph: a SQLite-backed store of nodes, edges and the evidence behind
//! them, plus the traversal queries every view and every LLM tool runs against.
//!
//! Nothing else in CodeSearch talks to SQLite. Views, the chat tool loop and the
//! MCP server all go through [`Graph`], which is what keeps "the graph is the
//! single source of truth" true in practice rather than only on the whiteboard.

pub mod schema;

mod cluster;
mod cycles;
mod hotspots;
mod query;
mod write;

pub use cluster::{ClusterEdge, ClusterLevel, ClusterNode};
pub use cycles::{Cycle, CycleEdge, CycleLevel, CycleNode};
pub use hotspots::{Hotspot, HotspotRule, HotspotThresholds};
pub use query::{
    Direction, GraphSlice, Metrics, Neighbour, NodeDetail, SliceEdge, SpanRead, StoredFile,
    SymbolHit, TextHit,
};
pub use write::{FileRecord, WriteBatch};

use anyhow::{Context, Result};
use rusqlite::Connection;
use std::path::{Path, PathBuf};

/// An opened index.
///
/// Cheap to construct and safe to hold for the life of the app. Concurrent reads
/// while the indexer writes are handled by WAL mode, not by locking here.
pub struct Graph {
    conn: Connection,
    /// Workspace root, kept so spans can be read back off disk without every
    /// caller having to carry it.
    root: PathBuf,
}

impl Graph {
    /// Opens (or creates) the index for `root` at `db_path`.
    ///
    /// An index written by an older schema is deleted and rebuilt rather than
    /// migrated — see [`schema::SCHEMA_VERSION`].
    pub fn open(root: impl AsRef<Path>, db_path: impl AsRef<Path>) -> Result<Self> {
        let db_path = db_path.as_ref();
        let root = root.as_ref().to_path_buf();

        if db_path.exists() {
            let existing = Connection::open(db_path)
                .ok()
                .and_then(|c| schema::version_of(&c));
            if existing != Some(schema::SCHEMA_VERSION) {
                tracing::info!(
                    found = ?existing,
                    expected = schema::SCHEMA_VERSION,
                    "index schema outdated, rebuilding from scratch"
                );
                remove_database(db_path)?;
            }
        }

        if let Some(parent) = db_path.parent() {
            std::fs::create_dir_all(parent)
                .with_context(|| format!("creating index directory {}", parent.display()))?;
        }

        let conn = Connection::open(db_path)
            .with_context(|| format!("opening index at {}", db_path.display()))?;
        schema::apply(&conn).context("applying index schema")?;

        Ok(Self { conn, root })
    }

    /// An index that lives only in memory. Used by tests and by the fixture
    /// runner, where writing a file would just slow things down.
    pub fn open_in_memory(root: impl AsRef<Path>) -> Result<Self> {
        let conn = Connection::open_in_memory()?;
        schema::apply(&conn)?;
        Ok(Self { conn, root: root.as_ref().to_path_buf() })
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn connection(&self) -> &Connection {
        &self.conn
    }

    /// Number of files, parsed files and nodes — the headline the UI shows after
    /// indexing finishes.
    pub fn stats(&self) -> Result<Stats> {
        let files: i64 = self.conn.query_row("SELECT count(*) FROM files", [], |r| r.get(0))?;
        let parsed: i64 =
            self.conn.query_row("SELECT count(*) FROM files WHERE parsed = 1", [], |r| r.get(0))?;
        let nodes: i64 = self.conn.query_row("SELECT count(*) FROM nodes", [], |r| r.get(0))?;
        let edges: i64 = self.conn.query_row("SELECT count(*) FROM edges", [], |r| r.get(0))?;
        let guessed: i64 = self.conn.query_row(
            "SELECT count(*) FROM edges WHERE confidence = 'guessed'",
            [],
            |r| r.get(0),
        )?;
        let gaps: i64 = self.conn.query_row(
            "SELECT count(*) FROM nodes WHERE kind = 'dynamic_gap'",
            [],
            |r| r.get(0),
        )?;
        Ok(Stats {
            files: files as u64,
            parsed_files: parsed as u64,
            nodes: nodes as u64,
            edges: edges as u64,
            guessed_edges: guessed as u64,
            dynamic_gaps: gaps as u64,
        })
    }
}

/// Removes a SQLite database and its WAL sidecars. Leaving `-wal` behind would
/// make the next open resurrect data from the schema we just rejected.
fn remove_database(db_path: &Path) -> Result<()> {
    for suffix in ["", "-wal", "-shm"] {
        let mut p = db_path.as_os_str().to_owned();
        p.push(suffix);
        let p = PathBuf::from(p);
        if p.exists() {
            std::fs::remove_file(&p)
                .with_context(|| format!("removing stale index file {}", p.display()))?;
        }
    }
    Ok(())
}

#[derive(Debug, Clone, Copy, serde::Serialize)]
pub struct Stats {
    pub files: u64,
    pub parsed_files: u64,
    pub nodes: u64,
    pub edges: u64,
    /// How much of the graph is tier C. Shown in the UI as an honesty gauge —
    /// a repo where 80% of edges are guesses is one you should trust less.
    pub guessed_edges: u64,
    /// Places where static analysis knowingly gave up.
    pub dynamic_gaps: u64,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_fresh_index_is_empty_but_valid() {
        let graph = Graph::open_in_memory("/tmp/nowhere").unwrap();
        let stats = graph.stats().unwrap();
        assert_eq!(stats.files, 0);
        assert_eq!(schema::version_of(graph.connection()), Some(schema::SCHEMA_VERSION));
    }

    #[test]
    fn an_outdated_index_is_rebuilt_rather_than_read() {
        let dir = tempfile::tempdir().unwrap();
        let db = dir.path().join("index.csdb");

        {
            let conn = Connection::open(&db).unwrap();
            schema::apply(&conn).unwrap();
            conn.execute("UPDATE meta SET value = '-1' WHERE key = 'schema_version'", [])
                .unwrap();
        }

        let graph = Graph::open(dir.path(), &db).unwrap();
        assert_eq!(schema::version_of(graph.connection()), Some(schema::SCHEMA_VERSION));
    }
}
