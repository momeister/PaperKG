//! What the model is allowed to see.
//!
//! The assistant has no access to the repository. It has these tools, and every
//! result they return carries the confidence and the evidence location that the
//! graph recorded. That is the whole mechanism: a model cannot report a
//! relationship as certain when the only way it could have learned of the
//! relationship also told it the relationship is a guess.
//!
//! Every span handed to the model is recorded in the [`Session`]. Citation
//! verification later checks quotations against exactly that record, so the
//! model cannot cite a line it was never shown — the most common shape of a
//! confident, plausible fabrication.

use anyhow::{bail, Result};
use cs_core::{ContentHash, EdgeKind, NodeId, NodeKind};
use cs_graph::{Direction, Graph, SpanRead};
use serde::Serialize;
use serde_json::{json, Value};

use crate::provider::{FunctionSpec, ToolSpec};

/// A range the model has actually been shown.
#[derive(Debug, Clone)]
pub struct ShownSpan {
    pub path: String,
    pub from_line: u32,
    pub to_line: u32,
    pub content_hash: ContentHash,
}

/// Lines of a definition handed over by `get_node`.
///
/// Everything a tool returns is re-sent with every later request, so a single
/// long function early in a conversation is paid for over and over. This is the
/// ceiling that keeps the loop usable on CPU inference.
const MAX_SOURCE_LINES: usize = 60;

/// Lines of a docstring. Flask's `send_file` has a 100-line one; the first few
/// say what it does and the rest is parameter reference the model can look up.
const MAX_DOC_LINES: usize = 12;

/// Per-conversation record of what was retrieved.
#[derive(Debug, Default)]
pub struct Session {
    shown: Vec<ShownSpan>,
}

impl Session {
    pub fn shown(&self) -> &[ShownSpan] {
        &self.shown
    }

    /// Records that these lines were handed to the model.
    ///
    /// Public because deterministic pre-retrieval in [`crate::context`] shows the
    /// model source too, and permission to quote must follow retrieval wherever
    /// the retrieval happened — otherwise pre-fetched lines would be flagged as
    /// fabricated.
    pub fn record(&mut self, span: ShownSpan) {
        self.shown.push(span);
    }

    /// Hands the recorded spans over, consuming the session.
    ///
    /// Exists so a *conversation* can carry its licence across turns: retrieval
    /// for turn three produces a fresh session, and folding it into the running
    /// one keeps what turn one was shown quotable. Without it a follow-up that
    /// quotes the answer it is following up on gets flagged as fabricated.
    pub fn into_shown(self) -> Vec<ShownSpan> {
        self.shown
    }

    /// Whether `path:from-to` lies inside something the model was given.
    ///
    /// Generous by one line at each end: models routinely cite the signature line
    /// of a function whose body they were shown, and rejecting that would flag
    /// honest answers as fabricated.
    pub fn covers(&self, path: &str, from: u32, to: u32) -> bool {
        self.shown.iter().any(|span| {
            span.path == path
                && from + 1 >= span.from_line
                && to <= span.to_line + 1
        })
    }
}

