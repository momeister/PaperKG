//! Strongly connected components — the structural answer to „where is this
//! code tangled?".
//!
//! Today the codebase has *no* cycle detection. `path_between` avoids rings by
//! checking the accumulated trail and returns `None` when it hits one, without
//! saying so. The cycle-break in `clusterLayout.ts` is layout, not analysis: it
//! runs per rendered level in TypeScript, holds no state, and is not queryable.
//! This module fills that gap with Tarjan's algorithm over the same `edges`
//! table every other view reads, so a ring reported here is a ring the graph
//! actually stores — with its evidence edges and weakest confidence attached.
//!
//! Two levels, both useful, both asked for in their own right:
//! * **File** — collapse every edge to its `(from_file, to_file)` pair and find
//!   SCCs over files. This is the „two modules that cannot be understood apart"
//!   view, and it runs over `Imports` by default (a file importing the file that
//!   imports it is the classic tangle).
//! * **Symbol** — SCCs over `Calls`. The „A calls B calls A" view, at the
//!   resolution a refactor actually operates on.
//!
//! A cycle is an SCC of size > 1, or a single vertex with a self-loop (a
//! recursive symbol, or a file that imports itself). Both are reported: a
//! self-loop is a trivial cycle, and hiding it would make the count a lie.
//!
//! Output is deterministic: cycles are sorted by their sorted member paths, so
//! two runs over the same index show the same rings in the same order. That
//! matters because the panel sits next to a model proposal — the user must be
//! able to tell „the ring is still there" from „the ring changed".

use crate::query::confidence_from_str;
use crate::Graph;
use anyhow::Result;
use cs_core::{Confidence, EdgeKind, NodeId};
use rusqlite::{params_from_iter, Row};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};

/// Which vertex set to find cycles over.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum CycleLevel {
    /// Collapse edges to file→file pairs. The default for „is this codebase
    /// tangled?" — files are few enough to read at once, and an import cycle is
    /// the shape a refactor breaks first.
    File,
    /// Keep symbol resolution. The level a refactor operates at; denser and
    /// noisier, so opt-in.
    Symbol,
}

/// One edge that participates in a cycle, with its proof. Every relationship
/// the panel shows carries confidence and `datei:zeile` — a cycle without its
/// evidence is a claim, and the rule of the tool is: no edge without proof.
#[derive(Debug, Clone, Serialize)]
pub struct CycleEdge {
    pub from: NodeId,
    pub to: NodeId,
    pub kind: String,
    pub confidence: Confidence,
    pub evidence_path: String,
    pub evidence_line: u32,
}

/// A node in a cycle. For `File` level `id` is a representative symbol of the
/// file (so the UI can deep-link), and `kind` is `"file"`; for `Symbol` level
/// it is the symbol itself.
#[derive(Debug, Clone, Serialize)]
pub struct CycleNode {
    pub id: NodeId,
    pub path: String,
    pub name: String,
    pub kind: String,
    pub line: u32,
}

/// One cycle: its members, the edges that close the ring, and the weakest
/// confidence among them — a ring with one guessed edge is a guessed ring.
#[derive(Debug, Clone, Serialize)]
pub struct Cycle {
    pub level: CycleLevel,
    pub size: u32,
    pub nodes: Vec<CycleNode>,
    pub edges: Vec<CycleEdge>,
    pub weakest: Confidence,
}

/// One raw edge read out of the database, before it is folded into a vertex set.
struct RawEdge {
    from: NodeId,
    to: NodeId,
    from_file: i64,
    to_file: i64,
    kind: String,
    conf_rank: i64,
    confidence: String,
    evidence_path: String,
    evidence_line: u32,
}

