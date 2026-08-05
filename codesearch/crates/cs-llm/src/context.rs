//! Deterministic retrieval, before the model is asked anything.
//!
//! The first version of the assistant was a plain tool loop: ask the model, run
//! the tool it wants, ask again. It navigated correctly — but measured against
//! Ollama on CPU it needed five round trips, and each round trip reprocesses the
//! whole grown prompt. Twenty-five minutes for one question.
//!
//! The fix is not a faster model, it is fewer calls. Everything the loop
//! discovered in its first four steps is something the graph could have been
//! asked directly: pull the likely symbols, their facts, their callers and their
//! source *before* the model sees the question, and the usual case becomes one
//! call instead of five. The tools stay available for the cases where the guess
//! was wrong, but they are the exception rather than the mechanism.
//!
//! This is also what makes a small model work at all. Retrieval is deterministic
//! and always right; the model only has to read and summarise.

use crate::tools::{ShownSpan, Session};
use cs_core::{ContentHash, EdgeKind, NodeKind};
use cs_graph::{Direction, Graph, SpanRead};
use std::collections::HashSet;

/// Symbols pulled in before the model runs. Enough to answer most questions,
/// small enough that prompt processing stays affordable.
const MAX_SYMBOLS: usize = 6;

/// Symbols pulled in one hop out from the matches — what they call and read.
/// Enough to answer "why does this value come out wrong" without a round trip.
const MAX_DEPENDENCIES: usize = 4;

/// Source lines per symbol. Past this a definition is summarised by its facts
/// and the model can call `read_lines` if it really needs the body.
const MAX_LINES: usize = 45;

/// Words too generic to be worth searching for. Not a stopword list for German —
/// these are the words that appear in *questions about code* and match nothing
/// useful.
const NOISE: &[&str] = &[
    "wie", "was", "wo", "warum", "wieso", "wer", "wann", "welche", "welcher", "welches", "wird",
    "werden", "ist", "sind", "war", "das", "der", "die", "den", "dem", "des", "ein", "eine",
    "einen", "einem", "eines", "und", "oder", "aber", "nicht", "kein", "keine", "mit", "ohne",
    "von", "vom", "zum", "zur", "für", "auf", "aus", "bei", "nach", "über", "unter", "durch",
    "kommt", "geht", "macht", "passiert", "funktioniert", "sich", "man", "hier", "dort", "dann",
    "the", "what", "where", "why", "how", "does", "did", "this", "that", "and", "for", "with",
    "from", "into", "code", "datei", "file", "funktion", "function", "klasse", "class", "methode",
];

/// German question words that map onto English identifiers.
///
/// The same kind of list as [`NOISE`] and justified the same way: it is not a
/// dictionary, it is the handful of stems that appear in *questions about code*
/// written in German about a codebase written in English. "Wo werden die
/// Passwörter verschlüsselt?" must reach `hash_password`, and no amount of
/// trigram matching gets from `passwörter` to `password`.
///
/// Prefix-matched, so `verschlüsselt`/`verschlüsselung` both hit `verschlüssel`.
/// Deliberately not an embedding index: this is offline, deterministic, and a
/// reader can see exactly why a symbol was found.
const GERMAN_TO_CODE: &[(&str, &[&str])] = &[
    ("passwor", &["password", "passwd", "credential"]),
    ("passwör", &["password", "passwd", "credential"]),
    ("verschlüssel", &["encrypt", "crypt", "cipher", "hash"]),
    ("entschlüssel", &["decrypt", "crypt", "cipher"]),
    ("anmeld", &["login", "signin", "auth"]),
    ("abmeld", &["logout", "signout"]),
    ("berechtig", &["permission", "authorize", "acl", "role"]),
    ("nutzer", &["user", "account"]),
    ("benutzer", &["user", "account"]),
    ("speicher", &["save", "store", "persist", "write"]),
    ("laden", &["load", "read", "fetch"]),
    ("lesen", &["read", "load"]),
    ("schreib", &["write", "save"]),
    ("lösch", &["delete", "remove", "drop"]),
    ("suche", &["search", "query", "find"]),
    ("such", &["search", "query", "find"]),
    ("fehler", &["error", "exception", "fail"]),
    ("ausnahme", &["exception", "error"]),
    ("einstellung", &["config", "setting", "option"]),
    ("konfigur", &["config", "setting"]),
    ("datenbank", &["database", "db", "sql", "query"]),
    ("abfrage", &["query", "select", "fetch"]),
    ("antwort", &["response", "answer", "reply"]),
    ("anfrage", &["request", "query"]),
    ("prüf", &["check", "validate", "verify"]),
    ("gültig", &["valid", "validate"]),
    ("zeit", &["time", "timestamp", "date"]),
    ("datum", &["date", "timestamp"]),
    ("hochlad", &["upload"]),
    ("herunterlad", &["download", "fetch"]),
    ("senden", &["send", "post", "emit"]),
    ("empfang", &["receive", "handle", "consume"]),
    ("verbind", &["connect", "connection", "session"]),
    ("schlüssel", &["key", "token", "secret"]),
    ("geheim", &["secret", "token", "credential"]),
    ("sitzung", &["session", "token"]),
    ("zwischenspeicher", &["cache", "buffer"]),
    ("warteschlange", &["queue", "job", "task"]),
    ("auftrag", &["job", "task", "batch"]),
    ("protokoll", &["log", "logger", "audit"]),
    ("zähl", &["count", "counter", "total"]),
    ("übersetz", &["translate", "locale", "i18n"]),
    ("bild", &["image", "picture", "thumbnail"]),
    ("datei", &["file", "path", "document"]),
    ("ordner", &["directory", "folder", "path"]),
];

