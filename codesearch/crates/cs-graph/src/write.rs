//! Writing into the index.
//!
//! All writes go through a [`WriteBatch`], which is one SQLite transaction. The
//! indexer commits per file group rather than per file, because a transaction per
//! file on a 20 000-file repo spends more time in fsync than in parsing.

use crate::Graph;
use anyhow::{Context, Result};
use cs_core::{
    ContentHash, Edge, EdgeId, Facts, FileId, Language, NodeId, RawBinding, RawImport,
    RawReference, Symbol,
};
use rusqlite::{params, Transaction};

/// A file as the index knows it.
#[derive(Debug, Clone)]
pub struct FileRecord {
    pub id: FileId,
    /// Workspace-relative, forward slashes.
    pub path: String,
    pub lang: Option<Language>,
    pub hash: ContentHash,
    pub size: u64,
    pub lines: u32,
    pub mtime: i64,
    pub parsed: bool,
    /// Why this file was not parsed, if it was not. Kept so the UI can show
    /// honest coverage instead of pretending the file does not exist.
    pub skip_reason: Option<String>,
}

pub struct WriteBatch<'a> {
    tx: Transaction<'a>,
}

impl Graph {
    /// Starts a write transaction. Nothing is visible to readers until
    /// [`WriteBatch::commit`].
    pub fn write_batch(&mut self) -> Result<WriteBatch<'_>> {
        Ok(WriteBatch { tx: self.conn.transaction()? })
    }

    /// Files already in the index, as `(path, hash, mtime)`.
    ///
    /// The indexer diffs the workspace against this to decide what to reparse.
    /// Comparing hashes rather than only mtimes matters more than it sounds:
    /// `git checkout` rewrites mtimes on files whose contents did not change, and
    /// without the hash check every branch switch would trigger a full reindex.
    pub fn known_files(&self) -> Result<Vec<(String, ContentHash, i64)>> {
        let mut stmt = self.conn.prepare("SELECT path, hash, mtime FROM files")?;
        let rows = stmt.query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?, row.get::<_, i64>(2)?))
        })?;
        let mut out = Vec::new();
        for row in rows {
            let (path, hash, mtime) = row?;
            if let Some(hash) = ContentHash::from_hex(&hash) {
                out.push((path, hash, mtime));
            }
        }
        Ok(out)
    }

    /// Drops files that no longer exist on disk, along with everything they own.
    pub fn prune_missing(&mut self, existing_paths: &[String]) -> Result<usize> {
        let tx = self.conn.transaction()?;
        let known: Vec<(i64, String)> = {
            let mut stmt = tx.prepare("SELECT id, path FROM files")?;
            let rows = stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?;
            rows.collect::<rusqlite::Result<_>>()?
        };
        let set: std::collections::HashSet<&str> =
            existing_paths.iter().map(|s| s.as_str()).collect();

        let mut removed = 0;
        for (id, path) in known {
            if !set.contains(path.as_str()) {
                delete_file_contents(&tx, id)?;
                tx.execute("DELETE FROM files WHERE id = ?1", [id])?;
                removed += 1;
            }
        }
        tx.commit()?;
        Ok(removed)
    }
}

/// Removes everything derived from one file, leaving the `files` row alone.
///
/// Edges are deleted by *evidence* file, not by endpoint: a call from `a.py` to
/// `b.py` is owned by `a.py`, because that is where the call site lives. Deleting
/// by endpoint would wipe edges that other, unchanged files still assert.
fn delete_file_contents(tx: &Transaction<'_>, file_id: i64) -> rusqlite::Result<()> {
    tx.execute(
        "DELETE FROM symbol_search WHERE rowid IN (SELECT id FROM nodes WHERE file_id = ?1)",
        [file_id],
    )?;
    tx.execute("DELETE FROM edges WHERE ev_file = ?1", [file_id])?;
    tx.execute("DELETE FROM refs WHERE file_id = ?1", [file_id])?;
    tx.execute("DELETE FROM imports WHERE file_id = ?1", [file_id])?;
    tx.execute("DELETE FROM bindings WHERE file_id = ?1", [file_id])?;
    // facts and metrics cascade from nodes.
    tx.execute("DELETE FROM nodes WHERE file_id = ?1", [file_id])?;
    tx.execute("DELETE FROM text_index WHERE rowid = ?1", [file_id])?;
    Ok(())
}

