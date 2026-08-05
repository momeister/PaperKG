//! A headless front end for the index.
//!
//! Exists so the analysis can be exercised, benchmarked and scripted without the
//! desktop app in the way — and so a broken index can be diagnosed from a
//! terminal rather than through a UI that is itself reading the broken index.
//!
//!   cs index <pfad>              build or refresh the index
//!   cs find  <pfad> <begriff>    search symbols
//!   cs show  <pfad> <begriff>    facts, callers and callees of the best match
//!   cs top   <pfad>              the most relevant symbols
//!   cs stats <pfad>              index health, including how much is guesswork
//!   cs ask   <pfad> "<frage>"    ask the assistant, with citation checking
//!   cs serve <pfad>              JSON line protocol on stdin/stdout, for embedders
//!
//! Every command takes an optional `--db <pfad>` to point at an index outside the
//! workspace. An embedder that did not write the repository has no business
//! leaving a `.codesearch/` directory in it, and being able to name the file is
//! also what makes such an index inspectable from a terminal.

use anyhow::{bail, Result};
use cs_core::{EdgeKind, NodeKind};
use cs_graph::Direction;
use cs_workspace::{Phase, Workspace};
use std::path::PathBuf;

fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "warn".into()),
        )
        .with_target(false)
        .init();

    let args: Vec<String> = std::env::args().skip(1).collect();
    let (command, root, rest) = match args.as_slice() {
        [command, root, rest @ ..] => (command.as_str(), root.clone(), rest.to_vec()),
        _ => {
            eprintln!("usage: cs <index|find|show|top|stats|ask|serve> <pfad> [begriff] [--db <pfad>]");
            std::process::exit(2);
        }
    };

    let (db_path, rest) = take_db_flag(rest);

    // `serve` owns stdout for the protocol and runs its own loop, so it takes
    // over before anything here prints a word.
    if command == "serve" {
        return cs_workspace::serve::run(std::path::Path::new(&root), db_path.as_deref());
    }

    let mut workspace = match &db_path {
        Some(path) => Workspace::open_with_db(&root, path)?,
        None => Workspace::open(&root)?,
    };

    match command {
        "index" => {
            let report = workspace.index(&|progress| {
                if progress.phase == Phase::Parsing && progress.total > 0 {
                    eprint!(
                        "\r{}: {}/{}    ",
                        progress.phase.label(),
                        progress.done,
                        progress.total
                    );
                } else {
                    eprint!("\r{}...                    ", progress.phase.label());
                }
            })?;
            eprintln!();

            println!("{} Dateien gesehen", report.files_seen);
            println!("{} geparst, {} wiederverwendet, {} entfernt",
                report.files_parsed, report.files_reused, report.files_removed);
            if !report.skipped.is_empty() {
                let mut reasons: Vec<_> = report.skipped.iter().collect();
                reasons.sort_by_key(|(_, count)| std::cmp::Reverse(**count));
                let summary: Vec<String> =
                    reasons.iter().map(|(reason, count)| format!("{count}× {reason}")).collect();
                println!("übersprungen: {}", summary.join(", "));
            }
            println!(
                "{} Commits ausgewertet{}",
                report.commits_walked,
                if report.history_truncated { " (Fenster erreicht)" } else { "" }
            );
            println!("in {} ms", report.duration_ms);
            print_stats(&workspace)?;
        }

        "find" => {
            let Some(query) = rest.first() else { bail!("kein Suchbegriff angegeben") };
            for hit in workspace.graph().search_symbols(query, &[], 25)? {
                println!(
                    "{:>5.0}%  {:<10} {:<40} {}:{}",
                    hit.relevance * 100.0,
                    hit.kind.as_str(),
                    hit.qualified,
                    hit.path,
                    hit.line
                );
            }
        }

        "show" => {
            let Some(query) = rest.first() else { bail!("kein Suchbegriff angegeben") };
            let hits = workspace.graph().search_symbols(query, &[], 1)?;
            let Some(hit) = hits.first() else { bail!("nichts gefunden für {query}") };

            let detail = workspace
                .graph()
                .node(hit.id)?
                .ok_or_else(|| anyhow::anyhow!("Knoten verschwunden"))?;

            println!("{} · {}", detail.qualified, detail.kind.as_str());
            println!("{}:{}", detail.path, detail.span.start_line);
            if let Some(doc) = &detail.doc {
                println!("\n  {doc}");
            }
            if let Some(facts) = &detail.facts {
                println!("\n  {}", facts.headline());
                println!("  Komplexität {} · {} Zeilen · Tiefe {}",
                    facts.complexity, facts.loc, facts.max_nesting);
            }
            println!(
                "\n  Relevanz {:.0}%  (PageRank {:.5}, {} Aufrufer, {} Änderungen, {} Fixes)",
                detail.metrics.relevance * 100.0,
                detail.metrics.pagerank,
                detail.metrics.fan_in,
                detail.metrics.churn,
                detail.metrics.risk
            );

            for (label, direction) in
                [("wird aufgerufen von", Direction::In), ("ruft auf", Direction::Out)]
            {
                let neighbours =
                    workspace.graph().neighbours(hit.id, direction, &[EdgeKind::Calls])?;
                if neighbours.is_empty() {
                    continue;
                }
                println!("\n  {label}:");
                for neighbour in neighbours.iter().take(15) {
                    let marker = match neighbour.confidence {
                        cs_core::Confidence::Measured => "◆",
                        cs_core::Confidence::Verified => "●",
                        cs_core::Confidence::Resolved => "◐",
                        cs_core::Confidence::Guessed => "○",
                    };
                    let ambiguity = if neighbour.candidates > 1 {
                        format!(" ({} Kandidaten)", neighbour.candidates)
                    } else {
                        String::new()
                    };
                    println!(
                        "    {marker} {:<40} {}:{}{}",
                        neighbour.node.qualified,
                        neighbour.evidence_path,
                        neighbour.evidence_line,
                        ambiguity
                    );
                }
            }
            println!("\n  ● verifiziert  ◐ aufgelöst  ○ vermutet");
        }

        "top" => {
            let kinds = [NodeKind::Function, NodeKind::Method, NodeKind::Class];
            for hit in workspace.graph().top_by_relevance(30, &kinds)? {
                println!(
                    "{:>5.0}%  {:<40} {}:{}",
                    hit.relevance * 100.0,
                    hit.qualified,
                    hit.path,
                    hit.line
                );
            }
        }

        "stats" => print_stats(&workspace)?,

        "ask" => {
            let question = rest.join(" ");
            if question.trim().is_empty() {
                bail!("keine Frage angegeben");
            }
            ask(&workspace, &question)?;
        }

        other => bail!("unbekannter Befehl: {other}"),
    }

    Ok(())
}