/// Extra search terms derived from a German question.
///
/// Separate from [`candidate_terms`] so the mapping is testable on its own and
/// so a caller can see which terms were added rather than only their effect.
pub fn code_terms_for(question: &str) -> Vec<String> {
    let lowered = question.to_lowercase();
    let mut out: Vec<String> = Vec::new();
    for (stem, targets) in GERMAN_TO_CODE {
        if !lowered.contains(stem) {
            continue;
        }
        for target in *targets {
            let term = (*target).to_string();
            if !out.contains(&term) {
                out.push(term);
            }
        }
    }
    out
}

/// What deterministic retrieval produced, and how well it went.
pub struct Retrieval {
    pub context: String,
    /// What citation verification later checks against, so retrieval and
    /// permission-to-quote stay the same act.
    pub session: Session,
    /// True when a symbol name actually matched something in the question.
    ///
    /// This, not the length of the context, is what says whether retrieval
    /// answered the question. A complete context for a small project is a few
    /// hundred characters; a byte threshold would call that "thin" and hand a
    /// slow model tools it does not need.
    pub matched_by_name: bool,
    pub symbols: usize,
    /// The symbols that ended up in the context, best first.
    ///
    /// The caller needs the ids, not only the rendered text: "show me every
    /// function belonging to this feature" is answered with a list and a map,
    /// and both need something to point at.
    pub symbol_ids: Vec<cs_core::NodeId>,
}

/// Builds the context block and records everything it shows the model.
///
/// The narrow form: one question, one answer, six symbols. Unchanged so
/// `/ask` and `/explain` keep behaving exactly as measured.
pub fn build(graph: &Graph, question: &str) -> Retrieval {
    build_with(graph, question, &[], MAX_SYMBOLS, false)
}

