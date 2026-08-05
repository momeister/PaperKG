//! The assistant that navigates the graph.
//!
//! It cannot read the repository. It has the tools in [`tools`] and nothing else,
//! and every answer it produces goes through [`citation::verify`] before anyone
//! sees it. Those two facts together are what separate this from a chat window
//! with a codebase pasted into it: the model's job is to *navigate and
//! summarise*, and retrieval — which is deterministic — does the part that has to
//! be right.
//!
//! The output is a **Spur**: an ordered chain of cited hops, each with one line
//! of reasoning. A paragraph of prose about a bug is hard to check. A chain of
//! four file positions is something you can walk yourself in a minute.

pub mod citation;
pub mod context;
pub mod provider;
pub mod tools;

use anyhow::{bail, Result};
use cs_graph::Graph;
use provider::{Message, Provider, Role};
use serde::Serialize;

/// How many extra tool rounds after the pre-retrieved context.
///
/// Low on purpose. Deterministic retrieval already put the likely symbols, their
/// callers and their source in front of the model, so the usual case is one call
/// and no tools at all. Each further round reprocesses the whole grown prompt —
/// on CPU inference that is minutes, which is what made the original eight-round
/// loop unusable.
const MAX_ROUNDS: usize = 3;

/// Marks the trail block at the end of an answer.
const TRAIL_MARKER: &str = "SPUR:";

const SYSTEM_PROMPT: &str = r#"Du hilfst dabei, eine fremde Codebase zu verstehen. Du beantwortest die Frage: warum funktioniert das so, wie es funktioniert?

Du kennst diesen Code nicht. Was du siehst, ist der nachgeschlagene Ausschnitt oben plus das, was du über deine Werkzeuge holst. Alles andere weißt du nicht — auch dann nicht, wenn du das Framework zu kennen glaubst.

Meistens steht die Antwort schon im nachgeschlagenen Ausschnitt. Lies ihn erst und antworte daraus. Stehen dir Werkzeuge zur Verfügung, greif nur dann dazu, wenn dort wirklich etwas fehlt — und niemals zweimal zum selben Aufruf.

Feste Regeln:

1. Belege jede Aussage über den Code mit `pfad/datei.py:zeile` oder `pfad/datei.py:von-bis`. Zitiere nur Zeilen, die du über `get_node` oder `read_lines` tatsächlich bekommen hast.
   - Richtig: `pricing.py:14` für das, was in Zeile 14 steht.
   - Falsch: `pricing.py:99` obwohl du die Zeile nie gesehen hast.
   Erfundene Belege werden maschinell erkannt und der Nutzerin als unbelegt angezeigt.

2. Jede Beziehung, die dir ein Werkzeug liefert, trägt ein Feld `sicherheit`:
   - "verifiziert" oder "aufgelöst" → du darfst es als Tatsache formulieren.
   - "vermutet" → du musst hedgen. Schreibe "vermutlich", "wahrscheinlich" oder "einer von N Kandidaten". Formuliere es nie als feststehend.

3. Stößt du auf einen Knoten der Art `dynamic_gap`, sag klar, dass die statische Analyse dort endet und das Ziel erst zur Laufzeit feststeht. Rate nicht weiter.

4. Findest du etwas nicht, sag das. "Ich habe dafür im Projekt nichts gefunden" ist eine gute Antwort. Eine erfundene ist keine.

5. Zitiere Code wörtlich oder gar nicht. Bereinige nichts, kürze nichts stillschweigend.

Arbeitsweise:

- Der nachgeschlagene Ausschnitt wurde schon mit englischen Begriffen gesucht — deutsche Frage, englische Bezeichner. Suche nach `total`, nicht nach „Endbetrag"; nach `discount`, nicht nach „Rabatt".
- Rate nicht dreimal mit Synonymen. Findet eine Suche nichts, nimm `most_relevant` und sieh nach, wie die Dinge hier tatsächlich heißen.
- Beginne die Antwort nicht mit einer Wiederholung der Frage — das kostet Tokens, die dann in der Spur fehlen.

