//! How relevant a symbol is, and — just as importantly — why.
//!
//! The score is a weighted blend of five signals, each normalised by *rank*
//! rather than by magnitude. Percentile normalisation matters here: PageRank and
//! churn are both heavy-tailed, so one file rewritten two hundred times would
//! flatten every other file's churn contribution to nearly zero under min-max
//! scaling. Ranking asks the question the user actually has — "is this in the
//! busiest tenth of the codebase?" — instead of "how does this compare to the
//! single worst outlier?".
//!
//! Every input stays in the `metrics` table alongside the score, so the UI can
//! open the number up and show what produced it. A relevance bar you cannot
//! interrogate is just a vibe with a decimal point.

pub mod pagerank;

use anyhow::Result;
use cs_core::{EdgeKind, NodeId};
use cs_git::HistoryReport;
use cs_graph::Graph as CodeGraph;
use rusqlite::params;
use serde::Serialize;
use std::collections::HashMap;

/// Contribution of each signal to the final score.
///
/// Structure leads because it is the only signal that is true of the code
/// itself; the rest describe how people have treated it. Exposed so the UI can
/// label the breakdown with the same numbers the backend used, rather than
/// hard-coding a second copy that will drift.
#[derive(Debug, Clone, Copy, Serialize)]
pub struct Weights {
    pub structure: f64,
    pub reach: f64,
    pub churn: f64,
    pub risk: f64,
    pub coverage: f64,
}

pub const WEIGHTS: Weights =
    Weights { structure: 0.35, reach: 0.25, churn: 0.20, risk: 0.12, coverage: 0.08 };

/// Recomputes every ranking signal and writes it to the `metrics` table.
///
/// This is a whole-graph pass, not an incremental one: PageRank is global, so a
/// single new call edge can shift scores anywhere. It runs after indexing
/// completes and takes well under a second on repositories of a few tens of
/// thousands of nodes.
pub fn apply(
    graph: &mut CodeGraph,
    history: &HistoryReport,
    entry_points: &[NodeId],
) -> Result<()> {
    let (ids, paths) = load_nodes(graph)?;
    if ids.is_empty() {
        return Ok(());
    }

    let edges = load_reference_edges(graph)?;
    let built = pagerank::Graph::build(&ids, &edges);

    let ranks = built.pagerank();
    let depths = built.reach_depths(entry_points);

    let fan_in: Vec<u32> = (0..ids.len()).map(|i| built.fan_in(i)).collect();
    let fan_out: Vec<u32> = (0..ids.len()).map(|i| built.fan_out(i)).collect();

    let churn: Vec<u32> = paths
        .iter()
        .map(|p| history.by_path.get(p).map(|h| h.churn).unwrap_or(0))
        .collect();
    let risk: Vec<u32> = paths
        .iter()
        .map(|p| history.by_path.get(p).map(|h| h.risk).unwrap_or(0))
        .collect();
    let authors: Vec<u32> = paths
        .iter()
        .map(|p| history.by_path.get(p).map(|h| h.authors).unwrap_or(0))
        .collect();
    let last_touched: Vec<Option<i64>> =
        paths.iter().map(|p| history.by_path.get(p).and_then(|h| h.last_touched)).collect();

    let structure_score = percentile_ranks(&ranks);
    let reach_score: Vec<f64> = depths
        .iter()
        .zip(percentile_ranks(&fan_in.iter().map(|v| *v as f64).collect::<Vec<_>>()))
        .map(|(depth, fan)| match depth {
            // Reachable from an entry point and close to it counts most; the
            // decay is gentle so a helper five hops deep is not written off.
            Some(d) => (fan + 1.0 / (1.0 + *d as f64)) / 2.0,
            // Unreachable code still gets its fan-in share: it may simply mean
            // we did not recognise the entry point.
            None => fan * 0.5,
        })
        .collect();
    let churn_score = percentile_ranks(&churn.iter().map(|v| *v as f64).collect::<Vec<_>>());
    let risk_score = percentile_ranks(&risk.iter().map(|v| *v as f64).collect::<Vec<_>>());

    let tx = graph.connection().unchecked_transaction()?;
    {
        let mut stmt = tx.prepare(
            "INSERT INTO metrics
                 (node_id, pagerank, fan_in, fan_out, reach_depth,
                  churn, risk, authors, last_touched, relevance)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)
             ON CONFLICT(node_id) DO UPDATE SET
                 pagerank = excluded.pagerank, fan_in = excluded.fan_in,
                 fan_out = excluded.fan_out, reach_depth = excluded.reach_depth,
                 churn = excluded.churn, risk = excluded.risk,
                 authors = excluded.authors, last_touched = excluded.last_touched,
                 relevance = excluded.relevance",
        )?;

        for i in 0..ids.len() {
            // Coverage is not imported yet; a neutral 0.5 keeps its weight from
            // silently penalising every symbol in a repo with no coverage data.
            let coverage_score = 0.5;
            let relevance = WEIGHTS.structure * structure_score[i]
                + WEIGHTS.reach * reach_score[i]
                + WEIGHTS.churn * churn_score[i]
                + WEIGHTS.risk * risk_score[i]
                + WEIGHTS.coverage * coverage_score;

            stmt.execute(params![
                ids[i].to_sqlite(),
                ranks[i],
                fan_in[i],
                fan_out[i],
                depths[i],
                churn[i],
                risk[i],
                authors[i],
                last_touched[i],
                relevance,
            ])?;
        }
    }
    tx.commit()?;

    Ok(())
}