/// Retrieval with the knobs exposed.
///
/// `broad` is the difference between a point question and a *feature* question.
/// In the narrow form full-text search only runs when name search found
/// literally nothing (`candidates.is_empty()` below) — so a single incidental
/// name hit suppresses it entirely, and the function whose *comment* says
/// "encrypt the password" is never found. Broad retrieval always runs both and
/// unions them, which costs one more query and answers a question the narrow
/// form silently could not.
pub fn build_with(
    graph: &Graph,
    question: &str,
    extra_terms: &[String],
    max_symbols: usize,
    broad: bool,
) -> Retrieval {
    let mut session = Session::default();
    let mut sections: Vec<String> = Vec::new();
    let mut seen: HashSet<cs_core::NodeId> = HashSet::new();

    let mut terms = candidate_terms(question);
    for extra in extra_terms {
        let extra = extra.trim().to_lowercase();
        if !extra.is_empty() && !terms.contains(&extra) {
            terms.push(extra);
        }
    }

    // Symbols whose names match something in the question, best first.
    let per_term = if broad { 8 } else { 4 };
    let mut candidates = Vec::new();
    for term in &terms {
        let Ok(hits) = graph.search_symbols(term, &[], per_term) else { continue };
        for hit in hits {
            if seen.insert(hit.id) {
                candidates.push(hit);
            }
        }
    }

    // Whether a name matched is recorded before full-text runs: it is the signal
    // for "did retrieval actually answer the question", and a text hit is a
    // weaker answer than a name hit even when it is the only one.
    let matched_by_name = !candidates.is_empty();

    // Full-text, which catches questions phrased in the words of comments rather
    // than identifiers. Narrow retrieval only reaches here when name search found
    // nothing at all; broad retrieval always unions the two.
    if broad || candidates.is_empty() {
        for term in terms.iter().filter(|t| t.len() >= 3) {
            let Ok(hits) = graph.search_text(term, 6) else { continue };
            for hit in hits {
                let Some(id) = hit.in_symbol else { continue };
                if !seen.insert(id) {
                    continue;
                }
                if let Ok(Some(detail)) = graph.node(id) {
                    candidates.push(cs_graph::SymbolHit {
                        id: detail.id,
                        name: detail.name,
                        qualified: detail.qualified,
                        kind: detail.kind,
                        language: detail.language,
                        path: detail.path,
                        line: detail.span.start_line,
                        relevance: detail.metrics.relevance,
                    });
                }
            }
        }
    }

    // Still nothing: give the model the shape of the project rather than an
    // empty context it would have to guess its way out of. An index with nothing
    // in it gets no context at all — inventing a heading over zero symbols would
    // be worse than saying nothing.
    if candidates.is_empty() {
        candidates = graph
            .top_by_relevance(max_symbols, &[NodeKind::Function, NodeKind::Method, NodeKind::Class])
            .unwrap_or_default();

        if !candidates.is_empty() {
            sections.push(
                "Kein Symbol passt direkt zur Frage. Hier sind stattdessen die wichtigsten \
                 Stellen des Projekts."
                    .to_string(),
            );
        }
    }

    candidates.sort_by(|a, b| b.relevance.partial_cmp(&a.relevance).unwrap_or(std::cmp::Ordering::Equal));
    candidates.truncate(max_symbols);

    for hit in &candidates {
        if let Some(section) = render_symbol(graph, &mut session, hit.id) {
            sections.push(section);
        }
    }

    // What the retrieved symbols depend on, one hop out.
    //
    // Measured: asked why a total was a cent out, the assistant spent a slow
    // round trip looking up the constant the function it was already reading
    // depends on. A dependency the graph already knows about should never cost a
    // model call to discover.
    let mut dependencies = Vec::new();
    for hit in &candidates {
        let Ok(neighbours) =
            graph.neighbours(hit.id, Direction::Out, &[EdgeKind::Calls, EdgeKind::Reads])
        else {
            continue;
        };
        for neighbour in neighbours.into_iter().take(5) {
            if !seen.insert(neighbour.node.id) {
                continue;
            }
            if let Some(section) = render_symbol(graph, &mut session, neighbour.node.id) {
                dependencies.push(section);
            }
            if dependencies.len() >= MAX_DEPENDENCIES {
                break;
            }
        }
        if dependencies.len() >= MAX_DEPENDENCIES {
            break;
        }
    }
    sections.extend(dependencies);

    if sections.is_empty() {
        return Retrieval {
            context: String::new(),
            session,
            matched_by_name: false,
            symbols: 0,
            symbol_ids: Vec::new(),
        };
    }

    let context = format!(
        "Aus dem Graphen dieses Projekts nachgeschlagen. Nur diese Zeilen darfst du zitieren; \
         für mehr nutze read_lines.\n\n{}",
        sections.join("\n\n")
    );
    Retrieval {
        context,
        session,
        matched_by_name,
        symbols: candidates.len(),
        symbol_ids: candidates.iter().map(|hit| hit.id).collect(),
    }
}