/// The tool list handed to the model.
pub fn specs() -> Vec<ToolSpec> {
    fn spec(name: &str, description: &str, parameters: Value) -> ToolSpec {
        ToolSpec {
            spec_type: "function",
            function: FunctionSpec {
                name: name.to_string(),
                description: description.to_string(),
                parameters,
            },
        }
    }

    vec![
        spec(
            "search_symbols",
            "Sucht Funktionen, Klassen und Methoden nach Namen. Teiltreffer sind erlaubt: \
             'disc' findet 'apply_discount'. Ergebnisse sind nach Relevanz sortiert.",
            json!({
                "type": "object",
                "properties": {
                    "query": { "type": "string", "description": "Namensteil" },
                    "kind": {
                        "type": "string",
                        "description": "Optional: function, method, class, test, external_package",
                    }
                },
                "required": ["query"]
            }),
        ),
        spec(
            "search_text",
            "Volltextsuche über alle Dateien, auch über solche ohne Grammatik wie READMEs \
             und Konfiguration. Mindestens 3 Zeichen.",
            json!({
                "type": "object",
                "properties": { "query": { "type": "string" } },
                "required": ["query"]
            }),
        ),
        spec(
            "get_node",
            "Liefert alles Berechnete zu einem Symbol: Signatur, Parameter, Seiteneffekte, \
             Komplexität, Relevanz, Historie und den Quelltext der Definition.",
            json!({
                "type": "object",
                "properties": { "id": { "type": "string", "description": "Symbol-ID aus einer Suche" } },
                "required": ["id"]
            }),
        ),
        spec(
            "callers_of",
            "Wer ruft dieses Symbol auf. Jeder Treffer nennt die Sicherheitsstufe und die \
             Zeile, an der der Aufruf steht.",
            json!({
                "type": "object",
                "properties": { "id": { "type": "string" } },
                "required": ["id"]
            }),
        ),
        spec(
            "callees_of",
            "Was dieses Symbol seinerseits aufruft, mit Sicherheitsstufe und Aufrufstelle.",
            json!({
                "type": "object",
                "properties": { "id": { "type": "string" } },
                "required": ["id"]
            }),
        ),
        spec(
            "path_between",
            "Kürzester Aufrufpfad von einem Symbol zu einem anderen. Leer, wenn es keinen gibt.",
            json!({
                "type": "object",
                "properties": {
                    "from": { "type": "string" },
                    "to": { "type": "string" }
                },
                "required": ["from", "to"]
            }),
        ),
        spec(
            "read_lines",
            "Liest einen Zeilenbereich einer Datei. Nur so kommt man an Quelltext heran, und \
             nur gelesene Zeilen dürfen zitiert werden.",
            json!({
                "type": "object",
                "properties": {
                    "path": { "type": "string" },
                    "from_line": { "type": "integer" },
                    "to_line": { "type": "integer" }
                },
                "required": ["path", "from_line", "to_line"]
            }),
        ),
        spec(
            "most_relevant",
            "Die wichtigsten Symbole des Projekts. Guter Einstieg, wenn noch unklar ist, wo \
             man anfangen soll.",
            json!({ "type": "object", "properties": {} }),
        ),
    ]
}

#[derive(Serialize)]
struct SymbolOut {
    id: String,
    name: String,
    qualified: String,
    kind: &'static str,
    path: String,
    line: u32,
    relevance: u32,
}

#[derive(Serialize)]
struct NeighbourOut {
    id: String,
    qualified: String,
    path: String,
    line: u32,
    beziehung: &'static str,
    /// The word the assistant is expected to reuse verbatim when hedging.
    sicherheit: &'static str,
    /// Present only for guesses, and then always meaningful.
    #[serde(skip_serializing_if = "Option::is_none")]
    kandidaten: Option<u16>,
    beleg: String,
}

/// Runs one tool call against the graph.
///
/// Errors come back as tool results rather than as failures: telling the model
/// "das Symbol gibt es nicht" lets it correct course, whereas aborting the turn
/// leaves the user with nothing.
pub fn dispatch(graph: &Graph, session: &mut Session, name: &str, arguments: &str) -> String {
    match run(graph, session, name, arguments) {
        Ok(value) => value.to_string(),
        Err(err) => json!({ "fehler": format!("{err:#}") }).to_string(),
    }
}