Antworte kurz — vier bis acht Sätze — und schließe mit einem Spur-Block ab:

SPUR:
pfad/datei.py:31 — was hier passiert, in einem Satz
pfad/andere.py:88 — was hier passiert, in einem Satz

Die Spur ist der Weg durch den Code, den man abgehen muss, um die Antwort selbst nachzuvollziehen. Zwei bis sechs Schritte, in der Reihenfolge, in der die Daten fließen. Lieber zwei belegte als sechs geratene — jeder Schritt ohne `datei:zeile` ist kein Schritt."#;

#[derive(Debug, Clone, Serialize)]
pub struct TrailStep {
    /// Resolved to a symbol where possible, so clicking a step moves every view.
    pub node_id: Option<String>,
    pub path: String,
    pub line: u32,
    pub reason: String,
    /// False when the cited position could not be verified.
    pub verified: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct Answer {
    pub verified: citation::VerifiedAnswer,
    pub trail: Vec<TrailStep>,
    /// How many tool calls it took. Shown in the UI: an answer built from twelve
    /// lookups is a different kind of answer than one built from one.
    pub tool_calls: usize,
    /// True when the loop was cut short rather than the model finishing.
    pub truncated: bool,
}

pub struct Assistant {
    pub provider: Provider,
    pub model: String,
}

impl Assistant {
    pub fn new(provider: Provider, model: impl Into<String>) -> Self {
        Self { provider, model: model.into() }
    }