fn render_symbol(graph: &Graph, session: &mut Session, id: cs_core::NodeId) -> Option<String> {
    let detail = graph.node(id).ok().flatten()?;
    let mut out = String::new();

    out.push_str(&format!(
        "## {} ({}) — {}:{}\n",
        detail.qualified,
        detail.kind.as_str(),
        detail.path,
        detail.span.start_line
    ));

    if let Some(doc) = &detail.doc {
        let first: Vec<&str> = doc.lines().take(4).collect();
        out.push_str(&format!("{}\n", first.join(" ")));
    }

    if let Some(facts) = &detail.facts {
        let effects: Vec<&str> = facts.side_effects.iter().map(|e| e.label()).collect();
        out.push_str(&format!(
            "Fakten: {} Parameter · Komplexität {} · {} Zeilen{}\n",
            facts.params.len(),
            facts.complexity,
            facts.loc,
            if effects.is_empty() { String::new() } else { format!(" · {}", effects.join(", ")) }
        ));
    }

    for (label, direction) in
        [("Aufrufer", Direction::In), ("Ruft auf", Direction::Out)]
    {
        let Ok(neighbours) = graph.neighbours(id, direction, &[EdgeKind::Calls]) else { continue };
        if neighbours.is_empty() {
            continue;
        }
        let rendered: Vec<String> = neighbours
            .iter()
            .take(6)
            .map(|n| {
                format!(
                    "{} [{}] {}:{}",
                    n.node.qualified,
                    n.confidence.label(),
                    n.evidence_path,
                    n.evidence_line
                )
            })
            .collect();
        out.push_str(&format!("{label}: {}\n", rendered.join(" · ")));
    }

    // The source, capped — and recorded, which is what licenses quoting it.
    if let Ok(SpanRead::Text { text, .. }) =
        graph.read_span(cs_core::FileId::of_path(&detail.path), detail.span)
    {
        let lines: Vec<&str> = text.lines().collect();
        let shown = lines.len().min(MAX_LINES);
        let numbered: Vec<String> = lines[..shown]
            .iter()
            .enumerate()
            .map(|(offset, line)| {
                format!("{:>5} | {line}", detail.span.start_line as usize + offset)
            })
            .collect();

        session.record(ShownSpan {
            path: detail.path.clone(),
            from_line: detail.span.start_line,
            to_line: detail.span.start_line + shown as u32,
            content_hash: ContentHash::of(text.as_bytes()),
        });

        out.push_str(&format!("```\n{}\n```", numbered.join("\n")));
        if lines.len() > shown {
            out.push_str(&format!("\n… {} weitere Zeilen", lines.len() - shown));
        }
    }

    Some(out)
}

/// Pulls plausible identifier names out of a question.
///
/// Splits on non-word characters and then on camelCase and snake_case, because
/// someone asking about `applyDiscount` and someone asking about "apply discount"
/// mean the same thing and only one of them matches the index directly.
fn candidate_terms(question: &str) -> Vec<String> {
    let mut terms: Vec<String> = Vec::new();
    let mut push = |term: String| {
        let lowered = term.to_lowercase();
        if term.len() >= 3 && !NOISE.contains(&lowered.as_str()) && !terms.contains(&term) {
            terms.push(term);
        }
    };

    for raw in question.split(|c: char| !c.is_alphanumeric() && c != '_') {
        if raw.is_empty() {
            continue;
        }
        push(raw.to_string());

        if raw.contains('_') {
            for part in raw.split('_') {
                push(part.to_string());
            }
        }

        // camelCase → its words.
        if raw.chars().any(char::is_uppercase) && raw.chars().any(char::is_lowercase) {
            let mut current = String::new();
            for character in raw.chars() {
                if character.is_uppercase() && !current.is_empty() {
                    push(std::mem::take(&mut current));
                }
                current.push(character);
            }
            push(current);
        }
    }

    terms
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn question_words_are_not_searched_for() {
        let terms = candidate_terms("Wie wird der Endbetrag in total berechnet?");
        assert!(!terms.iter().any(|t| t.eq_ignore_ascii_case("wie")));
        assert!(!terms.iter().any(|t| t.eq_ignore_ascii_case("wird")));
        assert!(terms.iter().any(|t| t == "total"));
    }

    #[test]
    fn compound_identifiers_are_split_both_ways() {
        let snake = candidate_terms("was macht apply_discount");
        assert!(snake.contains(&"apply_discount".to_string()));
        assert!(snake.contains(&"apply".to_string()));
        assert!(snake.contains(&"discount".to_string()));

        let camel = candidate_terms("was macht applyDiscount");
        assert!(camel.contains(&"applyDiscount".to_string()));
        assert!(camel.contains(&"apply".to_string()));
        assert!(camel.contains(&"Discount".to_string()));
    }

    #[test]
    fn an_empty_graph_yields_empty_context_rather_than_nonsense() {
        let graph = Graph::open_in_memory("/tmp/nowhere").unwrap();
        let retrieval = build(&graph, "Wie wird der Betrag berechnet?");
        assert!(retrieval.context.is_empty());
        assert!(retrieval.session.shown().is_empty());
        assert!(!retrieval.matched_by_name);
        assert_eq!(retrieval.symbols, 0);
    }
}