/// Maps values to their position in the sorted order, scaled to `0.0..=1.0`.
///
/// Ties share a score, so a thousand symbols with zero churn all land at the
/// same low value instead of being ordered arbitrarily by row id.
fn percentile_ranks(values: &[f64]) -> Vec<f64> {
    let n = values.len();
    if n <= 1 {
        return vec![0.5; n];
    }

    let mut order: Vec<usize> = (0..n).collect();
    order.sort_by(|a, b| values[*a].partial_cmp(&values[*b]).unwrap_or(std::cmp::Ordering::Equal));

    let mut out = vec![0.0; n];
    let mut i = 0;
    while i < n {
        let mut j = i;
        while j + 1 < n && (values[order[j + 1]] - values[order[i]]).abs() < f64::EPSILON {
            j += 1;
        }
        // Midpoint of the tied block, so a tie is not rewarded for its position.
        let score = ((i + j) as f64 / 2.0) / (n - 1) as f64;
        for &index in &order[i..=j] {
            out[index] = score;
        }
        i = j + 1;
    }
    out
}

fn load_nodes(graph: &CodeGraph) -> Result<(Vec<NodeId>, Vec<String>)> {
    let mut stmt = graph
        .connection()
        .prepare("SELECT n.id, f.path FROM nodes n JOIN files f ON f.id = n.file_id")?;
    let rows = stmt.query_map([], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?)))?;

    let mut ids = Vec::new();
    let mut paths = Vec::new();
    for row in rows {
        let (id, path) = row?;
        ids.push(NodeId::from_sqlite(id));
        paths.push(path);
    }
    Ok((ids, paths))
}

fn load_reference_edges(graph: &CodeGraph) -> Result<Vec<(NodeId, NodeId)>> {
    let kinds: Vec<&str> = [
        EdgeKind::Calls,
        EdgeKind::Reads,
        EdgeKind::Writes,
        EdgeKind::Inherits,
        EdgeKind::Implements,
    ]
    .iter()
    .map(|k| k.as_str())
    .collect();

    let placeholders = (1..=kinds.len()).map(|i| format!("?{i}")).collect::<Vec<_>>().join(", ");
    let sql = format!("SELECT from_id, to_id FROM edges WHERE kind IN ({placeholders})");

    let mut stmt = graph.connection().prepare(&sql)?;
    let rows = stmt.query_map(rusqlite::params_from_iter(kinds.iter()), |r| {
        Ok((NodeId::from_sqlite(r.get(0)?), NodeId::from_sqlite(r.get(1)?)))
    })?;
    Ok(rows.collect::<rusqlite::Result<_>>()?)
}

/// Convenience for callers that want the same breakdown the score was built
/// from, without re-reading the weights.
pub fn breakdown(
    structure: f64,
    reach: f64,
    churn: f64,
    risk: f64,
    coverage: f64,
) -> HashMap<&'static str, f64> {
    HashMap::from([
        ("struktur", WEIGHTS.structure * structure),
        ("reichweite", WEIGHTS.reach * reach),
        ("änderungen", WEIGHTS.churn * churn),
        ("risiko", WEIGHTS.risk * risk),
        ("abdeckung", WEIGHTS.coverage * coverage),
    ])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn weights_sum_to_one_so_relevance_stays_in_range() {
        let total = WEIGHTS.structure
            + WEIGHTS.reach
            + WEIGHTS.churn
            + WEIGHTS.risk
            + WEIGHTS.coverage;
        assert!((total - 1.0).abs() < 1e-9, "weights summed to {total}");
    }

    #[test]
    fn percentiles_spread_across_the_full_range() {
        let ranks = percentile_ranks(&[10.0, 20.0, 30.0, 40.0]);
        assert_eq!(ranks[0], 0.0);
        assert_eq!(ranks[3], 1.0);
        assert!(ranks[1] < ranks[2]);
    }

    #[test]
    fn ties_share_a_score_instead_of_being_ordered_arbitrarily() {
        let ranks = percentile_ranks(&[5.0, 5.0, 5.0, 9.0]);
        assert_eq!(ranks[0], ranks[1]);
        assert_eq!(ranks[1], ranks[2]);
        assert!(ranks[3] > ranks[0]);
    }

    #[test]
    fn one_outlier_does_not_flatten_everyone_else() {
        // The reason for percentile rather than min-max: with churn values like
        // these, min-max would put the first three within 1% of each other.
        let ranks = percentile_ranks(&[1.0, 2.0, 3.0, 1000.0]);
        assert!(ranks[2] - ranks[0] > 0.5, "the ordinary range must stay legible");
    }

    #[test]
    fn a_single_value_gets_a_neutral_score() {
        assert_eq!(percentile_ranks(&[42.0]), vec![0.5]);
        assert!(percentile_ranks(&[]).is_empty());
    }
}