/// Pulls `--db <pfad>` out of the trailing arguments, wherever it sits.
///
/// Kept deliberately small rather than reaching for an argument parser: the
/// positional grammar is the whole interface, and one optional flag does not
/// justify a dependency that would have to be threaded through every arm.
fn take_db_flag(rest: Vec<String>) -> (Option<PathBuf>, Vec<String>) {
    let mut db_path = None;
    let mut remaining = Vec::with_capacity(rest.len());
    let mut iter = rest.into_iter();

    while let Some(arg) = iter.next() {
        match arg.as_str() {
            "--db" => db_path = iter.next().map(PathBuf::from),
            other if other.starts_with("--db=") => {
                db_path = Some(PathBuf::from(&other["--db=".len()..]));
            }
            _ => remaining.push(arg),
        }
    }

    (db_path, remaining)
}

/// Runs one question through the assistant and prints the verification verdict.
///
/// This is the honest end-to-end check of the product's central claim, and it is
/// in the CLI on purpose: it can be run against any repository with any local
/// model, without a window in the way.
fn ask(workspace: &Workspace, question: &str) -> Result<()> {
    let providers = cs_llm::provider::discover_local();
    let Some(provider) = providers.into_iter().next() else {
        bail!(
            "Kein lokales Modell gefunden. CodeSearch sucht auf Port 11434 (Ollama), \
             1234 (LM Studio), 8080 (llama.cpp) und 8000 (vLLM)."
        );
    };

    let model = std::env::var("CS_MODEL")
        .ok()
        .filter(|m| provider.models.contains(m))
        .or_else(|| provider.models.first().cloned())
        .ok_or_else(|| anyhow::anyhow!("{} meldet keine Modelle", provider.name))?;

    eprintln!("{} · {model}\n", provider.name);

    let assistant = cs_llm::Assistant::new(provider, &model);
    let answer = assistant.ask(workspace.graph(), question, &|activity| {
        eprintln!("  … {activity}");
    })?;

    println!("\n{}\n", answer.verified.text);

    if !answer.trail.is_empty() {
        println!("SPUR");
        for (index, step) in answer.trail.iter().enumerate() {
            let flag = if step.verified { " " } else { "!" };
            println!("{flag} {}. {}:{}  {}", index + 1, step.path, step.line, step.reason);
        }
        println!();
    }

    println!("PRÜFUNG");
    if answer.verified.citations.is_empty() {
        println!("  keine Belege angegeben");
    }
    for citation in &answer.verified.citations {
        println!(
            "  [{}] {}:{}-{}",
            citation.status.label(),
            citation.path,
            citation.from_line,
            citation.to_line
        );
    }
    for line in &answer.verified.quote_mismatches {
        println!("  [Zitat stimmt nicht] {line}");
    }
    println!(
        "  {} Nachschläge · {} Sätze ohne Beleg{}",
        answer.tool_calls,
        answer.verified.uncited_sentences,
        if answer.truncated { " · Suche abgebrochen" } else { "" }
    );
    println!("\n  {}", answer.verified.verdict().label());

    Ok(())
}

fn print_stats(workspace: &Workspace) -> Result<()> {
    let stats = workspace.graph().stats()?;
    println!(
        "\n{} Dateien ({} geparst) · {} Knoten · {} Kanten",
        stats.files, stats.parsed_files, stats.nodes, stats.edges
    );
    let share = if stats.edges > 0 {
        stats.guessed_edges as f64 / stats.edges as f64 * 100.0
    } else {
        0.0
    };
    println!(
        "{} davon vermutet ({share:.0}%) · {} dynamische Lücken",
        stats.guessed_edges, stats.dynamic_gaps
    );
    Ok(())
}