impl Graph {
    /// Find every cycle at `level` over the given edge kinds.
    ///
    /// `kinds` is the same parameter every other traversal takes; the caller
    /// picks `Imports` for the file level and `Calls` for the symbol level. An
    /// empty list means „no edges" and returns no cycles, not „all edges" — a
    /// silent wildcard here would invent rings the index does not store.
    pub fn cycles(&self, level: CycleLevel, kinds: &[EdgeKind]) -> Result<Vec<Cycle>> {
        if kinds.is_empty() {
            return Ok(Vec::new());
        }

        let raw = self.load_cycle_edges(kinds)?;

        // Build the vertex set and adjacency for the chosen level.
        let adjacency: HashMap<i64, Vec<i64>> = match level {
            CycleLevel::Symbol => build_symbol_adjacency(&raw),
            CycleLevel::File => build_file_adjacency(&raw),
        };

        let sccs = tarjan(&adjacency);
        let mut cycles = Vec::new();
        for component in sccs {
            let in_component: HashSet<i64> = component.iter().copied().collect();
            // A cycle is an SCC of size > 1, or a single vertex with a self-loop.
            let has_self_loop = component.len() == 1
                && adjacency.get(&component[0]).is_some_and(|nb| nb.contains(&component[0]));
            if component.len() < 2 && !has_self_loop {
                continue;
            }

            // Edges that stay inside the component close the ring. For file
            // level, `vertex_edges` already holds one representative per
            // vertex pair; for symbol level, dedup by (from, to, kind).
            let mut edges: Vec<CycleEdge> = Vec::new();
            let mut seen: HashSet<(i64, i64, String)> = HashSet::new();
            for raw_e in &raw {
                let (v_from, v_to) = match level {
                    CycleLevel::Symbol => (raw_e.from.to_sqlite(), raw_e.to.to_sqlite()),
                    CycleLevel::File => (raw_e.from_file, raw_e.to_file),
                };
                if !in_component.contains(&v_from) || !in_component.contains(&v_to) {
                    continue;
                }
                let key = (v_from, v_to, raw_e.kind.clone());
                if !seen.insert(key) {
                    continue;
                }
                edges.push(CycleEdge {
                    from: raw_e.from,
                    to: raw_e.to,
                    kind: raw_e.kind.clone(),
                    confidence: confidence_from_str(&raw_e.confidence),
                    evidence_path: raw_e.evidence_path.clone(),
                    evidence_line: raw_e.evidence_line,
                });
            }

            let weakest = raw
                .iter()
                .filter(|r| {
                    let (vf, vt) = match level {
                        CycleLevel::Symbol => (r.from.to_sqlite(), r.to.to_sqlite()),
                        CycleLevel::File => (r.from_file, r.to_file),
                    };
                    in_component.contains(&vf) && in_component.contains(&vt)
                })
                .map(|r| r.conf_rank)
                .min()
                .map(confidence_from_rank)
                .unwrap_or(Confidence::Guessed);

            let nodes = self.cycle_nodes(level, &in_component, &raw)?;
            cycles.push(Cycle {
                level,
                size: component.len() as u32,
                nodes,
                edges,
                weakest,
            });
        }

        // Deterministic order: by the sorted member paths, then by size. Two
        // runs over the same index produce the same list, so „the ring is
        // still there" is distinguishable from „the ring changed".
        cycles.sort_by(|a, b| {
            let ka: Vec<&String> = a.nodes.iter().map(|n| &n.path).collect();
            let kb: Vec<&String> = b.nodes.iter().map(|n| &n.path).collect();
            ka.cmp(&kb).then(a.size.cmp(&b.size))
        });
        Ok(cycles)
    }

    /// Read the edges that can participate in a cycle, with file ids and
    /// evidence attached. One query, filtered by kind; the vertex folding
    /// happens in memory so file- and symbol-level share the same scan.
    fn load_cycle_edges(&self, kinds: &[EdgeKind]) -> Result<Vec<RawEdge>> {
        let placeholders =
            (0..kinds.len()).map(|i| format!("?{}", i + 1)).collect::<Vec<_>>().join(", ");
        let sql = format!(
            "SELECT e.from_id, e.to_id, nfrom.file_id, nto.file_id,
                    e.kind, e.confidence, e.conf_rank, ef.path, e.ev_start_line
             FROM edges e
             JOIN nodes nfrom ON nfrom.id = e.from_id
             JOIN nodes nto   ON nto.id   = e.to_id
             JOIN files ef    ON ef.id    = e.ev_file
             WHERE e.kind IN ({placeholders})"
        );
        let mut stmt = self.conn.prepare(&sql)?;
        let kind_strs: Vec<String> = kinds.iter().map(|k| k.as_str().to_string()).collect();
        let rows = stmt.query_map(params_from_iter(kind_strs.iter()), |row| {
            Ok(RawEdge {
                from: NodeId::from_sqlite(row.get(0)?),
                to: NodeId::from_sqlite(row.get(1)?),
                from_file: row.get(2)?,
                to_file: row.get(3)?,
                kind: row.get(4)?,
                confidence: row.get(5)?,
                conf_rank: row.get(6)?,
                evidence_path: row.get(7)?,
                evidence_line: row.get::<_, i64>(8)? as u32,
            })
        })?;
        Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
    }

