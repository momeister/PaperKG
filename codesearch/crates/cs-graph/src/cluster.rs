//! Directory rollup: the graph seen from far enough away to be a shape.
//!
//! A repository is not a flat bag of symbols. Large parts of it do one thing —
//! this folder fetches, that one persists, those are the routes — and that
//! division is the first thing someone entering an unfamiliar codebase needs.
//! The symbol map answers "what does this function do"; nothing answered "what
//! is this project made of".
//!
//! Three decisions, all of them for a reason:
//!
//! **The grouping is the directory tree, not a computed community.** It is
//! deterministic, it nests by itself, it survives reindexing unchanged, and a
//! reader can check it against their own file browser. A Louvain partition would
//! find coupling that crosses folders, but its groups would drift between runs
//! and have no name anyone could verify. The names can be improved by a model
//! later; the *structure* must not depend on one.
//!
//! **Nothing is stored.** A rollup is a `GROUP BY` over `files.path`, `nodes` and
//! `edges`, so it is correct the moment indexing finishes and there is no second
//! copy to go stale. Persisting it would buy a few milliseconds and cost a
//! consistency problem.
//!
//! **An aggregated edge carries the weakest confidence it contains, never the
//! most common one.** Rolling up a hundred edges of which ninety are verified
//! and ten are guessed into "verified" would launder a guess into a fact at
//! exactly the zoom level where nobody can check it. The count and the weakest
//! grade travel together, and [`Graph::cluster_edge_detail`] opens the aggregate
//! back into the real edges with their own evidence.

use crate::query::{Neighbour, SymbolHit};
use crate::Graph;
use anyhow::Result;
use cs_core::{Confidence, EdgeKind, NodeKind};
use rusqlite::params_from_iter;
use serde::Serialize;
use std::collections::BTreeMap;

/// How many representative symbols a cluster carries for its preview.
const TOP_SYMBOLS: usize = 5;

/// One area of the project: a path prefix and everything indexed beneath it.
#[derive(Debug, Clone, Serialize)]
pub struct ClusterNode {
    /// The prefix itself — `"query"`, `"query/retrieval"`. Never a symbol, and
    /// used as the node's identity everywhere above this layer.
    pub path: String,
    /// Last path segment. A model may add a friendlier name on top; this one is
    /// always available and always true.
    pub label: String,
    pub depth: u32,
    /// Whether descending one more level would show anything new.
    pub has_children: bool,
    pub symbols: usize,
    pub files: usize,
    /// Symbol kinds and their counts — the profile that says "routes live here".
    pub kinds: Vec<(NodeKind, usize)>,
    /// Highest symbol relevance in the cluster. A max, not a sum: a folder does
    /// not become important by containing many unimportant things.
    pub relevance: f64,
    /// Share of outgoing relationships that are only guessed, 0..1.
    pub guessed_share: f64,
    pub top_symbols: Vec<SymbolHit>,
}

/// An aggregated relationship between two areas.
#[derive(Debug, Clone, Serialize)]
pub struct ClusterEdge {
    pub from: String,
    pub to: String,
    /// Number of real edges behind this one.
    pub count: usize,
    /// Their occurrences summed — how often the relationship is actually written.
    pub occurrences: u64,
    /// The **weakest** confidence among them. See the module note.
    pub weakest: Confidence,
    pub kinds: Vec<(EdgeKind, usize)>,
}

/// One level of the rollup.
#[derive(Debug, Clone, Serialize)]
pub struct ClusterLevel {
    /// The prefix that was asked for; `""` is the top level.
    pub prefix: String,
    /// One level up, or `None` at the top.
    pub parent: Option<String>,
    pub nodes: Vec<ClusterNode>,
    pub edges: Vec<ClusterEdge>,
}

/// Cuts a file path down to the cluster it belongs to at this level.
///
/// `("query/retrieval/kg.py", "")` → `Some("query")`
/// `("query/retrieval/kg.py", "query")` → `Some("query/retrieval")`
/// `("query/kg.py", "query")` → `Some("query/kg.py")` — a file directly in the
/// prefix is its own leaf cluster rather than vanishing, otherwise the symbols
/// of `query/kg.py` would be invisible at every level.
/// `("other/x.py", "query")` → `None`
fn cluster_of(path: &str, prefix: &str) -> Option<String> {
    let rest = if prefix.is_empty() {
        path
    } else {
        let with_sep = format!("{prefix}/");
        path.strip_prefix(&with_sep)?
    };
    let head = rest.split('/').next()?;
    if head.is_empty() {
        return None;
    }
    Some(if prefix.is_empty() { head.to_string() } else { format!("{prefix}/{head}") })
}

