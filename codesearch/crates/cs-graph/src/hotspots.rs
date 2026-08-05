//! Spaghetti hotspots — symbols that trip a measured rule.
//!
//! The raw material has been in the index since phase 1 and was never queried:
//! `nodes.start_line/end_line` (so `loc = end_line - start_line + 1`), the
//! `metrics` table (`fan_in`, `fan_out`, `churn`, `risk`, `relevance`), and
//! `facts.json` with `complexity`, `max_nesting`. This module is the query that
//! was missing.
//!
//! **No invented overall score.** Every hotspot carries *which rule* it tripped
//! and *with what measured value* — the proof principle, applied to numbers. A
//! single „risk: 8.3" number would be a claim just like an edge without
//! `datei:zeile`; a row that says „complexity 22 > 15" is a fact the reader can
//! check against the function. A symbol can trip several rules; the list is
//! sorted by rule count, then by relevance — the function that breaks four
//! rules is the one to look at first.
//!
//! `churn` is relative, so its threshold is the upper decile of churn among the
//! callable symbols of *this* index, computed here in Rust rather than fixed in
//! config. The other thresholds come from the caller (config.yaml); a value of
//! `0` means „rule disabled" — a threshold of 0 would flag every symbol and is
//! not a useful default.

use crate::query::symbol_hit_from_row;
use crate::Graph;
use serde::Serialize;

/// One measured rule a symbol tripped, with the value that tripped it. The UI
/// renders this next to the symbol so „hotspot" is a conclusion the reader can
/// audit, not a label to trust.
#[derive(Debug, Clone, Serialize)]
pub struct HotspotRule {
    pub rule: String,
    pub value: f64,
    pub threshold: f64,
}

/// A symbol that tripped at least one rule, with all the measured signals and
/// the rules it broke.
#[derive(Debug, Clone, Serialize)]
pub struct Hotspot {
    pub node: serde_json::Value, // SymbolHit, serialised via the shared row helper
    pub loc: u32,
    pub complexity: u32,
    pub max_nesting: u32,
    pub fan_in: u32,
    pub fan_out: u32,
    pub churn: u32,
    pub risk: u32,
    pub rules: Vec<HotspotRule>,
}

/// Cutoffs for the hotspot rules. A field set to `0` disables that rule —
/// passing it through as a threshold of 0 would flag every callable symbol,
/// which is noise, not a diagnosis. The churn decile is computed per index and
/// overrides `churn` when `churn == 0`.
#[derive(Debug, Clone, Copy)]
pub struct HotspotThresholds {
    pub loc: u32,
    pub complexity: u32,
    pub max_nesting: u32,
    pub fan_in: u32,
    pub fan_out: u32,
    pub churn: u32,
}

impl Default for HotspotThresholds {
    fn default() -> Self {
        Self {
            loc: 200,
            complexity: 15,
            max_nesting: 5,
            fan_in: 30,
            fan_out: 25,
            churn: 0, // 0 ⇒ upper decile of this index
        }
    }
}