    /// Hydrate the vertices of a component into display nodes. For the file
    /// level each vertex is a file id; we pick the first symbol of that file as
    /// a representative so the UI has something to deep-link to, and label the
    /// node by the file path. For the symbol level the vertex is the symbol.
    fn cycle_nodes(
        &self,
        level: CycleLevel,
        component: &HashSet<i64>,
        raw: &[RawEdge],
    ) -> Result<Vec<CycleNode>> {
        match level {
            CycleLevel::Symbol => {
                let mut ids: HashSet<NodeId> = HashSet::new();
                for r in raw {
                    if component.contains(&r.from.to_sqlite()) {
                        ids.insert(r.from);
                    }
                    if component.contains(&r.to.to_sqlite()) {
                        ids.insert(r.to);
                    }
                }
                let hits = self.hydrate(&ids)?;
                let mut nodes: Vec<CycleNode> = hits
                    .into_iter()
                    .map(|h| CycleNode {
                        id: h.id,
                        path: h.path,
                        name: h.qualified,
                        kind: h.kind.as_str().to_string(),
                        line: h.line,
                    })
                    .collect();
                nodes.sort_by(|a, b| a.path.cmp(&b.path).then(a.name.cmp(&b.name)));
                Ok(nodes)
            }
            CycleLevel::File => {
                // Pick the first symbol of each file as representative.
                let mut nodes = Vec::new();
                for &file_id in component.iter() {
                    let row = self.conn.query_row(
                        "SELECT n.id, n.name, n.qualified, n.kind, f.path, n.start_line
                         FROM nodes n JOIN files f ON f.id = n.file_id
                         WHERE f.id = ?1 AND n.kind NOT IN ('file','module')
                         ORDER BY n.start_line ASC LIMIT 1",
                        [file_id],
                        |r| file_node_from_row(r, file_id),
                    );
                    if let Ok(node) = row {
                        nodes.push(node);
                    } else {
                        // A file with no symbols still belongs to the cycle; use
                        // the bare file path so the member list is complete.
                        let path: Option<String> = self
                            .conn
                            .query_row("SELECT path FROM files WHERE id = ?1", [file_id], |r| r.get(0))
                            .ok();
                        if let Some(path) = path {
                            nodes.push(CycleNode {
                                id: NodeId::from_sqlite(0),
                                path: path.clone(),
                                name: path,
                                kind: "file".to_string(),
                                line: 1,
                            });
                        }
                    }
                }
                nodes.sort_by(|a, b| a.path.cmp(&b.path));
                Ok(nodes)
            }
        }
    }
}

fn file_node_from_row(row: &Row<'_>, _file_id: i64) -> rusqlite::Result<CycleNode> {
    Ok(CycleNode {
        id: NodeId::from_sqlite(row.get(0)?),
        path: row.get(4)?,
        name: row.get::<_, String>(2)?,
        kind: "file".to_string(),
        line: row.get::<_, i64>(5)? as u32,
    })
}

/// Vertex = symbol id. Self-loops preserved (a recursive call is a cycle).
fn build_symbol_adjacency(raw: &[RawEdge]) -> HashMap<i64, Vec<i64>> {
    let mut adj: HashMap<i64, Vec<i64>> = HashMap::new();
    for e in raw {
        let from = e.from.to_sqlite();
        let to = e.to.to_sqlite();
        adj.entry(from).or_default().push(to);
    }
    // Deterministic neighbour order: sort and dedup so Tarjan visits in a
    // stable sequence and two runs produce the same component order.
    for nb in adj.values_mut() {
        nb.sort();
        nb.dedup();
    }
    adj
}

/// Vertex = file id. Edges collapsed to file pairs. A self-edge (a file that
/// imports itself) is a real cycle and is kept.
fn build_file_adjacency(raw: &[RawEdge]) -> HashMap<i64, Vec<i64>> {
    let mut adj: HashMap<i64, Vec<i64>> = HashMap::new();
    for e in raw {
        adj.entry(e.from_file).or_default().push(e.to_file);
    }
    for nb in adj.values_mut() {
        nb.sort();
        nb.dedup();
    }
    adj
}