impl WriteBatch<'_> {
    /// Inserts or updates a file row and clears everything previously derived
    /// from it. Call this before writing that file's symbols.
    pub fn put_file(&self, rec: &FileRecord) -> Result<()> {
        let id = rec.id.to_sqlite();
        delete_file_contents(&self.tx, id)?;
        self.tx
            .execute(
                "INSERT INTO files (id, path, lang, hash, size, lines, mtime, parsed, skip_reason)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)
                 ON CONFLICT(id) DO UPDATE SET
                     path = excluded.path, lang = excluded.lang, hash = excluded.hash,
                     size = excluded.size, lines = excluded.lines, mtime = excluded.mtime,
                     parsed = excluded.parsed, skip_reason = excluded.skip_reason",
                params![
                    id,
                    rec.path,
                    rec.lang.map(|l| l.slug()),
                    rec.hash.to_hex(),
                    rec.size as i64,
                    rec.lines as i64,
                    rec.mtime,
                    rec.parsed as i64,
                    rec.skip_reason,
                ],
            )
            .with_context(|| format!("writing file row for {}", rec.path))?;
        Ok(())
    }

    pub fn put_symbol(&self, file: FileId, path: &str, sym: &Symbol) -> Result<()> {
        self.tx.execute(
            "INSERT OR REPLACE INTO nodes
                 (id, file_id, kind, lang, name, qualified,
                  start_byte, end_byte, start_line, end_line,
                  sig_start_byte, sig_end_byte, sig_start_line, sig_end_line,
                  parent_id, visibility, doc)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17)",
            params![
                sym.id.to_sqlite(),
                file.to_sqlite(),
                sym.kind.as_str(),
                sym.language.slug(),
                sym.name,
                sym.qualified,
                sym.span.start_byte,
                sym.span.end_byte,
                sym.span.start_line,
                sym.span.end_line,
                sym.signature_span.start_byte,
                sym.signature_span.end_byte,
                sym.signature_span.start_line,
                sym.signature_span.end_line,
                sym.parent.map(|p| p.to_sqlite()),
                format!("{:?}", sym.visibility).to_lowercase(),
                sym.doc,
            ],
        )?;

        self.tx.execute(
            "INSERT OR REPLACE INTO symbol_search (rowid, name, qualified, path)
             VALUES (?1, ?2, ?3, ?4)",
            params![sym.id.to_sqlite(), sym.name, sym.qualified, path],
        )?;
        Ok(())
    }

    /// Writes an edge, keeping the strongest claim when the same relationship is
    /// found twice.
    ///
    /// Two passes routinely rediscover the same edge — the name heuristic guesses
    /// what scope resolution then proves. Upserting on confidence rank means pass
    /// order does not matter, which removes a whole category of ordering bugs.
    pub fn put_edge(&self, edge: &Edge) -> Result<()> {
        let id = EdgeId::of(edge.from, edge.to, edge.kind.as_str());
        self.tx.execute(
            "INSERT INTO edges
                 (id, from_id, to_id, kind, confidence, conf_rank, source, occurrences,
                  ev_file, ev_start_byte, ev_end_byte, ev_start_line, ev_end_line, ev_hash,
                  candidates)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, 1, ?8, ?9, ?10, ?11, ?12, ?13, ?14)
             ON CONFLICT(id) DO UPDATE SET
                 occurrences = edges.occurrences + 1,
                 confidence = CASE WHEN excluded.conf_rank > edges.conf_rank
                                   THEN excluded.confidence ELSE edges.confidence END,
                 source     = CASE WHEN excluded.conf_rank > edges.conf_rank
                                   THEN excluded.source ELSE edges.source END,
                 candidates = CASE WHEN excluded.conf_rank > edges.conf_rank
                                   THEN excluded.candidates ELSE edges.candidates END,
                 conf_rank  = max(edges.conf_rank, excluded.conf_rank)",
            params![
                id.to_sqlite(),
                edge.from.to_sqlite(),
                edge.to.to_sqlite(),
                edge.kind.as_str(),
                format!("{:?}", edge.confidence).to_lowercase(),
                edge.confidence as i64,
                format!("{:?}", edge.source).to_lowercase(),
                edge.evidence.file.to_sqlite(),
                edge.evidence.span.start_byte,
                edge.evidence.span.end_byte,
                edge.evidence.span.start_line,
                edge.evidence.span.end_line,
                edge.evidence.content_hash.to_hex(),
                edge.candidate_count,
            ],
        )?;
        Ok(())
    }

    /// Stores a reference exactly as parsed, so resolution can be redone later
    /// without reparsing the file it came from.
    pub fn put_reference(&self, file: FileId, reference: &RawReference) -> Result<()> {
        self.tx.execute(
            "INSERT INTO refs
                 (file_id, from_id, name, receiver, kind, start_byte, end_byte, start_line, end_line)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![
                file.to_sqlite(),
                reference.from.to_sqlite(),
                reference.name,
                reference.receiver,
                reference.kind.as_str(),
                reference.span.start_byte,
                reference.span.end_byte,
                reference.span.start_line,
                reference.span.end_line,
            ],
        )?;
        Ok(())
    }

    pub fn put_import(&self, file: FileId, import: &RawImport) -> Result<()> {
        self.tx.execute(
            "INSERT INTO imports
                 (file_id, module, symbol, alias, start_byte, end_byte, start_line, end_line)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![
                file.to_sqlite(),
                import.module,
                import.symbol,
                import.alias,
                import.span.start_byte,
                import.span.end_byte,
                import.span.start_line,
                import.span.end_line,
            ],
        )?;
        Ok(())
    }

    pub fn put_binding(&self, file: FileId, binding: &RawBinding) -> Result<()> {
        self.tx.execute(
            "INSERT INTO bindings
                 (file_id, variable, constructor, start_byte, end_byte, start_line, end_line)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![
                file.to_sqlite(),
                binding.variable,
                binding.constructor,
                binding.span.start_byte,
                binding.span.end_byte,
                binding.span.start_line,
                binding.span.end_line,
            ],
        )?;
        Ok(())
    }

    /// Removes every edge produced by a resolution pass, leaving parse output
    /// intact. Called before re-resolving so stale targets cannot survive.
    ///
    /// `syntax` is deliberately **not** in this list. Containment — the file owns
    /// its functions, the class owns its methods — comes from the parse, not from
    /// resolution, and is only invalidated when its file is reparsed. Deleting it
    /// here silently emptied the Blueprint's "enthält" list on every index run.
    pub fn clear_resolved_edges(&self) -> Result<()> {
        self.tx.execute(
            "DELETE FROM edges WHERE source IN ('scoperesolution', 'nameheuristic')",
            [],
        )?;
        Ok(())
    }

    pub fn put_facts(&self, node: NodeId, facts: &Facts) -> Result<()> {
        self.tx.execute(
            "INSERT OR REPLACE INTO facts (node_id, json) VALUES (?1, ?2)",
            params![node.to_sqlite(), serde_json::to_string(facts)?],
        )?;
        Ok(())
    }

    /// Feeds a file's text to the search index without storing it.
    pub fn index_text(&self, file: FileId, body: &str) -> Result<()> {
        self.tx.execute(
            "INSERT INTO text_index (rowid, body) VALUES (?1, ?2)",
            params![file.to_sqlite(), body],
        )?;
        Ok(())
    }

    pub fn commit(self) -> Result<()> {
        self.tx.commit()?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use cs_core::{Confidence, EdgeKind, Evidence, Source, Span};

    fn sample_file() -> FileRecord {
        FileRecord {
            id: FileId::of_path("a.py"),
            path: "a.py".into(),
            lang: Some(Language::Python),
            hash: ContentHash::of(b"x"),
            size: 1,
            lines: 1,
            mtime: 0,
            parsed: true,
            skip_reason: None,
        }
    }

    fn edge_with(confidence: Confidence, source: Source) -> Edge {
        let file = FileId::of_path("a.py");
        Edge::new(
            NodeId::of_symbol(file, "function", "caller"),
            NodeId::of_symbol(file, "function", "callee"),
            EdgeKind::Calls,
            confidence,
            source,
            Evidence { file, span: Span::new(0, 5, 1, 1), content_hash: ContentHash::of(b"x") },
        )
    }

    #[test]
    fn a_stronger_claim_replaces_a_weaker_one_regardless_of_order() {
        for (first, second) in [
            (Confidence::Guessed, Confidence::Verified),
            (Confidence::Verified, Confidence::Guessed),
        ] {
            let mut graph = Graph::open_in_memory("/tmp/x").unwrap();
            let batch = graph.write_batch().unwrap();
            batch.put_file(&sample_file()).unwrap();
            batch.put_edge(&edge_with(first, Source::NameHeuristic)).unwrap();
            batch.put_edge(&edge_with(second, Source::LanguageServer)).unwrap();
            batch.commit().unwrap();

            let stored: String = graph
                .connection()
                .query_row("SELECT confidence FROM edges", [], |r| r.get(0))
                .unwrap();
            assert_eq!(stored, "verified", "pass order must not change the outcome");

            let occurrences: i64 = graph
                .connection()
                .query_row("SELECT occurrences FROM edges", [], |r| r.get(0))
                .unwrap();
            assert_eq!(occurrences, 2);
        }
    }

    #[test]
    fn reindexing_a_file_replaces_its_edges_instead_of_duplicating_them() {
        let mut graph = Graph::open_in_memory("/tmp/x").unwrap();

        for _ in 0..3 {
            let batch = graph.write_batch().unwrap();
            batch.put_file(&sample_file()).unwrap();
            batch.put_edge(&edge_with(Confidence::Resolved, Source::ScopeResolution)).unwrap();
            batch.commit().unwrap();
        }

        let count: i64 =
            graph.connection().query_row("SELECT count(*) FROM edges", [], |r| r.get(0)).unwrap();
        assert_eq!(count, 1, "put_file must clear what the previous parse asserted");
    }

    #[test]
    fn pruning_removes_files_that_vanished_from_disk() {
        let mut graph = Graph::open_in_memory("/tmp/x").unwrap();
        let batch = graph.write_batch().unwrap();
        batch.put_file(&sample_file()).unwrap();
        batch.commit().unwrap();

        assert_eq!(graph.prune_missing(&[]).unwrap(), 1);
        assert_eq!(graph.stats().unwrap().files, 0);
    }
}
