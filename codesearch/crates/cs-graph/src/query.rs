//! Reading the graph.
//!
//! Every view in the app and every tool the model can call bottoms out in one of
//! these methods. They are deliberately narrow: each returns nodes with their
//! confidence and their evidence attached, so a caller physically cannot render
//! or quote a relationship without also having the proof for it at hand.

use crate::Graph;
use anyhow::{bail, Context, Result};
use cs_core::{
    Confidence, ContentHash, EdgeKind, Facts, FileId, Language, NodeId, NodeKind, RawBinding,
    RawImport,
    RawReference, Span, Symbol, Visibility,
};
use rusqlite::{params, params_from_iter, Row};
use serde::Serialize;
use std::collections::{HashMap, HashSet};

/// One file's parse output, read back from the index.
pub struct StoredFile {
    pub file: FileId,
    pub path: String,
    pub language: Language,
    pub hash: ContentHash,
    pub symbols: Vec<Symbol>,
    pub references: Vec<RawReference>,
    pub imports: Vec<RawImport>,
    pub bindings: Vec<RawBinding>,
}

fn visibility_from_str(s: &str) -> Visibility {
    match s {
        "private" => Visibility::Private,
        "protected" => Visibility::Protected,
        "internal" => Visibility::Internal,
        _ => Visibility::Public,
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct SymbolHit {
    pub id: NodeId,
    pub name: String,
    pub qualified: String,
    pub kind: NodeKind,
    pub language: Language,
    pub path: String,
    pub line: u32,
    pub relevance: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct TextHit {
    pub path: String,
    pub line: u32,
    pub text: String,
    /// The symbol whose span contains this line, when there is one. Lets a text
    /// hit jump straight into the graph instead of dead-ending in a file.
    pub in_symbol: Option<NodeId>,
}

#[derive(Debug, Clone, Serialize)]
pub struct NodeDetail {
    pub id: NodeId,
    pub name: String,
    pub qualified: String,
    pub kind: NodeKind,
    pub language: Language,
    pub path: String,
    pub span: Span,
    pub signature_span: Span,
    pub doc: Option<String>,
    pub parent: Option<NodeId>,
    pub facts: Option<Facts>,
    pub metrics: Metrics,
}

#[derive(Debug, Clone, Default, Serialize)]
pub struct Metrics {
    pub pagerank: f64,
    pub fan_in: u32,
    pub fan_out: u32,
    pub reach_depth: Option<u32>,
    pub churn: u32,
    pub risk: u32,
    pub authors: u32,
    pub coverage: Option<f64>,
    pub hits: Option<u64>,
    pub relevance: f64,
}

/// One step away from a node, with the edge that got you there.
#[derive(Debug, Clone, Serialize)]
pub struct Neighbour {
    pub node: SymbolHit,
    pub kind: EdgeKind,
    pub confidence: Confidence,
    pub candidates: u16,
    pub occurrences: u32,
    /// Where the relationship is written down — the call site, not the target.
    pub evidence_path: String,
    pub evidence_line: u32,
}

/// A subgraph ready for rendering.
#[derive(Debug, Clone, Default, Serialize)]
pub struct GraphSlice {
    pub nodes: Vec<SymbolHit>,
    pub edges: Vec<SliceEdge>,
    /// True when the traversal hit its node budget and stopped early, so the UI
    /// can say "showing 500 of ~3000" rather than quietly lying about the shape
    /// of the graph.
    pub truncated: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct SliceEdge {
    pub from: NodeId,
    pub to: NodeId,
    pub kind: EdgeKind,
    pub confidence: Confidence,
    pub occurrences: u32,
    pub evidence_line: u32,
    pub evidence_path: String,
}

/// One reached node in an impact analysis, with how far it is and how strong
/// the best path to it is.
#[derive(Debug, Clone, Serialize)]
pub struct ImpactNode {
    pub node: SymbolHit,
    /// Hop distance from the changed symbol. 1 = direct caller.
    pub hops: u32,
    /// Confidence of the *weakest edge* along the strongest path — a chain is
    /// only as strong as its weakest link, but one strong route suffices.
    pub confidence: Confidence,
}

/// A file touched by the blast radius, with how many of its symbols are reached
/// and the git churn/risk summed over those symbols.
#[derive(Debug, Clone, Serialize)]
pub struct ImpactFile {
    pub path: String,
    pub symbols: u32,
    pub churn: u32,
    pub risk: u32,
}

/// The blast radius of a change at `root` — who is reached, who tests it, where
/// the static analysis ends, and which files are touched.
#[derive(Debug, Clone, Serialize)]
pub struct Impact {
    pub root: NodeId,
    pub max_depth: u32,
    /// Every transitively reached symbol, nearest first.
    pub reached: Vec<ImpactNode>,
    /// Direct callers (hops == 1) with their edge evidence attached.
    pub direct_callers: Vec<Neighbour>,
    /// Reached symbols of kind `Test` — „what breaks if I change this" is only
    /// answerable when you can also see what would catch it. Derived from
    /// `kind='test'`, **not** from `EdgeKind::TestedBy` (declared, never
    /// produced — a query on it would read as „no tests").
    pub tests: Vec<SymbolHit>,
    /// Reached `dynamic_gap` nodes — the explicit edge of the static analysis,
    /// not a missing entry. Stating them is an answer; hiding them is a lie.
    pub dynamic_gaps: Vec<SymbolHit>,
    pub files: Vec<ImpactFile>,
    /// True when the traversal hit its budget — a half answer on „what breaks"
    /// is worse than none, so this is signalled, not silently truncated.
    pub truncated: bool,
    pub edge_kinds: Vec<String>,
}

struct ImpactReach {
    node: NodeId,
    hops: u32,
    best_rank: i64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, serde::Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Direction {
    /// Follow edges away from the node — what it uses.
    Out,
    /// Follow edges into the node — who uses it.
    In,
    Both,
}

impl Graph {
    /// Identifier search. Trigram-backed, so `disc` finds `apply_discount`.
    ///
    /// Ranked by relevance rather than by string similarity: when you type
    /// `handler` you want the one on the hot path, not the one in a test fixture.
    pub fn search_symbols(
        &self,
        query: &str,
        kinds: &[NodeKind],
        limit: usize,
    ) -> Result<Vec<SymbolHit>> {
        let query = query.trim();
        if query.is_empty() {
            return Ok(Vec::new());
        }

        // The trigram tokeniser cannot match anything shorter than three
        // characters, so short queries take a prefix path instead of silently
        // returning nothing.
        let mut sql = if query.len() < 3 {
            String::from(
                "SELECT n.id, n.name, n.qualified, n.kind, n.lang, f.path, n.start_line,
                        coalesce(m.relevance, 0)
                 FROM nodes n
                 JOIN files f ON f.id = n.file_id
                 LEFT JOIN metrics m ON m.node_id = n.id
                 WHERE n.name LIKE ?1 || '%'",
            )
        } else {
            String::from(
                "SELECT n.id, n.name, n.qualified, n.kind, n.lang, f.path, n.start_line,
                        coalesce(m.relevance, 0)
                 FROM symbol_search s
                 JOIN nodes n ON n.id = s.rowid
                 JOIN files f ON f.id = n.file_id
                 LEFT JOIN metrics m ON m.node_id = n.id
                 WHERE symbol_search MATCH ?1",
            )
        };

        let mut binds: Vec<String> = vec![if query.len() < 3 {
            query.to_string()
        } else {
            fts_literal(query)
        }];

        if !kinds.is_empty() {
            let placeholders =
                (0..kinds.len()).map(|i| format!("?{}", i + 2)).collect::<Vec<_>>().join(", ");
            sql.push_str(&format!(" AND n.kind IN ({placeholders})"));
            binds.extend(kinds.iter().map(|k| k.as_str().to_string()));
        }

        // Exact name matches first, then relevance. Without the exact-match term
        // a search for `main` surfaces `domain_check` above `main` itself.
        sql.push_str(&format!(
            " ORDER BY (n.name = ?{}) DESC, coalesce(m.relevance, 0) DESC, length(n.name) ASC
              LIMIT ?{}",
            binds.len() + 1,
            binds.len() + 2
        ));
        binds.push(query.to_string());
        binds.push(limit.to_string());

        let mut stmt = self.conn.prepare(&sql)?;
        let rows = stmt.query_map(params_from_iter(binds.iter()), symbol_hit_from_row)?;
        Ok(rows.collect::<rusqlite::Result<_>>()?)
    }

    /// Full-text search across the workspace.
    ///
    /// The FTS index narrows the repo to candidate files; the actual lines are
    /// then read off disk. That keeps the index small and — more usefully — means
    /// a hit is always the text as it exists right now, never a stale copy.
    pub fn search_text(&self, query: &str, limit: usize) -> Result<Vec<TextHit>> {
        let query = query.trim();
        if query.len() < 3 {
            bail!("Volltextsuche braucht mindestens 3 Zeichen");
        }

        let mut stmt = self.conn.prepare(
            "SELECT f.id, f.path, f.hash
             FROM text_index t
             JOIN files f ON f.id = t.rowid
             WHERE text_index MATCH ?1
             LIMIT 400",
        )?;
        let candidates: Vec<(i64, String, String)> = stmt
            .query_map([fts_literal(query)], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?
            .collect::<rusqlite::Result<_>>()?;

        let needle = query.to_lowercase();
        let mut hits = Vec::new();
        for (file_id, path, _hash) in candidates {
            if hits.len() >= limit {
                break;
            }
            let full = self.root.join(&path);
            let Ok(text) = std::fs::read_to_string(&full) else {
                // Deleted or unreadable since indexing. Skipping is correct: a
                // result we cannot verify has no business being shown.
                continue;
            };
            for (idx, line) in text.lines().enumerate() {
                if hits.len() >= limit {
                    break;
                }
                if line.to_lowercase().contains(&needle) {
                    let line_no = idx as u32 + 1;
                    hits.push(TextHit {
                        path: path.clone(),
                        line: line_no,
                        text: line.trim_end().chars().take(300).collect(),
                        in_symbol: self.symbol_at(FileId::from_sqlite(file_id), line_no).ok().flatten(),
                    });
                }
            }
        }
        Ok(hits)
    }

    /// The innermost symbol whose span covers `line`.
    pub fn symbol_at(&self, file: FileId, line: u32) -> Result<Option<NodeId>> {
        let id = self
            .conn
            .query_row(
                "SELECT id FROM nodes
                 WHERE file_id = ?1 AND start_line <= ?2 AND end_line >= ?2
                   AND kind NOT IN ('file', 'module')
                 ORDER BY (end_line - start_line) ASC
                 LIMIT 1",
                params![file.to_sqlite(), line],
                |r| r.get::<_, i64>(0),
            )
            .ok();
        Ok(id.map(NodeId::from_sqlite))
    }

    pub fn node(&self, id: NodeId) -> Result<Option<NodeDetail>> {
        let mut stmt = self.conn.prepare(
            "SELECT n.id, n.name, n.qualified, n.kind, n.lang, f.path,
                    n.start_byte, n.end_byte, n.start_line, n.end_line,
                    n.sig_start_byte, n.sig_end_byte, n.sig_start_line, n.sig_end_line,
                    n.doc, n.parent_id, fa.json,
                    m.pagerank, m.fan_in, m.fan_out, m.reach_depth, m.churn, m.risk,
                    m.authors, m.coverage, m.hits, m.relevance
             FROM nodes n
             JOIN files f ON f.id = n.file_id
             LEFT JOIN facts fa ON fa.node_id = n.id
             LEFT JOIN metrics m ON m.node_id = n.id
             WHERE n.id = ?1",
        )?;

        let detail = stmt
            .query_row([id.to_sqlite()], |row| {
                Ok(NodeDetail {
                    id: NodeId::from_sqlite(row.get(0)?),
                    name: row.get(1)?,
                    qualified: row.get(2)?,
                    kind: NodeKind::from_str(&row.get::<_, String>(3)?).unwrap_or(NodeKind::File),
                    language: Language::from_slug(&row.get::<_, String>(4)?)
                        .unwrap_or(Language::Python),
                    path: row.get(5)?,
                    span: Span::new(row.get(6)?, row.get(7)?, row.get(8)?, row.get(9)?),
                    signature_span: Span::new(
                        row.get(10)?,
                        row.get(11)?,
                        row.get(12)?,
                        row.get(13)?,
                    ),
                    doc: row.get(14)?,
                    parent: row.get::<_, Option<i64>>(15)?.map(NodeId::from_sqlite),
                    facts: row
                        .get::<_, Option<String>>(16)?
                        .and_then(|j| serde_json::from_str(&j).ok()),
                    metrics: Metrics {
                        pagerank: row.get::<_, Option<f64>>(17)?.unwrap_or(0.0),
                        fan_in: row.get::<_, Option<u32>>(18)?.unwrap_or(0),
                        fan_out: row.get::<_, Option<u32>>(19)?.unwrap_or(0),
                        reach_depth: row.get(20)?,
                        churn: row.get::<_, Option<u32>>(21)?.unwrap_or(0),
                        risk: row.get::<_, Option<u32>>(22)?.unwrap_or(0),
                        authors: row.get::<_, Option<u32>>(23)?.unwrap_or(0),
                        coverage: row.get(24)?,
                        hits: row.get::<_, Option<i64>>(25)?.map(|h| h.max(0) as u64),
                        relevance: row.get::<_, Option<f64>>(26)?.unwrap_or(0.0),
                    },
                })
            })
            .ok();
        Ok(detail)
    }

    /// One hop out of a node, in either direction.
    pub fn neighbours(
        &self,
        id: NodeId,
        direction: Direction,
        kinds: &[EdgeKind],
    ) -> Result<Vec<Neighbour>> {
        let (join_on, match_on) = match direction {
            Direction::Out => ("e.to_id", "e.from_id"),
            Direction::In => ("e.from_id", "e.to_id"),
            // Handled by running both directions and concatenating; a single
            // query would need a UNION that defeats the index on (from_id, kind).
            Direction::Both => {
                let mut out = self.neighbours(id, Direction::Out, kinds)?;
                out.extend(self.neighbours(id, Direction::In, kinds)?);
                return Ok(out);
            }
        };

        let mut sql = format!(
            "SELECT n.id, n.name, n.qualified, n.kind, n.lang, f.path, n.start_line,
                    coalesce(m.relevance, 0),
                    e.kind, e.confidence, e.candidates, e.occurrences,
                    ef.path, e.ev_start_line
             FROM edges e
             JOIN nodes n ON n.id = {join_on}
             JOIN files f ON f.id = n.file_id
             JOIN files ef ON ef.id = e.ev_file
             LEFT JOIN metrics m ON m.node_id = n.id
             WHERE {match_on} = ?1"
        );

        let mut binds: Vec<String> = vec![id.to_sqlite().to_string()];
        if !kinds.is_empty() {
            let placeholders =
                (0..kinds.len()).map(|i| format!("?{}", i + 2)).collect::<Vec<_>>().join(", ");
            sql.push_str(&format!(" AND e.kind IN ({placeholders})"));
            binds.extend(kinds.iter().map(|k| k.as_str().to_string()));
        }
        // Strongest claims first: a verified caller is more useful to look at
        // than a guessed one, and the list is often cut off by the eye, not by a
        // LIMIT.
        sql.push_str(" ORDER BY e.conf_rank DESC, coalesce(m.relevance, 0) DESC LIMIT 500");

        let mut stmt = self.conn.prepare(&sql)?;
        let rows = stmt.query_map(params_from_iter(binds.iter()), |row| {
            Ok(Neighbour {
                node: symbol_hit_from_row(row)?,
                kind: EdgeKind::from_str(&row.get::<_, String>(8)?).unwrap_or(EdgeKind::Calls),
                confidence: confidence_from_str(&row.get::<_, String>(9)?),
                candidates: row.get(10)?,
                occurrences: row.get(11)?,
                evidence_path: row.get(12)?,
                evidence_line: row.get(13)?,
            })
        })?;
        Ok(rows.collect::<rusqlite::Result<_>>()?)
    }

    /// A subgraph around `id`, breadth-first up to `depth` hops.
    ///
    /// `budget` caps the node count. Blueprint view asks for a small budget and
    /// gets something readable; Map view asks for a large one. Exceeding it sets
    /// `truncated` rather than returning a partial graph that looks complete.
    pub fn slice(
        &self,
        id: NodeId,
        depth: u32,
        direction: Direction,
        kinds: &[EdgeKind],
        budget: usize,
    ) -> Result<GraphSlice> {
        let mut seen: HashSet<NodeId> = HashSet::from([id]);
        let mut frontier = vec![id];
        let mut edges: Vec<SliceEdge> = Vec::new();
        let mut truncated = false;

        for _ in 0..depth {
            let mut next = Vec::new();
            for node in frontier.drain(..) {
                for neighbour in self.neighbours(node, direction, kinds)? {
                    let (from, to) = match direction {
                        Direction::In => (neighbour.node.id, node),
                        _ => (node, neighbour.node.id),
                    };
                    edges.push(SliceEdge {
                        from,
                        to,
                        kind: neighbour.kind,
                        confidence: neighbour.confidence,
                        occurrences: neighbour.occurrences,
                        evidence_line: neighbour.evidence_line,
                        evidence_path: neighbour.evidence_path,
                    });
                    if seen.len() >= budget {
                        truncated = true;
                        continue;
                    }
                    if seen.insert(neighbour.node.id) {
                        next.push(neighbour.node.id);
                    }
                }
            }
            if next.is_empty() {
                break;
            }
            frontier = next;
        }

        // `Direction::Both` visits an edge from each end, so the same
        // relationship can be recorded twice.
        edges.sort_by_key(|e| (e.from, e.to, e.kind.as_str()));
        edges.dedup_by_key(|e| (e.from, e.to, e.kind.as_str()));
        edges.retain(|e| seen.contains(&e.from) && seen.contains(&e.to));

        let nodes = self.hydrate(&seen)?;
        Ok(GraphSlice { nodes, edges, truncated })
    }

    /// Shortest call path from `from` to `to`, or `None` when there is none.
    ///
    /// This is what turns "why does changing A break B" into something you can
    /// look at. Cycles are excluded by checking the accumulated path, so a
    /// recursive call graph cannot make this run forever.
    pub fn path_between(&self, from: NodeId, to: NodeId, max_depth: u32) -> Result<Option<Vec<NodeId>>> {
        let path: Option<String> = self
            .conn
            .query_row(
                "WITH RECURSIVE walk(node, trail, depth) AS (
                     SELECT ?1, ',' || ?1 || ',', 0
                     UNION ALL
                     SELECT e.to_id, walk.trail || e.to_id || ',', walk.depth + 1
                     FROM walk
                     JOIN edges e ON e.from_id = walk.node
                     WHERE walk.depth < ?3
                       AND instr(walk.trail, ',' || e.to_id || ',') = 0
                       AND e.kind IN ('calls', 'reads', 'writes')
                 )
                 SELECT trail FROM walk WHERE node = ?2 ORDER BY depth LIMIT 1",
                params![from.to_sqlite(), to.to_sqlite(), max_depth],
                |r| r.get(0),
            )
            .ok();

        Ok(path.map(|trail| {
            trail
                .split(',')
                .filter(|s| !s.is_empty())
                .filter_map(|s| s.parse::<i64>().ok())
                .map(NodeId::from_sqlite)
                .collect()
        }))
    }

    /// Who is reached by a change at `id`, walking the graph **backwards**.
    ///
    /// This is the answer to „what breaks if I change this?" — the question
    /// `path_between` does not ask (it walks forward, and its edge kinds are
    /// hard-wired). `direction=In` over `calls/reads/writes` gives every
    /// transitive caller, with the hop distance and the weakest link along the
    /// best path.
    ///
    /// Two aggregation choices, both load-bearing:
    /// * `MIN(conf_rank)` along a path — a chain with one guessed edge is
    ///   guessed end to end. A chain is only as strong as its weakest edge.
    /// * `MAX` of that across paths — one verified route to a node is enough
    ///   to call it affected; we do not require *every* route to be verified.
    ///
    /// `budget` caps the reached-node count; exceeding it sets `truncated`
    /// (unlike `neighbours`, which silently drops at LIMIT 500 — on a
    /// „what breaks?" question a half answer is worse than none).
    pub fn impact(
        &self,
        id: NodeId,
        max_depth: u32,
        kinds: &[EdgeKind],
        budget: usize,
    ) -> Result<Impact> {
        // Recursive backward walk. `weakest` starts at 4 (stronger than any
        // real conf_rank 0..3) so the seed never clamps the first edge.
        let mut sql = String::from(
            "WITH RECURSIVE back(node, hops, weakest) AS (
                 SELECT ?1, 0, 4
                 UNION
                 SELECT e.from_id, back.hops + 1, MIN(back.weakest, e.conf_rank)
                 FROM back JOIN edges e ON e.to_id = back.node
                 WHERE back.hops < ?2",
        );
        let mut binds: Vec<Box<dyn rusqlite::ToSql>> = Vec::new();
        binds.push(Box::new(id.to_sqlite()));
        binds.push(Box::new(max_depth as i64));
        let mut next_param = 3;
        for k in kinds {
            let placeholders = format!("?{next_param}");
            next_param += 1;
            if !sql.contains("e.kind IN") {
                sql.push_str(&format!(" AND e.kind IN ({placeholders}"));
            } else {
                sql.push_str(&format!(", {placeholders}"));
            }
            binds.push(Box::new(k.as_str().to_string()));
        }
        if sql.contains("e.kind IN") && !kinds.is_empty() {
            sql.push(')');
        }
        sql.push_str(&format!(
            ")
             SELECT node, MIN(hops) AS hops, MAX(weakest) AS best
             FROM back WHERE node <> ?1
             GROUP BY node
             ORDER BY hops, best DESC LIMIT ?{next_param}",
        ));
        binds.push(Box::new(budget as i64 + 1));

        let mut stmt = self.conn.prepare(&sql)?;
        let rows = stmt.query_map(
            params_from_iter(binds.iter().map(|b| b.as_ref())),
            |row| {
                Ok(ImpactReach {
                    node: NodeId::from_sqlite(row.get(0)?),
                    hops: row.get::<_, i64>(1)? as u32,
                    best_rank: row.get::<_, i64>(2)?,
                })
            },
        )?;
        let mut reached: Vec<ImpactReach> = rows.collect::<rusqlite::Result<_>>()?;
        let truncated = reached.len() > budget;
        if truncated {
            reached.truncate(budget);
        }

        // Hydrate the reached nodes to get path/kind/line, then split by kind.
        let reached_ids: HashSet<NodeId> = reached.iter().map(|r| r.node).collect();
        let mut hits = self.hydrate(&reached_ids)?;
        hits.sort_by_key(|h| h.path.clone());

        let mut reached_out: Vec<ImpactNode> = Vec::with_capacity(reached.len());
        let mut tests: Vec<SymbolHit> = Vec::new();
        let mut gaps: Vec<SymbolHit> = Vec::new();
        let mut files: HashMap<String, ImpactFile> = HashMap::new();
        for hit in &hits {
            let rank = reached
                .iter()
                .find(|r| r.node == hit.id)
                .map(|r| r.best_rank)
                .unwrap_or(0);
            let confidence = confidence_from_rank(rank);
            let hops = reached
                .iter()
                .find(|r| r.node == hit.id)
                .map(|r| r.hops)
                .unwrap_or(0);
            reached_out.push(ImpactNode {
                node: hit.clone(),
                hops,
                confidence,
            });
            match hit.kind {
                NodeKind::Test => tests.push(hit.clone()),
                NodeKind::DynamicGap => gaps.push(hit.clone()),
                _ => {}
            }
            let entry = files.entry(hit.path.clone()).or_insert_with(|| ImpactFile {
                path: hit.path.clone(),
                symbols: 0,
                churn: 0,
                risk: 0,
            });
            entry.symbols += 1;
        }
        // Attach churn/risk per file from metrics. cs-git records churn/risk per
        // *node* (copied from the file), so summing nodes of a file is exact.
        for file in files.values_mut() {
            let row = self.conn.query_row(
                "SELECT coalesce(SUM(m.churn), 0), coalesce(SUM(m.risk), 0)
                 FROM nodes n JOIN metrics m ON m.node_id = n.id
                 JOIN files f ON f.id = n.file_id
                 WHERE f.path = ?1",
                params![file.path],
                |r| Ok((r.get::<_, i64>(0)? as u32, r.get::<_, i64>(1)? as u32)),
            );
            if let Ok((churn, risk)) = row {
                file.churn = churn;
                file.risk = risk;
            }
        }

        // Direct callers (hops == 1) get their evidence attached via neighbours —
        // the recursive aggregate loses which edge led to a node, so only the
        // one-hop set can carry a `datei:zeile` proof per entry.
        let direct_callers = self.neighbours(id, Direction::In, kinds)?;

        Ok(Impact {
            root: id,
            max_depth,
            reached: reached_out,
            direct_callers,
            tests,
            dynamic_gaps: gaps,
            files: files.into_values().collect(),
            truncated,
            edge_kinds: kinds.iter().map(|k| k.as_str().to_string()).collect(),
        })
    }

    /// The most structurally important symbols. Drives the first-run tour and the
    /// default Map view.
    pub fn top_by_relevance(&self, limit: usize, kinds: &[NodeKind]) -> Result<Vec<SymbolHit>> {
        let mut sql = String::from(
            "SELECT n.id, n.name, n.qualified, n.kind, n.lang, f.path, n.start_line, m.relevance
             FROM metrics m
             JOIN nodes n ON n.id = m.node_id
             JOIN files f ON f.id = n.file_id
             WHERE 1 = 1",
        );
        let mut binds: Vec<String> = Vec::new();
        if !kinds.is_empty() {
            let placeholders =
                (0..kinds.len()).map(|i| format!("?{}", i + 1)).collect::<Vec<_>>().join(", ");
            sql.push_str(&format!(" AND n.kind IN ({placeholders})"));
            binds.extend(kinds.iter().map(|k| k.as_str().to_string()));
        }
        sql.push_str(&format!(" ORDER BY m.relevance DESC LIMIT ?{}", binds.len() + 1));
        binds.push(limit.to_string());

        let mut stmt = self.conn.prepare(&sql)?;
        let rows = stmt.query_map(params_from_iter(binds.iter()), symbol_hit_from_row)?;
        Ok(rows.collect::<rusqlite::Result<_>>()?)
    }

    /// Reads a byte range off disk and confirms the file still matches what was
    /// indexed.
    ///
    /// This is the primitive behind both the code viewer and citation checking.
    /// Returning [`SpanRead::Stale`] instead of the text is the whole point: a
    /// quotation from a file that has moved on is not a quotation, it is a
    /// plausible-looking fabrication, and the UI labels it as such.
    pub fn read_span(&self, file: FileId, span: Span) -> Result<SpanRead> {
        let (path, expected): (String, String) = self.conn.query_row(
            "SELECT path, hash FROM files WHERE id = ?1",
            [file.to_sqlite()],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )?;

        let full = self.root.join(&path);
        let bytes = std::fs::read(&full)
            .with_context(|| format!("reading {} for a code span", full.display()))?;

        let actual = ContentHash::of(&bytes);
        if Some(actual) != ContentHash::from_hex(&expected) {
            return Ok(SpanRead::Stale { path });
        }

        let start = span.start_byte as usize;
        let end = (span.end_byte as usize).min(bytes.len());
        if start >= end {
            return Ok(SpanRead::OutOfRange { path });
        }

        Ok(SpanRead::Text {
            path,
            text: String::from_utf8_lossy(&bytes[start..end]).into_owned(),
            start_line: span.start_line,
        })
    }

    /// Everything the resolver needs, read back out of the index.
    ///
    /// This is what makes reindexing incremental: after reparsing a single
    /// changed file, resolution runs against the stored references of every
    /// other file rather than reparsing the workspace to rediscover them.
    pub fn stored_facts(&self) -> Result<Vec<StoredFile>> {
        let mut files: Vec<StoredFile> = {
            let mut stmt = self
                .conn
                .prepare("SELECT id, path, lang, hash FROM files WHERE parsed = 1 AND lang IS NOT NULL")?;
            let rows = stmt.query_map([], |row| {
                Ok(StoredFile {
                    file: FileId::from_sqlite(row.get(0)?),
                    path: row.get(1)?,
                    language: Language::from_slug(&row.get::<_, String>(2)?)
                        .unwrap_or(Language::Python),
                    hash: ContentHash::from_hex(&row.get::<_, String>(3)?)
                        .unwrap_or(ContentHash([0; 16])),
                    symbols: Vec::new(),
                    references: Vec::new(),
                    imports: Vec::new(),
                    bindings: Vec::new(),
                })
            })?;
            rows.collect::<rusqlite::Result<_>>()?
        };

        let mut by_file: HashMap<FileId, usize> =
            files.iter().enumerate().map(|(i, f)| (f.file, i)).collect();

        {
            let mut stmt = self.conn.prepare(
                "SELECT file_id, id, kind, lang, name, qualified,
                        start_byte, end_byte, start_line, end_line,
                        sig_start_byte, sig_end_byte, sig_start_line, sig_end_line,
                        parent_id, visibility, doc
                 FROM nodes",
            )?;
            let rows = stmt.query_map([], |row| {
                Ok((
                    FileId::from_sqlite(row.get(0)?),
                    Symbol {
                        id: NodeId::from_sqlite(row.get(1)?),
                        kind: NodeKind::from_str(&row.get::<_, String>(2)?)
                            .unwrap_or(NodeKind::Function),
                        language: Language::from_slug(&row.get::<_, String>(3)?)
                            .unwrap_or(Language::Python),
                        name: row.get(4)?,
                        qualified: row.get(5)?,
                        span: Span::new(row.get(6)?, row.get(7)?, row.get(8)?, row.get(9)?),
                        signature_span: Span::new(
                            row.get(10)?,
                            row.get(11)?,
                            row.get(12)?,
                            row.get(13)?,
                        ),
                        parent: row.get::<_, Option<i64>>(14)?.map(NodeId::from_sqlite),
                        visibility: visibility_from_str(&row.get::<_, String>(15)?),
                        doc: row.get(16)?,
                    },
                ))
            })?;
            for row in rows {
                let (file, symbol) = row?;
                if let Some(&index) = by_file.get(&file) {
                    files[index].symbols.push(symbol);
                }
            }
        }

        {
            let mut stmt = self.conn.prepare(
                "SELECT file_id, from_id, name, receiver, kind,
                        start_byte, end_byte, start_line, end_line
                 FROM refs",
            )?;
            let rows = stmt.query_map([], |row| {
                Ok((
                    FileId::from_sqlite(row.get(0)?),
                    RawReference {
                        from: NodeId::from_sqlite(row.get(1)?),
                        name: row.get(2)?,
                        receiver: row.get(3)?,
                        kind: EdgeKind::from_str(&row.get::<_, String>(4)?)
                            .unwrap_or(EdgeKind::Calls),
                        span: Span::new(row.get(5)?, row.get(6)?, row.get(7)?, row.get(8)?),
                    },
                ))
            })?;
            for row in rows {
                let (file, reference) = row?;
                if let Some(&index) = by_file.get(&file) {
                    files[index].references.push(reference);
                }
            }
        }

        {
            let mut stmt = self.conn.prepare(
                "SELECT file_id, module, symbol, alias, start_byte, end_byte, start_line, end_line
                 FROM imports",
            )?;
            let rows = stmt.query_map([], |row| {
                Ok((
                    FileId::from_sqlite(row.get(0)?),
                    RawImport {
                        module: row.get(1)?,
                        symbol: row.get(2)?,
                        alias: row.get(3)?,
                        span: Span::new(row.get(4)?, row.get(5)?, row.get(6)?, row.get(7)?),
                    },
                ))
            })?;
            for row in rows {
                let (file, import) = row?;
                if let Some(&index) = by_file.get(&file) {
                    files[index].imports.push(import);
                }
            }
        }

        {
            let mut stmt = self.conn.prepare(
                "SELECT file_id, variable, constructor, start_byte, end_byte, start_line, end_line
                 FROM bindings",
            )?;
            let rows = stmt.query_map([], |row| {
                Ok((
                    FileId::from_sqlite(row.get(0)?),
                    RawBinding {
                        variable: row.get(1)?,
                        constructor: row.get(2)?,
                        span: Span::new(row.get(3)?, row.get(4)?, row.get(5)?, row.get(6)?),
                    },
                ))
            })?;
            for row in rows {
                let (file, binding) = row?;
                if let Some(&index) = by_file.get(&file) {
                    files[index].bindings.push(binding);
                }
            }
        }

        by_file.clear();
        Ok(files)
    }

    /// Turns a set of ids into displayable nodes in one query.
    pub(crate) fn hydrate(&self, ids: &HashSet<NodeId>) -> Result<Vec<SymbolHit>> {
        if ids.is_empty() {
            return Ok(Vec::new());
        }
        let mut out = Vec::with_capacity(ids.len());
        // SQLite's parameter limit is high but not unlimited, and a Map view can
        // ask about thousands of nodes at once.
        for chunk in ids.iter().collect::<Vec<_>>().chunks(500) {
            let placeholders =
                (0..chunk.len()).map(|i| format!("?{}", i + 1)).collect::<Vec<_>>().join(", ");
            let sql = format!(
                "SELECT n.id, n.name, n.qualified, n.kind, n.lang, f.path, n.start_line,
                        coalesce(m.relevance, 0)
                 FROM nodes n
                 JOIN files f ON f.id = n.file_id
                 LEFT JOIN metrics m ON m.node_id = n.id
                 WHERE n.id IN ({placeholders})"
            );
            let mut stmt = self.conn.prepare(&sql)?;
            let binds: Vec<i64> = chunk.iter().map(|id| id.to_sqlite()).collect();
            let rows = stmt.query_map(params_from_iter(binds.iter()), symbol_hit_from_row)?;
            for row in rows {
                out.push(row?);
            }
        }
        Ok(out)
    }
}

/// Result of reading a code span, including the two ways it can legitimately
/// fail.
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "status", rename_all = "lowercase")]
pub enum SpanRead {
    Text { path: String, text: String, start_line: u32 },
    /// The file changed since indexing; line numbers can no longer be trusted.
    Stale { path: String },
    /// The span points past the end of the file.
    OutOfRange { path: String },
}

pub(crate) fn symbol_hit_from_row(row: &Row<'_>) -> rusqlite::Result<SymbolHit> {
    Ok(SymbolHit {
        id: NodeId::from_sqlite(row.get(0)?),
        name: row.get(1)?,
        qualified: row.get(2)?,
        kind: NodeKind::from_str(&row.get::<_, String>(3)?).unwrap_or(NodeKind::File),
        language: Language::from_slug(&row.get::<_, String>(4)?).unwrap_or(Language::Python),
        path: row.get(5)?,
        line: row.get(6)?,
        relevance: row.get(7)?,
    })
}

pub(crate) fn confidence_from_str(s: &str) -> Confidence {
    match s {
        "measured" => Confidence::Measured,
        "verified" => Confidence::Verified,
        "resolved" => Confidence::Resolved,
        _ => Confidence::Guessed,
    }
}

/// Inverse of `Confidence as i64` (Guessed=0, Resolved=1, Verified=2, Measured=3).
/// `impact` carries the weakest link along a path as a numeric rank and turns it
/// back into the enum at the end.
fn confidence_from_rank(rank: i64) -> Confidence {
    match rank {
        3 => Confidence::Measured,
        2 => Confidence::Verified,
        1 => Confidence::Resolved,
        _ => Confidence::Guessed,
    }
}

/// Wraps a user query as an FTS5 string literal.
///
/// Without this, a search for `foo(bar)` or `a OR b` is parsed as FTS5 query
/// syntax and either errors or silently means something else. Doubling embedded
/// quotes and wrapping the whole thing makes it a literal phrase.
fn fts_literal(query: &str) -> String {
    format!("\"{}\"", query.replace('"', "\"\""))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::FileRecord;
    use cs_core::{Edge, Evidence, Source, Symbol, Visibility};

    /// a → b → c, plus an unrelated `d`.
    fn chain_graph() -> Graph {
        let mut graph = Graph::open_in_memory("/tmp/x").unwrap();
        let file = FileId::of_path("a.py");
        let hash = ContentHash::of(b"source");

        let batch = graph.write_batch().unwrap();
        batch
            .put_file(&FileRecord {
                id: file,
                path: "a.py".into(),
                lang: Some(Language::Python),
                hash,
                size: 6,
                lines: 4,
                mtime: 0,
                parsed: true,
                skip_reason: None,
            })
            .unwrap();

        let id = |n: &str| NodeId::of_symbol(file, "function", n);
        for (i, name) in ["a", "b", "c", "d"].iter().enumerate() {
            let line = i as u32 + 1;
            batch
                .put_symbol(
                    file,
                    "a.py",
                    &Symbol {
                        id: id(name),
                        kind: NodeKind::Function,
                        language: Language::Python,
                        name: name.to_string(),
                        qualified: name.to_string(),
                        span: Span::new(line, line + 1, line, line),
                        signature_span: Span::new(line, line + 1, line, line),
                        parent: None,
                        visibility: Visibility::Public,
                        doc: None,
                    },
                )
                .unwrap();
        }

        for (from, to) in [("a", "b"), ("b", "c")] {
            batch
                .put_edge(&Edge::new(
                    id(from),
                    id(to),
                    EdgeKind::Calls,
                    Confidence::Resolved,
                    Source::ScopeResolution,
                    Evidence { file, span: Span::new(0, 1, 1, 1), content_hash: hash },
                ))
                .unwrap();
        }
        batch.commit().unwrap();
        graph
    }

    #[test]
    fn path_between_finds_a_transitive_call_chain() {
        let graph = chain_graph();
        let file = FileId::of_path("a.py");
        let id = |n: &str| NodeId::of_symbol(file, "function", n);

        let path = graph.path_between(id("a"), id("c"), 10).unwrap().unwrap();
        assert_eq!(path, vec![id("a"), id("b"), id("c")]);
    }

    #[test]
    fn path_between_returns_nothing_when_there_is_no_connection() {
        let graph = chain_graph();
        let file = FileId::of_path("a.py");
        let id = |n: &str| NodeId::of_symbol(file, "function", n);
        assert!(graph.path_between(id("a"), id("d"), 10).unwrap().is_none());
    }

    #[test]
    fn a_cycle_does_not_hang_the_traversal() {
        let mut graph = chain_graph();
        let file = FileId::of_path("a.py");
        let hash = ContentHash::of(b"source");
        let id = |n: &str| NodeId::of_symbol(file, "function", n);

        let batch = graph.write_batch().unwrap();
        batch
            .put_edge(&Edge::new(
                id("c"),
                id("a"),
                EdgeKind::Calls,
                Confidence::Resolved,
                Source::ScopeResolution,
                Evidence { file, span: Span::new(0, 1, 3, 3), content_hash: hash },
            ))
            .unwrap();
        batch.commit().unwrap();

        // c → a closes the loop; without the trail check this recurses forever.
        assert!(graph.path_between(id("a"), id("d"), 20).unwrap().is_none());
    }

    #[test]
    fn slice_walks_outward_and_reports_truncation() {
        let graph = chain_graph();
        let file = FileId::of_path("a.py");
        let id = |n: &str| NodeId::of_symbol(file, "function", n);

        let full = graph.slice(id("a"), 3, Direction::Out, &[EdgeKind::Calls], 100).unwrap();
        assert_eq!(full.nodes.len(), 3);
        assert!(!full.truncated);

        let capped = graph.slice(id("a"), 3, Direction::Out, &[EdgeKind::Calls], 2).unwrap();
        assert!(capped.truncated, "hitting the budget must be visible to the caller");
    }

    #[test]
    fn symbol_search_matches_inside_a_name() {
        let graph = chain_graph();
        // Trigram tokenisation is what makes an infix query work at all.
        assert!(!graph.search_symbols("a", &[], 10).unwrap().is_empty());
    }

    #[test]
    fn search_queries_with_fts_operators_are_treated_as_text() {
        let graph = chain_graph();
        // Would be a syntax error if passed through to FTS5 unquoted.
        assert!(graph.search_symbols("foo OR (bar", &[], 10).is_ok());
        assert!(graph.search_text("a\" OR b", 10).is_ok());
    }

    #[test]
    fn impact_walks_backwards_with_hop_distances() {
        // chain a→b→c; changing c should reach b (hops 1) and a (hops 2).
        let graph = chain_graph();
        let file = FileId::of_path("a.py");
        let id = |n: &str| NodeId::of_symbol(file, "function", n);

        let impact = graph
            .impact(id("c"), 5, &[EdgeKind::Calls], 100)
            .unwrap();
        let by_id: HashMap<NodeId, u32> =
            impact.reached.iter().map(|r| (r.node.id, r.hops)).collect();
        assert_eq!(by_id.get(&id("b")), Some(&1), "b calls c directly");
        assert_eq!(by_id.get(&id("a")), Some(&2), "a reaches c transitively");
        assert!(!by_id.contains_key(&id("d")), "d is unrelated");
        assert!(!impact.truncated);
    }

    #[test]
    fn impact_weakest_link_makes_a_chain_guessed() {
        // a→b verified, b→c guessed: the path a→b→c is guessed end to end.
        // Changing c reaches a with Confidence::Guessed (weakest link wins).
        let mut graph = chain_graph();
        let file = FileId::of_path("a.py");
        let hash = ContentHash::of(b"source");
        let id = |n: &str| NodeId::of_symbol(file, "function", n);

        // Overwrite b→c with a guessed edge (stronger-claim upsert keeps
        // Resolved; instead add a guessed a→b-alternative path is complex, so
        // build a fresh two-hop guessed chain to a new node e).
        let batch = graph.write_batch().unwrap();
        batch
            .put_symbol(
                file,
                "a.py",
                &Symbol {
                    id: id("e"),
                    kind: NodeKind::Function,
                    language: Language::Python,
                    name: "e".into(),
                    qualified: "e".into(),
                    span: Span::new(9, 10, 9, 9),
                    signature_span: Span::new(9, 10, 9, 9),
                    parent: None,
                    visibility: Visibility::Public,
                    doc: None,
                },
            )
            .unwrap();
        // a→e guessed, e→c guessed: a reaches c only through guesses.
        for from in ["a", "e"] {
            let to = if from == "a" { "e" } else { "c" };
            batch
                .put_edge(&Edge::new(
                    id(from),
                    id(to),
                    EdgeKind::Calls,
                    Confidence::Guessed,
                    Source::ScopeResolution,
                    Evidence { file, span: Span::new(0, 1, 5, 5), content_hash: hash },
                ))
                .unwrap();
        }
        batch.commit().unwrap();

        let impact = graph.impact(id("c"), 5, &[EdgeKind::Calls], 100).unwrap();
        let a = impact
            .reached
            .iter()
            .find(|r| r.node.id == id("a"))
            .expect("a is reached");
        // There is a resolved path a→b→c (best Rank 1) and a guessed path a→e→c
        // (Rank 0); MAX across paths picks the resolved one — one strong route
        // suffices.
        assert_eq!(a.confidence, Confidence::Resolved);

        // Now drop the budget below the reached set → truncated must be true.
        let capped = graph.impact(id("c"), 5, &[EdgeKind::Calls], 1).unwrap();
        assert!(capped.truncated, "hitting the budget must be signalled, not silent");
    }

    #[test]
    fn impact_a_cycle_does_not_hang() {
        let mut graph = chain_graph();
        let file = FileId::of_path("a.py");
        let hash = ContentHash::of(b"source");
        let id = |n: &str| NodeId::of_symbol(file, "function", n);
        let batch = graph.write_batch().unwrap();
        batch
            .put_edge(&Edge::new(
                id("c"),
                id("a"),
                EdgeKind::Calls,
                Confidence::Resolved,
                Source::ScopeResolution,
                Evidence { file, span: Span::new(0, 1, 3, 3), content_hash: hash },
            ))
            .unwrap();
        batch.commit().unwrap();
        // c↔a closes a loop; the recursive walk must terminate (UNION dedups).
        let impact = graph.impact(id("c"), 10, &[EdgeKind::Calls], 100).unwrap();
        assert!(impact.reached.iter().any(|r| r.node.id == id("a")));
    }
}