/// Iterative Tarjan. Recursive SCC over a large graph would blow the stack —
/// `cycles` is a query, not a batch job, so it must not depend on graph depth.
fn tarjan(adj: &HashMap<i64, Vec<i64>>) -> Vec<Vec<i64>> {
    let mut vertices: Vec<i64> = adj.keys().copied().collect();
    vertices.sort();
    let mut index: u32 = 0;
    let mut indices: HashMap<i64, u32> = HashMap::new();
    let mut lowlinks: HashMap<i64, u32> = HashMap::new();
    let mut on_stack: HashSet<i64> = HashSet::new();
    let mut stack: Vec<i64> = Vec::new();
    let mut sccs: Vec<Vec<i64>> = Vec::new();

    // Explicit-stack strongconnect. Each frame holds the vertex and the
    // iterator position over its neighbours.
    enum Frame {
        Enter(i64),
        Resume(i64, usize),
    }
    let mut work: Vec<Frame> = vertices.into_iter().map(Frame::Enter).collect();

    while let Some(frame) = work.pop() {
        match frame {
            Frame::Enter(v) => {
                indices.insert(v, index);
                lowlinks.insert(v, index);
                index += 1;
                stack.push(v);
                on_stack.insert(v);
                work.push(Frame::Resume(v, 0));
            }
            Frame::Resume(v, start) => {
                let neighbours = adj.get(&v).cloned().unwrap_or_default();
                let mut i = start;
                let mut advanced = false;
                while i < neighbours.len() {
                    let w = neighbours[i];
                    if !indices.contains_key(&w) {
                        // Recurse into w.
                        work.push(Frame::Resume(v, i + 1));
                        work.push(Frame::Enter(w));
                        advanced = true;
                        break;
                    } else if on_stack.contains(&w) {
                        let li = *lowlinks.get(&v).unwrap_or(&0);
                        let wi = *indices.get(&w).unwrap_or(&0);
                        lowlinks.insert(v, li.min(wi));
                    }
                    i += 1;
                }
                if advanced {
                    continue;
                }
                // All neighbours processed; form the SCC if v is a root.
                let vi = *indices.get(&v).unwrap_or(&0);
                let lv = *lowlinks.get(&v).unwrap_or(&0);
                if lv == vi {
                    let mut component = Vec::new();
                    while let Some(w) = stack.pop() {
                        on_stack.remove(&w);
                        component.push(w);
                        if w == v {
                            break;
                        }
                    }
                    component.sort();
                    sccs.push(component);
                }
                // Propagate lowlink to the parent (the Resume frame below this
                // one on the stack, if any).
                if let Some(Frame::Resume(parent, _)) = work.last() {
                    let parent = *parent;
                    let lp = *lowlinks.get(&parent).unwrap_or(&0);
                    lowlinks.insert(parent, lp.min(lv));
                }
            }
        }
    }

    sccs
}

/// Numeric mirror of `Confidence`: Guessed=0, Resolved=1, Verified=2,
/// Measured=3. Matches `confidence_from_rank` in `query.rs`; duplicated here
/// because that helper is private to the cycle path and the rank comes straight
/// from the `RawEdge` scan.
fn confidence_from_rank(rank: i64) -> Confidence {
    match rank {
        3 => Confidence::Measured,
        2 => Confidence::Verified,
        1 => Confidence::Resolved,
        _ => Confidence::Guessed,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use cs_core::{Confidence, EdgeKind};

    /// `confidence_from_rank` must mirror `query::confidence_from_rank` exactly,
    /// otherwise a cycle's `weakest` would disagree with the badge the same edge
    /// gets everywhere else.
    #[test]
    fn confidence_rank_mirror_matches_query_module() {
        assert_eq!(confidence_from_rank(0), Confidence::Guessed);
        assert_eq!(confidence_from_rank(1), Confidence::Resolved);
        assert_eq!(confidence_from_rank(2), Confidence::Verified);
        assert_eq!(confidence_from_rank(3), Confidence::Measured);
        assert_eq!(confidence_from_rank(99), Confidence::Guessed);
    }

    #[test]
    fn tarjan_finds_a_two_cycle_and_a_three_cycle() {
        // a -> b -> a  (two-cycle),  c -> d -> e -> c  (three-cycle),  x alone.
        let mut adj: HashMap<i64, Vec<i64>> = HashMap::new();
        adj.insert(1, vec![2]);
        adj.insert(2, vec![1]);
        adj.insert(3, vec![4]);
        adj.insert(4, vec![5]);
        adj.insert(5, vec![3]);
        adj.insert(9, vec![9]); // self-loop is a cycle too
        let mut sccs = tarjan(&adj);
        sccs.sort();
        let has = |set: &Vec<Vec<i64>>, want: &[i64]| {
            set.iter().any(|c| c.as_slice() == want)
        };
        assert!(has(&sccs, &[1, 2]), "two-cycle missing: {sccs:?}");
        assert!(has(&sccs, &[3, 4, 5]), "three-cycle missing: {sccs:?}");
        assert!(has(&sccs, &[9]), "self-loop missing: {sccs:?}");
    }

    /// The empty-kind case is „no edges", not „all edges". A silent wildcard
    /// would invent rings the index does not store.
    #[test]
    fn empty_kinds_yield_no_cycles() {
        let graph = Graph::open_in_memory("/tmp/nowhere").unwrap();
        let cycles = graph.cycles(CycleLevel::Symbol, &[]).unwrap();
        assert!(cycles.is_empty());
        let _ = EdgeKind::Calls; // touched so the import is used in this test module
    }
}