fn run(graph: &Graph, session: &mut Session, name: &str, arguments: &str) -> Result<Value> {
    // Small models emit `{}`, `""` or malformed fragments surprisingly often.
    let args: Value = serde_json::from_str(arguments).unwrap_or_else(|_| json!({}));

    let string_arg = |key: &str| -> Option<String> {
        args.get(key).and_then(Value::as_str).map(str::to_string)
    };
    let id_arg = |key: &str| -> Result<NodeId> {
        let raw = args
            .get(key)
            .and_then(Value::as_str)
            .ok_or_else(|| anyhow::anyhow!("Argument '{key}' fehlt"))?;
        parse_id(raw)
    };

    match name {
        "search_symbols" => {
            let query = string_arg("query").unwrap_or_default();
            let kinds: Vec<NodeKind> =
                string_arg("kind").and_then(|k| NodeKind::from_str(&k)).into_iter().collect();
            let hits = graph.search_symbols(&query, &kinds, 15)?;

            if hits.is_empty() {
                // An empty result invites a second guess, then a third — measured
                // against a small local model, four fruitless searches for
                // synonyms of a German word before it gave up. Answering with
                // real names from the project ends that loop immediately.
                let examples: Vec<String> = graph
                    .top_by_relevance(10, &[NodeKind::Function, NodeKind::Method, NodeKind::Class])?
                    .iter()
                    .map(|hit| hit.qualified.clone())
                    .collect();

                return Ok(json!({
                    "treffer": [],
                    "hinweis": format!(
                        "Kein Symbol enthält '{query}'. Bezeichner in diesem Projekt heißen \
                         zum Beispiel: {}. Suche nach einem davon oder nutze search_text.",
                        examples.join(", ")
                    ),
                }));
            }

            Ok(json!({ "treffer": hits.iter().map(to_symbol_out).collect::<Vec<_>>() }))
        }

        "search_text" => {
            let query = string_arg("query").unwrap_or_default();
            let hits = graph.search_text(&query, 20)?;
            Ok(json!({
                "treffer": hits.iter().map(|hit| json!({
                    "pfad": hit.path,
                    "zeile": hit.line,
                    "text": hit.text,
                    "im_symbol": hit.in_symbol.map(|id| id.to_string()),
                })).collect::<Vec<_>>()
            }))
        }

        "get_node" => {
            let id = id_arg("id")?;
            let Some(detail) = graph.node(id)? else {
                bail!("Symbol nicht gefunden");
            };

            // Hand over the definition itself, and record that we did — this is
            // what makes a later quotation checkable.
            //
            // Capped, because every tool result is re-sent with every subsequent
            // request: one 400-line function early in a conversation makes each
            // later call pay for it again. Measured against Flask on CPU, an
            // uncapped `send_file` (124 lines, mostly docstring) was enough to
            // make the loop unusable. `read_lines` is there for when more is
            // genuinely needed.
            let source = match graph.read_span(cs_core::FileId::of_path(&detail.path), detail.span)? {
                SpanRead::Text { text, .. } => {
                    let lines: Vec<&str> = text.lines().collect();
                    let shown_to = if lines.len() > MAX_SOURCE_LINES {
                        detail.span.start_line + MAX_SOURCE_LINES as u32
                    } else {
                        detail.span.end_line
                    };
                    session.record(ShownSpan {
                        path: detail.path.clone(),
                        from_line: detail.span.start_line,
                        to_line: shown_to,
                        content_hash: ContentHash::of(text.as_bytes()),
                    });

                    if lines.len() > MAX_SOURCE_LINES {
                        let head = lines[..MAX_SOURCE_LINES].join("\n");
                        Some(format!(
                            "{head}\n… gekürzt nach {MAX_SOURCE_LINES} von {} Zeilen. \
                             Mit read_lines weiterlesen.",
                            lines.len()
                        ))
                    } else {
                        Some(text)
                    }
                }
                SpanRead::Stale { .. } | SpanRead::OutOfRange { .. } => None,
            };

            let facts = detail.facts.as_ref();
            Ok(json!({
                "id": detail.id.to_string(),
                "name": detail.qualified,
                "art": detail.kind.as_str(),
                "pfad": detail.path,
                "zeilen": format!("{}-{}", detail.span.start_line, detail.span.end_line),
                "doku": detail.doc.as_ref().map(|doc| {
                    let lines: Vec<&str> = doc.lines().take(MAX_DOC_LINES).collect();
                    lines.join("\n")
                }),
                "signatur": facts.map(|f| f.signature.clone()),
                "parameter": facts.map(|f| f.params.iter().map(|p| json!({
                    "name": p.name,
                    "typ": p.type_hint,
                    "standard": p.default,
                })).collect::<Vec<_>>()),
                "gibt_zurueck": facts.and_then(|f| f.returns.clone()),
                "seiteneffekte": facts.map(|f| f.side_effects.iter().map(|e| e.label()).collect::<Vec<_>>()),
                "komplexitaet": facts.map(|f| f.complexity),
                "zeilenzahl": facts.map(|f| f.loc),
                "relevanz_prozent": (detail.metrics.relevance * 100.0).round() as u32,
                "aufrufer_anzahl": detail.metrics.fan_in,
                "aenderungen": detail.metrics.churn,
                "fehlerbehebungen": detail.metrics.risk,
                "quelltext": source,
                "hinweis": if source.is_none() {
                    Some("Die Datei hat sich seit dem Einlesen geändert; der Quelltext wurde nicht mitgeliefert und darf nicht zitiert werden.")
                } else {
                    None
                },
            }))
        }

        "callers_of" | "callees_of" => {
            let id = id_arg("id")?;
            let direction = if name == "callers_of" { Direction::In } else { Direction::Out };
            let neighbours = graph.neighbours(
                id,
                direction,
                &[EdgeKind::Calls, EdgeKind::Reads, EdgeKind::Writes],
            )?;

            let out: Vec<NeighbourOut> = neighbours
                .iter()
                .take(25)
                .map(|neighbour| NeighbourOut {
                    id: neighbour.node.id.to_string(),
                    qualified: neighbour.node.qualified.clone(),
                    path: neighbour.node.path.clone(),
                    line: neighbour.node.line,
                    beziehung: neighbour.kind.as_str(),
                    sicherheit: neighbour.confidence.label(),
                    kandidaten: (neighbour.candidates > 1).then_some(neighbour.candidates),
                    beleg: format!("{}:{}", neighbour.evidence_path, neighbour.evidence_line),
                })
                .collect();

            Ok(json!({ "nachbarn": out }))
        }

        "path_between" => {
            let from = id_arg("from")?;
            let to = id_arg("to")?;
            match graph.path_between(from, to, 12)? {
                Some(path) => {
                    let steps: Vec<Value> = path
                        .iter()
                        .filter_map(|id| graph.node(*id).ok().flatten())
                        .map(|node| {
                            json!({
                                "id": node.id.to_string(),
                                "name": node.qualified,
                                "ort": format!("{}:{}", node.path, node.span.start_line),
                            })
                        })
                        .collect();
                    Ok(json!({ "pfad": steps }))
                }
                None => Ok(json!({
                    "pfad": [],
                    "hinweis": "Es gibt keinen statisch sichtbaren Aufrufpfad zwischen diesen \
                                beiden Symbolen. Das kann bedeuten, dass keiner existiert — oder \
                                dass er über eine dynamische Stelle läuft."
                })),
            }
        }

        "read_lines" => {
            let path = string_arg("path").ok_or_else(|| anyhow::anyhow!("Argument 'path' fehlt"))?;
            let from = args.get("from_line").and_then(Value::as_u64).unwrap_or(1) as u32;
            let to = args.get("to_line").and_then(Value::as_u64).unwrap_or(from as u64 + 40) as u32;

            // Capped so a model cannot pull an entire repository into context one
            // call at a time.
            let to = to.min(from + 300);

            let full = std::fs::read_to_string(graph.root().join(&path))
                .map_err(|err| anyhow::anyhow!("{path} nicht lesbar: {err}"))?;
            let lines: Vec<&str> = full.lines().collect();
            let start = from.saturating_sub(1) as usize;
            let end = (to as usize).min(lines.len());
            if start >= end {
                bail!("Zeilenbereich liegt außerhalb der Datei ({} Zeilen)", lines.len());
            }

            let text = lines[start..end]
                .iter()
                .enumerate()
                .map(|(offset, line)| format!("{:>5} | {line}", start + offset + 1))
                .collect::<Vec<_>>()
                .join("\n");

            session.record(ShownSpan {
                path: path.clone(),
                from_line: from.max(1),
                to_line: end as u32,
                content_hash: ContentHash::of(full.as_bytes()),
            });

            Ok(json!({ "pfad": path, "von": from, "bis": end, "text": text }))
        }

        "most_relevant" => {
            let hits = graph.top_by_relevance(
                15,
                &[NodeKind::Function, NodeKind::Method, NodeKind::Class],
            )?;
            Ok(json!({ "symbole": hits.iter().map(to_symbol_out).collect::<Vec<_>>() }))
        }

        other => bail!("Unbekanntes Werkzeug: {other}"),
    }
}