impl Graph {
    /// Every callable symbol that trips at least one rule, strongest first.
    pub fn hotspots(
        &self,
        thresholds: &HotspotThresholds,
        limit: usize,
    ) -> rusqlite::Result<Vec<Hotspot>> {
        let churn_threshold = if thresholds.churn == 0 {
            self.churn_decile().unwrap_or(u32::MAX)
        } else {
            thresholds.churn
        };

        // Only callable symbols carry the signals that matter here; a class or
        // a route has no `complexity` in the cyclomatic sense. `kind` is read
        // as a string so the row mapping stays simple.
        let mut stmt = self.conn.prepare(
            "SELECT n.id, n.name, n.qualified, n.kind, n.lang, f.path, n.start_line,
                    coalesce(m.relevance, 0),
                    coalesce(json_extract(fa.json, '$.loc'), 0),
                    coalesce(json_extract(fa.json, '$.complexity'), 0),
                    coalesce(json_extract(fa.json, '$.max_nesting'), 0),
                    coalesce(m.fan_in, 0), coalesce(m.fan_out, 0),
                    coalesce(m.churn, 0), coalesce(m.risk, 0)
             FROM nodes n
             JOIN files f ON f.id = n.file_id
             LEFT JOIN facts fa ON fa.node_id = n.id
             LEFT JOIN metrics m ON m.node_id = n.id
             WHERE n.kind IN ('function', 'method', 'test')",
        )?;

        let mut rows: Vec<Hotspot> = Vec::new();
        let mapped = stmt.query_map([], |row| {
            let kind = row.get::<_, String>(3)?;
            Ok((
                symbol_hit_from_row(row)?,
                kind,
                row.get::<_, i64>(8)? as u32,
                row.get::<_, i64>(9)? as u32,
                row.get::<_, i64>(10)? as u32,
                row.get::<_, i64>(11)? as u32,
                row.get::<_, i64>(12)? as u32,
                row.get::<_, i64>(13)? as u32,
                row.get::<_, i64>(14)? as u32,
            ))
        })?;
        for entry in mapped {
            let (hit, _kind, loc, complexity, max_nesting, fan_in, fan_out, churn, risk) = entry?;
            let mut rules = Vec::new();
            if thresholds.loc > 0 && loc > thresholds.loc {
                rules.push(HotspotRule {
                    rule: "loc".into(),
                    value: loc as f64,
                    threshold: thresholds.loc as f64,
                });
            }
            if thresholds.complexity > 0 && complexity > thresholds.complexity {
                rules.push(HotspotRule {
                    rule: "complexity".into(),
                    value: complexity as f64,
                    threshold: thresholds.complexity as f64,
                });
            }
            if thresholds.max_nesting > 0 && max_nesting > thresholds.max_nesting {
                rules.push(HotspotRule {
                    rule: "max_nesting".into(),
                    value: max_nesting as f64,
                    threshold: thresholds.max_nesting as f64,
                });
            }
            if thresholds.fan_in > 0 && fan_in > thresholds.fan_in {
                rules.push(HotspotRule {
                    rule: "fan_in".into(),
                    value: fan_in as f64,
                    threshold: thresholds.fan_in as f64,
                });
            }
            if thresholds.fan_out > 0 && fan_out > thresholds.fan_out {
                rules.push(HotspotRule {
                    rule: "fan_out".into(),
                    value: fan_out as f64,
                    threshold: thresholds.fan_out as f64,
                });
            }
            if churn_threshold > 0 && churn >= churn_threshold && churn > 0 {
                rules.push(HotspotRule {
                    rule: "churn".into(),
                    value: churn as f64,
                    threshold: churn_threshold as f64,
                });
            }
            if rules.is_empty() {
                continue;
            }
            // Sort the rules deterministically so two runs show the same row.
            rules.sort_by(|a, b| a.rule.cmp(&b.rule));
            rows.push(Hotspot {
                node: serde_json::to_value(&hit).unwrap_or(serde_json::Value::Null),
                loc,
                complexity,
                max_nesting,
                fan_in,
                fan_out,
                churn,
                risk,
                rules,
            });
        }

        // Most rules broken first, then by relevance — the symbol that trips
        // four rules is the one to look at before the one that trips one.
        rows.sort_by(|a, b| {
            b.rules
                .len()
                .cmp(&a.rules.len())
                .then_with(|| {
                    let ra = a.node.get("relevance").and_then(|v| v.as_f64()).unwrap_or(0.0);
                    let rb = b.node.get("relevance").and_then(|v| v.as_f64()).unwrap_or(0.0);
                    rb.partial_cmp(&ra).unwrap_or(std::cmp::Ordering::Equal)
                })
        });
        rows.truncate(limit);
        Ok(rows)
    }

    /// The churn value at the 90th percentile among callable symbols with churn
    /// > 0 — the „upper decile" the plan calls for. Returns `None` when no
    /// callable symbol has churned, in which case the churn rule is disabled.
    fn churn_decile(&self) -> Option<u32> {
        let values: Vec<i64> = {
            let mut stmt = self.conn.prepare(
                "SELECT m.churn
                 FROM metrics m JOIN nodes n ON n.id = m.node_id
                 WHERE n.kind IN ('function','method','test') AND m.churn > 0
                 ORDER BY m.churn DESC",
            )
            .ok()?;
            let rows = stmt.query_map([], |r| r.get::<_, i64>(0)).ok()?;
            rows.filter_map(|r| r.ok()).collect()
        };
        if values.is_empty() {
            return None;
        }
        // Index at 10% from the top of the descending list. At least 1, so a
        // small index still flags its most-churned symbol.
        let idx = (values.len() / 10).max(1).min(values.len());
        Some(values[idx - 1] as u32)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A 0 threshold disables the rule rather than flagging everything — a
    /// threshold of 0 on `loc` would call every function a hotspot.
    #[test]
    fn a_zero_threshold_disables_the_rule() {
        let t = HotspotThresholds {
            loc: 0,
            complexity: 0,
            max_nesting: 0,
            fan_in: 0,
            fan_out: 0,
            churn: 0,
        };
        let graph = Graph::open_in_memory("/tmp/nowhere").unwrap();
        let hotspots = graph.hotspots(&t, 50).unwrap();
        assert!(hotspots.is_empty());
    }
}