    /// Runs one question to completion.
    ///
    /// `on_activity` is called with a short description before each tool call, so
    /// the UI can show what the assistant is doing rather than a spinner —
    /// watching it look things up is most of what makes the answer trustworthy.
    pub fn ask(
        &self,
        graph: &Graph,
        question: &str,
        on_activity: &dyn Fn(&str),
    ) -> Result<Answer> {
        let specs = tools::specs();

        // Retrieval first, deterministically. What the model is handed here is
        // what the tool loop used to spend four slow round trips discovering.
        let context::Retrieval { context, mut session, matched_by_name, symbols, .. } =
            context::build(graph, question);
        if !context.is_empty() {
            on_activity("schlage im Graphen nach");
        }

        let mut messages = vec![Message::system(SYSTEM_PROMPT)];
        if context.is_empty() {
            messages.push(Message::user(question));
        } else {
            messages.push(Message::user(format!("{context}\n\n---\n\nFrage: {question}")));
        }
        // Tools are offered only when retrieval came back thin.
        //
        // This is the conclusion of measuring, not a preference. Given both a
        // complete context *and* tools, a small local model looks things up
        // anyway — nine calls for one question, four of them exact repeats of
        // earlier ones, at minutes apiece. With a good context and no tools it
        // answers in a single call from material that is already correct.
        //
        // When retrieval finds little, the tools are the only way forward and
        // are offered despite the cost.
        let offer_tools = should_offer_tools(matched_by_name, symbols);
        let tool_specs = offer_tools.then_some(specs.as_slice());
        let rounds = if offer_tools { MAX_ROUNDS } else { 1 };

        let mut tool_calls = 0usize;
        let mut truncated = true;
        let mut final_text = String::new();

        for _ in 0..rounds {
            let (content, calls) =
                provider::chat(&self.provider, &self.model, &messages, tool_specs)?;

            if calls.is_empty() {
                final_text = content.unwrap_or_default();
                truncated = false;
                break;
            }

            messages.push(Message {
                role: Role::Assistant,
                content: content.clone(),
                tool_calls: Some(calls.clone()),
                tool_call_id: None,
            });

            for call in &calls {
                tool_calls += 1;
                on_activity(&describe(&call.function.name, &call.function.arguments));
                let result =
                    tools::dispatch(graph, &mut session, &call.function.name, &call.function.arguments);
                messages.push(Message::tool_result(&call.id, result));
            }
        }

        if final_text.is_empty() && truncated && offer_tools {
            // The loop ran out. Ask once more without tools so whatever was
            // gathered still produces an answer instead of nothing.
            on_activity("fasse zusammen");
            messages.push(Message::user(
                "Beantworte die Frage jetzt mit dem, was du gefunden hast. Keine weiteren \
                 Werkzeugaufrufe. Sag klar, was offen geblieben ist.",
            ));
            let (content, _) = provider::chat(&self.provider, &self.model, &messages, None)?;
            final_text = content.unwrap_or_default();
        }

        if final_text.trim().is_empty() {
            bail!("Das Modell hat keine Antwort geliefert");
        }

        let (prose, trail_block) = split_trail(&final_text);
        let verified = citation::verify(graph, &session, &prose);
        let trail = parse_trail(graph, &session, trail_block);

        Ok(Answer { verified, trail, tool_calls, truncated })
    }
}

/// Whether the tools are worth their cost for this question.
///
/// The signal is whether a symbol *name* matched the question — not how long the
/// context turned out. A complete context for a small project is only a few
/// hundred characters, and a length threshold called that "thin" and handed a
/// slow model tools it did not need.
fn should_offer_tools(matched_by_name: bool, symbols: usize) -> bool {
    !matched_by_name || symbols == 0
}

/// A short, human-readable description of a tool call for the activity line.
fn describe(name: &str, arguments: &str) -> String {
    let args: serde_json::Value = serde_json::from_str(arguments).unwrap_or(serde_json::Value::Null);
    let query = args.get("query").and_then(|v| v.as_str()).unwrap_or("");
    let path = args.get("path").and_then(|v| v.as_str()).unwrap_or("");

    match name {
        "search_symbols" => format!("suche Symbol „{query}“"),
        "search_text" => format!("suche Text „{query}“"),
        "get_node" => "sehe mir ein Symbol an".to_string(),
        "callers_of" => "sehe nach, wer das aufruft".to_string(),
        "callees_of" => "sehe nach, was das aufruft".to_string(),
        "path_between" => "suche den Pfad dazwischen".to_string(),
        "read_lines" => format!("lese {path}"),
        "most_relevant" => "sehe mir die wichtigsten Stellen an".to_string(),
        other => format!("rufe {other} auf"),
    }
}

/// Splits the prose from the trail block.
fn split_trail(text: &str) -> (String, &str) {
    match text.find(TRAIL_MARKER) {
        Some(index) => (
            text[..index].trim_end().to_string(),
            text[index + TRAIL_MARKER.len()..].trim_start(),
        ),
        None => (text.trim().to_string(), ""),
    }
}

/// Turns `pfad:zeile — Begründung` lines into steps bound to graph nodes.
///
/// A step that does not check out is kept and marked rather than dropped:
/// silently shortening the trail would hide that the assistant pointed somewhere
/// it never actually looked.
fn parse_trail(graph: &Graph, session: &tools::Session, block: &str) -> Vec<TrailStep> {
    let mut steps = Vec::new();

    for line in block.lines() {
        let line = line.trim().trim_start_matches(['-', '*', '•']).trim();
        if line.is_empty() {
            continue;
        }

        // Both an em dash and a plain hyphen appear in practice.
        let (position, reason) = match line.split_once('—').or_else(|| line.split_once(" - ")) {
            Some((position, reason)) => (position.trim(), reason.trim()),
            None => (line, ""),
        };

        let Some((path, line_text)) = position.rsplit_once(':') else { continue };
        let Ok(line_number) = line_text.trim().split('-').next().unwrap_or("").parse::<u32>() else {
            continue;
        };

        let path = path.trim();
        let file = cs_core::FileId::of_path(path);
        let node_id = graph.symbol_at(file, line_number).ok().flatten();

        // A trail step is a citation like any other, and gets the same test:
        // the file must exist *and* these lines must have been retrieved. A step
        // pointing at code the model never saw is a guess dressed as a route.
        let verified = std::fs::metadata(graph.root().join(path)).is_ok()
            && session.covers(path, line_number, line_number);

        steps.push(TrailStep {
            node_id: node_id.map(|id| id.to_string()),
            path: path.to_string(),
            line: line_number,
            reason: reason.to_string(),
            verified,
        });
    }

    steps
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_trail_block_is_separated_from_the_prose() {
        let (prose, trail) = split_trail("Der Betrag wird gerundet.\n\nSPUR:\na.py:1 — hier rein");
        assert_eq!(prose, "Der Betrag wird gerundet.");
        assert!(trail.starts_with("a.py:1"));
    }

    #[test]
    fn an_answer_without_a_trail_still_yields_its_prose() {
        let (prose, trail) = split_trail("Dazu habe ich nichts gefunden.");
        assert_eq!(prose, "Dazu habe ich nichts gefunden.");
        assert!(trail.is_empty());
    }

    #[test]
    fn trail_lines_parse_with_either_dash() {
        let graph = Graph::open_in_memory("/tmp/nowhere").unwrap();
        let steps = parse_trail(
            &graph,
            &tools::Session::default(),
            "src/a.py:31 — hier kommt der Wert rein\n- src/b.py:88 - hier wird gerundet\n",
        );

        assert_eq!(steps.len(), 2);
        assert_eq!(steps[0].path, "src/a.py");
        assert_eq!(steps[0].line, 31);
        assert_eq!(steps[0].reason, "hier kommt der Wert rein");
        assert_eq!(steps[1].line, 88);
        // Neither file exists and nothing was retrieved, so both must be flagged
        // rather than dropped.
        assert!(steps.iter().all(|step| !step.verified));
    }

    #[test]
    fn a_malformed_trail_line_is_skipped_without_taking_the_rest_with_it() {
        let graph = Graph::open_in_memory("/tmp/nowhere").unwrap();
        let steps = parse_trail(
            &graph,
            &tools::Session::default(),
            "irgendwas ohne Position\nsrc/a.py:12 — passt\n",
        );
        assert_eq!(steps.len(), 1);
        assert_eq!(steps[0].line, 12);
    }

    #[test]
    fn tools_are_withheld_once_retrieval_has_answered_the_question() {
        // The rule that makes a small local model usable at all. Measured: a
        // complete context *plus* tools produced nine calls for one question,
        // four of them exact repeats of earlier ones, at minutes apiece. A
        // complete context alone produces one call from correct material.
        //
        // Asserted against a real index rather than against string lengths,
        // because what matters is that a normal question on a normal repository
        // actually clears the threshold.
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("src")).unwrap();
        std::fs::write(
            dir.path().join("src/pricing.py"),
            "TAX = 0.19\n\n\ndef apply_discount(amount, pct):\n\
             \x20   \"\"\"Zieht einen Rabatt ab.\"\"\"\n\
             \x20   return amount * (1 - pct)\n\n\ndef total(amount, pct):\n\
             \x20   net = apply_discount(amount, pct)\n\
             \x20   return round(net * (1 + TAX), 2)\n",
        )
        .unwrap();

        let mut workspace = cs_workspace::Workspace::open(dir.path()).unwrap();
        workspace.index(&|_| {}).unwrap();

        let hit = context::build(workspace.graph(), "Wie wird total berechnet?");
        assert!(hit.matched_by_name, "`total` is a symbol in this project");
        assert!(
            !should_offer_tools(hit.matched_by_name, hit.symbols),
            "a question retrieval answered must not pay for tools"
        );

        // A question whose words match no symbol falls back to full-text and
        // then to the project's most relevant symbols — retrieval did not answer
        // it, so the tools are the only way forward.
        let miss = context::build(workspace.graph(), "Wie läuft das Deployment?");
        assert!(!miss.matched_by_name);
        assert!(should_offer_tools(miss.matched_by_name, miss.symbols));
    }

    #[test]
    fn activity_descriptions_name_what_is_being_looked_up() {
        assert_eq!(describe("search_symbols", r#"{"query":"total"}"#), "suche Symbol „total“");
        assert_eq!(describe("read_lines", r#"{"path":"a.py"}"#), "lese a.py");
        // Malformed arguments must not panic the activity line.
        assert!(describe("search_symbols", "{").contains("suche Symbol"));
    }
}