fn to_symbol_out(hit: &cs_graph::SymbolHit) -> SymbolOut {
    SymbolOut {
        id: hit.id.to_string(),
        name: hit.name.clone(),
        qualified: hit.qualified.clone(),
        kind: hit.kind.as_str(),
        path: hit.path.clone(),
        line: hit.line,
        relevance: (hit.relevance * 100.0).round() as u32,
    }
}

fn parse_id(hex: &str) -> Result<NodeId> {
    u64::from_str_radix(hex.trim().trim_start_matches("0x"), 16)
        .map(NodeId)
        .map_err(|_| anyhow::anyhow!("'{hex}' ist keine gültige Symbol-ID"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_span_the_model_was_shown_is_recognised_as_covered() {
        let mut session = Session::default();
        session.record(ShownSpan {
            path: "a.py".into(),
            from_line: 10,
            to_line: 20,
            content_hash: ContentHash::of(b""),
        });

        assert!(session.covers("a.py", 12, 15));
        // One line of slack at each end: models cite the signature of a body
        // they were shown, and rejecting that would flag honest answers.
        assert!(session.covers("a.py", 9, 21));
        assert!(!session.covers("a.py", 40, 45));
        assert!(!session.covers("b.py", 12, 15));
    }

    #[test]
    fn malformed_tool_arguments_do_not_abort_the_turn() {
        let graph = Graph::open_in_memory("/tmp/x").unwrap();
        let mut session = Session::default();

        for arguments in ["", "{", "null", "\"just a string\""] {
            let result = dispatch(&graph, &mut session, "search_symbols", arguments);
            assert!(
                result.contains("treffer") || result.contains("fehler"),
                "got {result} for {arguments:?}"
            );
        }
    }

    #[test]
    fn a_fruitless_search_comes_back_with_real_names_to_try() {
        // Without this the model searches for synonyms of a word that was never
        // in the code, one slow round trip at a time.
        let graph = Graph::open_in_memory("/tmp/x").unwrap();
        let mut session = Session::default();
        let result = dispatch(&graph, &mut session, "search_symbols", r#"{"query":"Endbetrag"}"#);

        assert!(result.contains("hinweis"), "an empty result must redirect: {result}");
        assert!(result.contains("search_text"));
    }

    #[test]
    fn an_unknown_tool_reports_back_instead_of_failing() {
        let graph = Graph::open_in_memory("/tmp/x").unwrap();
        let mut session = Session::default();
        let result = dispatch(&graph, &mut session, "delete_everything", "{}");
        assert!(result.contains("Unbekanntes Werkzeug"));
    }

    #[test]
    fn every_tool_spec_declares_an_object_schema() {
        // A spec whose parameters are not an object is rejected outright by
        // several local runners, and the failure looks like the model being bad.
        for spec in specs() {
            assert_eq!(
                spec.function.parameters.get("type").and_then(Value::as_str),
                Some("object"),
                "{} has a malformed schema",
                spec.function.name
            );
            assert!(!spec.function.description.is_empty());
        }
    }
}