/// True when this cluster has anything below it — i.e. the path continues.
fn has_more(path: &str, cluster: &str) -> bool {
    path.len() > cluster.len() && path.as_bytes().get(cluster.len()) == Some(&b'/')
}

#[derive(Default)]
struct Accumulator {
    files: BTreeMap<String, ()>,
    symbols: usize,
    kinds: BTreeMap<String, usize>,
    relevance: f64,
    has_children: bool,
    guessed: usize,
    edges_out: usize,
}

impl Graph {
    /// The areas of the project at `prefix`, and how they relate.
    ///
    /// `prefix` is `""` for the top level, then `"query"`, `"query/retrieval"`
    /// and so on. `kinds` filters which edge kinds count towards the aggregated
    /// relationships; empty means all of them.
    pub fn clusters(&self, prefix: &str, kinds: &[EdgeKind]) -> Result<ClusterLevel> {
        let prefix = prefix.trim_matches('/').to_string();
        let mut acc: BTreeMap<String, Accumulator> = BTreeMap::new();

        // Pass one: every symbol, bucketed by the cluster its file falls into.
        {
            let mut stmt = self.conn.prepare(
                "SELECT f.path, n.kind, coalesce(m.relevance, 0)
                 FROM nodes n
                 JOIN files f ON f.id = n.file_id
                 LEFT JOIN metrics m ON m.node_id = n.id",
            )?;
            let rows = stmt.query_map([], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, f64>(2)?,
                ))
            })?;
            for row in rows {
                let (path, kind, relevance) = row?;
                let Some(cluster) = cluster_of(&path, &prefix) else { continue };
                let entry = acc.entry(cluster.clone()).or_default();
                entry.files.insert(path.clone(), ());
                entry.symbols += 1;
                *entry.kinds.entry(kind).or_default() += 1;
                if relevance > entry.relevance {
                    entry.relevance = relevance;
                }
                if has_more(&path, &cluster) {
                    entry.has_children = true;
                }
            }
        }

        if acc.is_empty() {
            return Ok(ClusterLevel {
                prefix: prefix.clone(),
                parent: parent_of(&prefix),
                nodes: Vec::new(),
                edges: Vec::new(),
            });
        }

        // Pass two: every edge, mapped onto the pair of clusters it crosses.
        let mut pairs: BTreeMap<(String, String), ClusterEdge> = BTreeMap::new();
        {
            let (filter, binds) = edge_kind_filter(kinds);
            let sql = format!(
                "SELECT ff.path, tf.path, e.kind, e.confidence, e.occurrences
                 FROM edges e
                 JOIN nodes fn ON fn.id = e.from_id
                 JOIN nodes tn ON tn.id = e.to_id
                 JOIN files ff ON ff.id = fn.file_id
                 JOIN files tf ON tf.id = tn.file_id
                 WHERE 1 = 1{filter}"
            );
            let mut stmt = self.conn.prepare(&sql)?;
            let rows = stmt.query_map(params_from_iter(binds.iter()), |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, i64>(4)?,
                ))
            })?;
            for row in rows {
                let (from_path, to_path, kind, confidence, occurrences) = row?;
                let (Some(from), Some(to)) =
                    (cluster_of(&from_path, &prefix), cluster_of(&to_path, &prefix))
                else {
                    continue;
                };
                let confidence = confidence_of(&confidence);

                // The guessed share is a property of the *source* cluster: "how
                // much of what this area claims about the world is a guess".
                if let Some(entry) = acc.get_mut(&from) {
                    entry.edges_out += 1;
                    if confidence == Confidence::Guessed {
                        entry.guessed += 1;
                    }
                }

                // A cluster's edges to itself say nothing about how areas relate,
                // and drawn on a map they are a loop on every single node.
                if from == to {
                    continue;
                }
                let Some(kind) = EdgeKind::from_str(&kind) else { continue };

                let edge = pairs.entry((from.clone(), to.clone())).or_insert_with(|| ClusterEdge {
                    from,
                    to,
                    count: 0,
                    occurrences: 0,
                    weakest: Confidence::Measured,
                    kinds: Vec::new(),
                });
                edge.count += 1;
                edge.occurrences += occurrences.max(0) as u64;
                if confidence < edge.weakest {
                    edge.weakest = confidence;
                }
                match edge.kinds.iter_mut().find(|(existing, _)| *existing == kind) {
                    Some((_, count)) => *count += 1,
                    None => edge.kinds.push((kind, 1)),
                }
            }
        }

        let depth = if prefix.is_empty() { 1 } else { prefix.split('/').count() as u32 + 1 };
        let mut nodes: Vec<ClusterNode> = acc
            .into_iter()
            .map(|(path, entry)| {
                let mut kinds: Vec<(NodeKind, usize)> = entry
                    .kinds
                    .into_iter()
                    .filter_map(|(name, count)| NodeKind::from_str(&name).map(|k| (k, count)))
                    .collect();
                kinds.sort_by(|a, b| b.1.cmp(&a.1));
                ClusterNode {
                    label: path.rsplit('/').next().unwrap_or(&path).to_string(),
                    depth,
                    has_children: entry.has_children,
                    symbols: entry.symbols,
                    files: entry.files.len(),
                    kinds,
                    relevance: entry.relevance,
                    guessed_share: if entry.edges_out == 0 {
                        0.0
                    } else {
                        entry.guessed as f64 / entry.edges_out as f64
                    },
                    top_symbols: Vec::new(),
                    path,
                }
            })
            .collect();

        for node in &mut nodes {
            node.top_symbols = self.cluster_members(&node.path, TOP_SYMBOLS)?;
        }
        nodes.sort_by(|a, b| b.symbols.cmp(&a.symbols).then_with(|| a.path.cmp(&b.path)));

        let mut edges: Vec<ClusterEdge> = pairs.into_values().collect();
        for edge in &mut edges {
            edge.kinds.sort_by(|a, b| b.1.cmp(&a.1));
        }
        edges.sort_by(|a, b| b.count.cmp(&a.count).then_with(|| a.from.cmp(&b.from)));

        Ok(ClusterLevel { parent: parent_of(&prefix), prefix, nodes, edges })
    }

    /// The most relevant symbols anywhere under `prefix`.
    pub fn cluster_members(&self, prefix: &str, limit: usize) -> Result<Vec<SymbolHit>> {
        let prefix = prefix.trim_matches('/');
        let mut stmt = self.conn.prepare(
            "SELECT n.id, n.name, n.qualified, n.kind, n.lang, f.path, n.start_line,
                    coalesce(m.relevance, 0) AS rel
             FROM nodes n
             JOIN files f ON f.id = n.file_id
             LEFT JOIN metrics m ON m.node_id = n.id
             WHERE (?1 = '' OR f.path = ?1 OR f.path LIKE ?2 ESCAPE '\\')
               AND n.kind NOT IN ('file', 'module')
             ORDER BY rel DESC, n.name ASC
             LIMIT ?3",
        )?;
        let like = format!("{}/%", escape_like(prefix));
        let rows = stmt.query_map(
            rusqlite::params![prefix, like, limit as i64],
            crate::query::symbol_hit_from_row,
        )?;
        Ok(rows.collect::<rusqlite::Result<_>>()?)
    }

    /// The real edges behind one aggregated cluster edge — the proof for the
    /// number on the arrow.
    ///
    /// Without this the rollup would be exactly the kind of unfalsifiable
    /// summary the rest of this tool exists to avoid.
    pub fn cluster_edge_detail(
        &self,
        from: &str,
        to: &str,
        kinds: &[EdgeKind],
        limit: usize,
    ) -> Result<Vec<Neighbour>> {
        let from = from.trim_matches('/');
        let to = to.trim_matches('/');
        let (kind_filter, kind_binds) = edge_kind_filter(kinds);

        let sql = format!(
            "SELECT tn.id, tn.name, tn.qualified, tn.kind, tn.lang, tf.path, tn.start_line,
                    coalesce(m.relevance, 0),
                    e.kind, e.confidence, e.candidates, e.occurrences,
                    ef.path, e.ev_start_line,
                    fn.qualified, ff.path, fn.start_line
             FROM edges e
             JOIN nodes fn ON fn.id = e.from_id
             JOIN nodes tn ON tn.id = e.to_id
             JOIN files ff ON ff.id = fn.file_id
             JOIN files tf ON tf.id = tn.file_id
             JOIN files ef ON ef.id = e.ev_file
             LEFT JOIN metrics m ON m.node_id = tn.id
             WHERE (?{a} = '' OR ff.path = ?{a} OR ff.path LIKE ?{b} ESCAPE '\\')
               AND (?{c} = '' OR tf.path = ?{c} OR tf.path LIKE ?{d} ESCAPE '\\')
               {kind_filter}
             ORDER BY e.conf_rank ASC, e.occurrences DESC
             LIMIT ?{e}",
            a = kind_binds.len() + 1,
            b = kind_binds.len() + 2,
            c = kind_binds.len() + 3,
            d = kind_binds.len() + 4,
            e = kind_binds.len() + 5,
        );

        let mut binds: Vec<String> = kind_binds;
        binds.push(from.to_string());
        binds.push(format!("{}/%", escape_like(from)));
        binds.push(to.to_string());
        binds.push(format!("{}/%", escape_like(to)));
        binds.push(limit.to_string());

        let mut stmt = self.conn.prepare(&sql)?;
        let rows = stmt.query_map(params_from_iter(binds.iter()), |row| {
            Ok(Neighbour {
                node: SymbolHit {
                    id: cs_core::NodeId::from_sqlite(row.get(0)?),
                    name: row.get(1)?,
                    qualified: row.get(2)?,
                    kind: NodeKind::from_str(&row.get::<_, String>(3)?).unwrap_or(NodeKind::File),
                    language: cs_core::Language::from_slug(&row.get::<_, String>(4)?)
                        .unwrap_or(cs_core::Language::Python),
                    path: row.get(5)?,
                    line: row.get(6)?,
                    relevance: row.get(7)?,
                },
                kind: EdgeKind::from_str(&row.get::<_, String>(8)?).unwrap_or(EdgeKind::Calls),
                confidence: confidence_of(&row.get::<_, String>(9)?),
                candidates: row.get::<_, i64>(10)? as u16,
                occurrences: row.get::<_, i64>(11)? as u32,
                evidence_path: row.get(12)?,
                evidence_line: row.get(13)?,
            })
        })?;
        Ok(rows.collect::<rusqlite::Result<_>>()?)
    }
}

