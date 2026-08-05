//! PageRank over the reference graph.
//!
//! The question it answers is "how important is this symbol to the code around
//! it", and it answers it better than call counts do: a function called once by
//! the request handler matters more than one called twenty times by tests.
//!
//! Only reference edges go in. Containment would make every file outrank the
//! functions in it, and type edges would let a widely-used data class swamp the
//! logic that actually does the work.

use cs_core::NodeId;
use std::collections::HashMap;

const DAMPING: f64 = 0.85;
const MAX_ITERATIONS: usize = 40;
/// Converged once the total change across all nodes drops below this. Ranks are
/// only ever compared to each other, so more precision buys nothing.
const EPSILON: f64 = 1e-7;

pub struct Graph {
    /// Index-based adjacency; ids are mapped once so the hot loop is over `Vec`s.
    out_edges: Vec<Vec<usize>>,
    in_edges: Vec<Vec<usize>>,
    ids: Vec<NodeId>,
}

impl Graph {
    pub fn build(nodes: &[NodeId], edges: &[(NodeId, NodeId)]) -> Self {
        let index: HashMap<NodeId, usize> =
            nodes.iter().enumerate().map(|(i, id)| (*id, i)).collect();

        let mut out_edges = vec![Vec::new(); nodes.len()];
        let mut in_edges = vec![Vec::new(); nodes.len()];

        for (from, to) in edges {
            let (Some(&from), Some(&to)) = (index.get(from), index.get(to)) else {
                // An edge into a node that was pruned. Dropping it is correct;
                // keeping it would give rank to something that no longer exists.
                continue;
            };
            if from == to {
                // Self-recursion says nothing about importance and inflates the
                // rank of any recursive helper.
                continue;
            }
            out_edges[from].push(to);
            in_edges[to].push(from);
        }

        Self { out_edges, in_edges, ids: nodes.to_vec() }
    }

    pub fn fan_in(&self, index: usize) -> u32 {
        self.in_edges[index].len() as u32
    }

    pub fn fan_out(&self, index: usize) -> u32 {
        self.out_edges[index].len() as u32
    }

    pub fn ids(&self) -> &[NodeId] {
        &self.ids
    }

    /// Runs the iteration and returns a score per node, in `ids` order.
    pub fn pagerank(&self) -> Vec<f64> {
        let n = self.ids.len();
        if n == 0 {
            return Vec::new();
        }

        let base = 1.0 / n as f64;
        let mut rank = vec![base; n];
        let mut next = vec![0.0; n];

        for _ in 0..MAX_ITERATIONS {
            // Nodes with no outgoing edges would otherwise leak their rank out of
            // the system entirely; it is redistributed evenly instead.
            let dangling: f64 = (0..n)
                .filter(|i| self.out_edges[*i].is_empty())
                .map(|i| rank[i])
                .sum();

            let leaked = DAMPING * dangling / n as f64;
            let teleport = (1.0 - DAMPING) / n as f64;

            for slot in next.iter_mut() {
                *slot = teleport + leaked;
            }

            for source in 0..n {
                let targets = &self.out_edges[source];
                if targets.is_empty() {
                    continue;
                }
                let share = DAMPING * rank[source] / targets.len() as f64;
                for &target in targets {
                    next[target] += share;
                }
            }

            let delta: f64 = rank.iter().zip(next.iter()).map(|(a, b)| (a - b).abs()).sum();
            rank.copy_from_slice(&next);
            if delta < EPSILON {
                break;
            }
        }

        rank
    }

    /// Hops from the nearest entry point, following edges forwards.
    ///
    /// `None` means unreachable from any entry point — which is a strong hint of
    /// dead code, and equally a hint that an entry point was missed. The UI says
    /// "nicht von einem Einstiegspunkt erreichbar" rather than "toter Code",
    /// because with dynamic dispatch in play those are not the same claim.
    pub fn reach_depths(&self, entry_points: &[NodeId]) -> Vec<Option<u32>> {
        let index: HashMap<NodeId, usize> =
            self.ids.iter().enumerate().map(|(i, id)| (*id, i)).collect();

        let mut depth = vec![None; self.ids.len()];
        let mut queue = std::collections::VecDeque::new();

        for entry in entry_points {
            if let Some(&i) = index.get(entry) {
                if depth[i].is_none() {
                    depth[i] = Some(0);
                    queue.push_back(i);
                }
            }
        }

        while let Some(current) = queue.pop_front() {
            let next_depth = depth[current].expect("queued nodes are always assigned") + 1;
            for &target in &self.out_edges[current] {
                if depth[target].is_none() {
                    depth[target] = Some(next_depth);
                    queue.push_back(target);
                }
            }
        }

        depth
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use cs_core::FileId;

    fn node(name: &str) -> NodeId {
        NodeId::of_symbol(FileId::of_path("t.py"), "function", name)
    }

    #[test]
    fn a_widely_referenced_node_outranks_a_leaf() {
        let nodes: Vec<NodeId> = ["hub", "a", "b", "c", "lonely"].iter().map(|n| node(n)).collect();
        let edges = vec![
            (node("a"), node("hub")),
            (node("b"), node("hub")),
            (node("c"), node("hub")),
        ];
        let graph = Graph::build(&nodes, &edges);
        let ranks = graph.pagerank();

        let hub = ranks[0];
        let lonely = ranks[4];
        assert!(hub > lonely, "hub {hub} should outrank isolated node {lonely}");
    }

    #[test]
    fn ranks_form_a_probability_distribution() {
        let nodes: Vec<NodeId> = ["a", "b", "c"].iter().map(|n| node(n)).collect();
        let edges = vec![(node("a"), node("b")), (node("b"), node("c"))];
        let total: f64 = Graph::build(&nodes, &edges).pagerank().iter().sum();
        // Dangling mass is redistributed rather than lost, so the total stays 1.
        assert!((total - 1.0).abs() < 1e-6, "ranks summed to {total}");
    }

    #[test]
    fn a_cycle_converges_instead_of_spinning() {
        let nodes: Vec<NodeId> = ["a", "b"].iter().map(|n| node(n)).collect();
        let edges = vec![(node("a"), node("b")), (node("b"), node("a"))];
        let ranks = Graph::build(&nodes, &edges).pagerank();
        assert!((ranks[0] - ranks[1]).abs() < 1e-6, "a symmetric cycle must be symmetric");
    }

    #[test]
    fn self_recursion_does_not_inflate_rank() {
        let nodes: Vec<NodeId> = ["a", "b"].iter().map(|n| node(n)).collect();
        let recursive = Graph::build(&nodes, &[(node("a"), node("a"))]).pagerank();
        let plain = Graph::build(&nodes, &[]).pagerank();
        assert!((recursive[0] - plain[0]).abs() < 1e-9);
    }

    #[test]
    fn reachability_measures_distance_from_an_entry_point() {
        let nodes: Vec<NodeId> = ["main", "a", "b", "orphan"].iter().map(|n| node(n)).collect();
        let edges = vec![(node("main"), node("a")), (node("a"), node("b"))];
        let depths = Graph::build(&nodes, &edges).reach_depths(&[node("main")]);

        assert_eq!(depths[0], Some(0));
        assert_eq!(depths[1], Some(1));
        assert_eq!(depths[2], Some(2));
        assert_eq!(depths[3], None, "unreachable must be distinguishable from depth 0");
    }

    #[test]
    fn an_empty_graph_is_handled_without_panicking() {
        assert!(Graph::build(&[], &[]).pagerank().is_empty());
    }
}