fn parent_of(prefix: &str) -> Option<String> {
    if prefix.is_empty() {
        return None;
    }
    Some(match prefix.rfind('/') {
        Some(index) => prefix[..index].to_string(),
        None => String::new(),
    })
}

/// `LIKE` treats `%` and `_` as wildcards, and paths legitimately contain `_`.
/// Without escaping, `some_dir` would also match `someXdir`.
fn escape_like(value: &str) -> String {
    value.replace('\\', "\\\\").replace('%', "\\%").replace('_', "\\_")
}

fn edge_kind_filter(kinds: &[EdgeKind]) -> (String, Vec<String>) {
    if kinds.is_empty() {
        return (String::new(), Vec::new());
    }
    let placeholders =
        (0..kinds.len()).map(|i| format!("?{}", i + 1)).collect::<Vec<_>>().join(", ");
    (
        format!(" AND e.kind IN ({placeholders})"),
        kinds.iter().map(|k| k.as_str().to_string()).collect(),
    )
}

fn confidence_of(value: &str) -> Confidence {
    match value {
        "measured" => Confidence::Measured,
        "verified" => Confidence::Verified,
        "resolved" => Confidence::Resolved,
        _ => Confidence::Guessed,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cluster_of_takes_the_first_segment_below_the_prefix() {
        assert_eq!(cluster_of("query/retrieval/kg.py", ""), Some("query".into()));
        assert_eq!(
            cluster_of("query/retrieval/kg.py", "query"),
            Some("query/retrieval".into())
        );
        assert_eq!(cluster_of("query/kg.py", "query"), Some("query/kg.py".into()));
        assert_eq!(cluster_of("other/x.py", "query"), None);
        // A prefix must match on a segment boundary, not on characters: `query`
        // is not the parent of `querytools`.
        assert_eq!(cluster_of("querytools/x.py", "query"), None);
    }

    #[test]
    fn has_more_only_when_the_path_continues_at_a_separator() {
        assert!(has_more("query/retrieval/kg.py", "query"));
        assert!(!has_more("query/kg.py", "query/kg.py"));
        assert!(!has_more("querytools/kg.py", "query"));
    }

    #[test]
    fn parent_walks_one_level_up_and_stops_at_the_root() {
        assert_eq!(parent_of(""), None);
        assert_eq!(parent_of("query"), Some(String::new()));
        assert_eq!(parent_of("query/retrieval"), Some("query".into()));
    }

    #[test]
    fn like_escaping_keeps_underscores_literal() {
        assert_eq!(escape_like("some_dir"), "some\\_dir");
        assert_eq!(escape_like("a%b"), "a\\%b");
    }
}
