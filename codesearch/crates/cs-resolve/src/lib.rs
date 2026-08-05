//! Connecting names to definitions, and being honest about how sure it is.
//!
//! The resolver runs in tiers and every edge it emits carries the tier it came
//! from:
//!
//! * **Resolved** — the file's own scope or its imports make the target
//!   unambiguous. One definition, no competition.
//! * **Guessed** — several definitions share the name and the best-scoring one
//!   was picked. The edge records how many others tied, so the UI can say
//!   "vermutet (3 Kandidaten)" and the model is barred from asserting it.
//! * *(Verified is produced by the language-server pass, which plugs in here via
//!   the same [`Edge`] type and wins on confidence rank.)*
//!
//! Two outcomes are deliberately **not** edges. A name that matches nothing
//! anywhere is a language builtin and is dropped — inventing a node for `len`
//! would fill the graph with noise. A name that matches a known dynamic
//! construct (`getattr`, `eval`, reflection) becomes a [`NodeKind::DynamicGap`]
//! node: a visible hole that says "static analysis cannot see past here", which
//! is the honest answer and the exact thing phase 2 tracing later fills in.

pub mod modules;

use cs_core::{
    Confidence, ContentHash, Edge, EdgeKind, Evidence, FileId, Language, NodeId, NodeKind, Source,
    Span, Symbol, Visibility,
};
use cs_index::parse::{Binding, Import, Reference};
use modules::PathIndex;
use std::collections::HashMap;

/// Everything the resolver needs to know about one parsed file.
pub struct FileFacts {
    pub file: FileId,
    pub path: String,
    pub language: Language,
    pub hash: ContentHash,
    pub symbols: Vec<Symbol>,
    pub references: Vec<Reference>,
    pub imports: Vec<Import>,
    pub bindings: Vec<Binding>,
}

#[derive(Default)]
pub struct Resolved {
    pub edges: Vec<Edge>,
    /// Third-party packages discovered through imports. Worth their own nodes:
    /// "what does this project actually depend on, and where" is a question the
    /// dependency manifest answers badly and the import graph answers exactly.
    pub externals: Vec<(NodeId, String, Language)>,
    /// Places where static analysis knowingly stopped.
    pub gaps: Vec<(NodeId, GapInfo)>,
}

#[derive(Debug, Clone)]
pub struct GapInfo {
    pub file: FileId,
    pub path: String,
    pub line: u32,
    /// The construct that caused it, e.g. `getattr`.
    pub construct: String,
}

/// One definition, as the resolver sees it.
struct Def {
    id: NodeId,
    file: FileId,
    path: String,
    name: String,
    kind: NodeKind,
    /// Enclosing class name, when the definition is a method.
    owner: Option<String>,
    visibility: Visibility,
}

/// Constructs that defeat static resolution. A call to one of these is recorded
/// as a gap rather than silently dropped, because "we cannot see this" is
/// information and its absence is a lie by omission.
fn dynamic_constructs(language: Language) -> &'static [&'static str] {
    use Language::*;
    match language {
        Python => &["getattr", "setattr", "eval", "exec", "__import__", "import_module", "globals", "locals"],
        JavaScript | TypeScript | Tsx => &["eval", "Function", "Reflect", "importScripts"],
        Java => &["forName", "getMethod", "getDeclaredMethod", "invoke", "newInstance"],
        CSharp => &["GetType", "GetMethod", "Invoke", "CreateInstance", "Activator"],
        Ruby => &["send", "public_send", "method_missing", "instance_eval", "const_get", "eval"],
        Php => &["call_user_func", "call_user_func_array", "eval", "variable_variables"],
        Go => &["reflect", "ValueOf", "MethodByName"],
        Rust => &[],
        C | Cpp => &["dlsym", "dlopen"],
        Bash => &["eval", "source"],
    }
}

/// Resolves the whole workspace in one pass.
pub fn resolve(files: &[FileFacts]) -> Resolved {
    let paths: Vec<String> = files.iter().map(|f| f.path.clone()).collect();
    let path_index = PathIndex::build(&paths);

    let mut defs: Vec<Def> = Vec::new();
    let mut by_name: HashMap<&str, Vec<usize>> = HashMap::new();
    let mut by_file: HashMap<FileId, Vec<usize>> = HashMap::new();
    let mut file_of_path: HashMap<&str, FileId> = HashMap::new();

    for facts in files {
        file_of_path.insert(facts.path.as_str(), facts.file);
        for symbol in &facts.symbols {
            // `Invoice.apply_discount` → owner `Invoice`.
            let owner = symbol.qualified.rsplit_once('.').map(|(head, _)| {
                head.rsplit('.').next().unwrap_or(head).to_string()
            });
            defs.push(Def {
                id: symbol.id,
                file: facts.file,
                path: facts.path.clone(),
                name: symbol.name.clone(),
                kind: symbol.kind,
                owner,
                visibility: symbol.visibility,
            });
        }
    }
    for (index, def) in defs.iter().enumerate() {
        by_name.entry(def.name.as_str()).or_default().push(index);
        by_file.entry(def.file).or_default().push(index);
    }

    // Class → the classes it extends, by name.
    //
    // Method lookup has to walk this. In any real framework the method you call
    // on an object is usually defined on a base class — `app.route()` lives on
    // Flask's `Scaffold`, not on `Flask` — so without inheritance the binding and
    // `self` rules would resolve almost nothing in exactly the code where they
    // matter most.
    let mut parents_of: HashMap<&str, Vec<&str>> = HashMap::new();
    for facts in files {
        for reference in &facts.references {
            if reference.kind != EdgeKind::Inherits {
                continue;
            }
            // The `from` of an inheritance reference is the class itself.
            if let Some(child) = defs.iter().find(|d| d.id == reference.from) {
                parents_of
                    .entry(child.name.as_str())
                    .or_default()
                    .push(reference.name.as_str());
            }
        }
    }

    // Names a file re-exports rather than defines: `flask/__init__.py` imports
    // `Blueprint` from `flask/blueprints.py` and everyone imports it from the
    // package. Without following one hop of re-export, every such call in a
    // package-structured codebase degrades to a name guess — and package
    // `__init__` files exist precisely so that callers do not have to know where
    // things really live.
    //
    // One hop only. Chains of re-exports are rare, and each extra hop makes a
    // wrong answer more likely while the confidence label stays the same.
    let mut reexports: HashMap<(FileId, &str), FileId> = HashMap::new();
    for facts in files {
        for import in &facts.imports {
            let Some(symbol) = &import.symbol else { continue };
            let defines_it = by_file
                .get(&facts.file)
                .map(|indices| indices.iter().any(|i| defs[*i].name == *symbol))
                .unwrap_or(false);
            if defines_it {
                continue;
            }
            let targets = path_index.resolve(&import.module, facts.language, &facts.path);
            if let Some(target) = targets.first().and_then(|p| file_of_path.get(p.as_str())) {
                reexports.insert((facts.file, symbol.as_str()), *target);
            }
        }
    }

    let mut out = Resolved::default();

    for facts in files {
        // Which files this one can see, and under what name.
        let mut imported_files: Vec<FileId> = Vec::new();
        let mut imported_names: HashMap<String, Vec<FileId>> = HashMap::new();
        // Names that come from outside the workspace entirely.
        let mut external_names: HashMap<String, NodeId> = HashMap::new();

        for import in &facts.imports {
            let targets = path_index.resolve(&import.module, facts.language, &facts.path);
            let file_node = NodeId::of_symbol(facts.file, "file", &facts.path);

            if targets.is_empty() {
                let package = root_package(&import.module);
                if package.is_empty() {
                    continue;
                }
                let external = NodeId::of_external(facts.language.slug(), &package);
                out.externals.push((external, package.clone(), facts.language));

                // An explicitly imported name refers to the dependency, even
                // when a local definition happens to share it. Without this,
                // `from werkzeug.utils import send_file` inside a module that
                // also defines `send_file` produces a confident self-call that
                // is simply false.
                for name in [import.symbol.as_deref(), import.alias.as_deref()]
                    .into_iter()
                    .flatten()
                {
                    external_names.insert(name.to_string(), external);
                }
                out.edges.push(Edge::new(
                    file_node,
                    external,
                    EdgeKind::Imports,
                    Confidence::Resolved,
                    Source::ScopeResolution,
                    Evidence { file: facts.file, span: import.span, content_hash: facts.hash },
                ));
                continue;
            }

            for target_path in targets.iter().take(2) {
                let Some(&target) = file_of_path.get(target_path.as_str()) else { continue };
                imported_files.push(target);
                if let Some(symbol) = &import.symbol {
                    imported_names.entry(symbol.clone()).or_default().push(target);
                    // `from flask import Blueprint` where flask/__init__.py only
                    // re-exports it: follow to where it is actually defined.
                    if let Some(&origin) = reexports.get(&(target, symbol.as_str())) {
                        imported_names.entry(symbol.clone()).or_default().push(origin);
                        imported_files.push(origin);
                    }
                }
                if let Some(alias) = &import.alias {
                    imported_names.entry(alias.clone()).or_default().push(target);
                }
                // `import flask` binds the name `flask`, so `flask.Flask(...)`
                // can be resolved through the import rather than guessed at.
                if let Some(tail) = import.module.rsplit(['.', '/', '\\', ':']).find(|s| !s.is_empty())
                {
                    let tail = tail.trim_end_matches(".py").trim_end_matches(".rb");
                    imported_names.entry(tail.to_string()).or_default().push(target);
                }
                // Everything the imported module itself re-exports is reachable
                // through it, so `import flask` makes `flask.Blueprint` resolve
                // into blueprints.py where Blueprint is actually defined.
                for ((host, name), origin) in &reexports {
                    if *host == target {
                        imported_files.push(*origin);
                        imported_names.entry((*name).to_string()).or_default().push(*origin);
                    }
                }

                out.edges.push(Edge::new(
                    file_node,
                    NodeId::of_symbol(target, "file", target_path),
                    EdgeKind::Imports,
                    // Two candidate files means the import is genuinely ambiguous
                    // from the workspace alone.
                    if targets.len() == 1 { Confidence::Resolved } else { Confidence::Guessed },
                    Source::ScopeResolution,
                    Evidence { file: facts.file, span: import.span, content_hash: facts.hash },
                ));
            }
        }

        let empty = Vec::new();
        let local: &[usize] = by_file.get(&facts.file).map(|v| v.as_slice()).unwrap_or(&empty);

        // A variable bound to two *different* constructors has an ambiguous type
        // and is dropped. Bound twenty times to the same one is not ambiguous at
        // all — that is just a test file where every test starts
        // `app = Flask(__name__)`, which is exactly the case worth resolving.
        let mut distinct: HashMap<&str, &str> = HashMap::new();
        let mut conflicting: Vec<&str> = Vec::new();
        for binding in &facts.bindings {
            let variable = binding.variable.as_str();
            let constructor = binding.constructor.as_str();
            match distinct.get(variable) {
                Some(existing) if *existing != constructor => conflicting.push(variable),
                Some(_) => {}
                None => {
                    distinct.insert(variable, constructor);
                }
            }
        }
        let bindings: HashMap<&str, &str> = distinct
            .into_iter()
            .filter(|(variable, _)| !conflicting.contains(variable))
            .collect();

        // Which class each symbol in this file belongs to, for `self.method()`.
        let owner_of: HashMap<NodeId, &str> = local
            .iter()
            .filter_map(|i| defs[*i].owner.as_deref().map(|owner| (defs[*i].id, owner)))
            .collect();

        for reference in &facts.references {
            let evidence =
                Evidence { file: facts.file, span: reference.span, content_hash: facts.hash };

            if let Some(&external) = external_names.get(&reference.name) {
                out.edges.push(Edge::new(
                    reference.from,
                    external,
                    reference.kind,
                    Confidence::Resolved,
                    Source::ScopeResolution,
                    evidence,
                ));
                continue;
            }

            match pick_target(
                reference,
                &defs,
                &by_name,
                local,
                &imported_files,
                &imported_names,
                &facts.path,
                facts.language,
                owner_of.get(&reference.from).copied(),
                &bindings,
                &parents_of,
            ) {
                Outcome::Certain(target) => out.edges.push(Edge::new(
                    reference.from,
                    target,
                    reference.kind,
                    Confidence::Resolved,
                    Source::ScopeResolution,
                    evidence,
                )),
                Outcome::Guess { target, competitors } => out.edges.push(
                    Edge::new(
                        reference.from,
                        target,
                        reference.kind,
                        Confidence::Guessed,
                        Source::NameHeuristic,
                        evidence,
                    )
                    .with_candidates(competitors),
                ),
                Outcome::Dynamic => {
                    let gap = NodeId::of_gap(facts.file, reference.span.start_line, &reference.name);
                    out.gaps.push((
                        gap,
                        GapInfo {
                            file: facts.file,
                            path: facts.path.clone(),
                            line: reference.span.start_line,
                            construct: reference.name.clone(),
                        },
                    ));
                    out.edges.push(Edge::new(
                        reference.from,
                        gap,
                        EdgeKind::Calls,
                        // The *gap* is a fact, even though its target is not.
                        Confidence::Resolved,
                        // Resolution produced this, so resolution must be able to
                        // clear it. Marking it Syntax would make it survive a
                        // re-resolve that no longer finds the gap.
                        Source::ScopeResolution,
                        evidence,
                    ));
                }
                Outcome::Unknown => {}
            }
        }
    }

    out.externals.sort_by_key(|(id, _, _)| id.raw());
    out.externals.dedup_by_key(|(id, _, _)| *id);
    out.gaps.sort_by_key(|(id, _)| id.raw());
    out.gaps.dedup_by_key(|(id, _)| *id);
    out
}

enum Outcome {
    Certain(NodeId),
    Guess { target: NodeId, competitors: u16 },
    Dynamic,
    /// A builtin, or a name from a dependency we do not index. Dropped on
    /// purpose: an edge to nothing is worse than no edge.
    Unknown,
}

#[allow(clippy::too_many_arguments)]
fn pick_target(
    reference: &Reference,
    defs: &[Def],
    by_name: &HashMap<&str, Vec<usize>>,
    local: &[usize],
    imported_files: &[FileId],
    imported_names: &HashMap<String, Vec<FileId>>,
    from_path: &str,
    language: Language,
    enclosing_class: Option<&str>,
    bindings: &HashMap<&str, &str>,
    parents_of: &HashMap<&str, Vec<&str>>,
) -> Outcome {
    let Some(matching) = by_name.get(reference.name.as_str()) else {
        // Nothing in the workspace has this name. Either it is a construct that
        // defeats static analysis, or it is a builtin. The distinction is per
        // language: `send` is reflection in Ruby and an ordinary helper in Go.
        return if dynamic_constructs(language).contains(&reference.name.as_str()) {
            Outcome::Dynamic
        } else {
            Outcome::Unknown
        };
    };

    // Tier B, strongest form: exactly one definition in this very file. Scope
    // beats everything — a local helper shadows any number of same-named symbols
    // elsewhere in the repo.
    let local_matches: Vec<usize> = local
        .iter()
        .copied()
        .filter(|i| defs[*i].name == reference.name && wanted_kind(defs[*i].kind, reference.kind))
        .collect();
    if local_matches.len() == 1 {
        return Outcome::Certain(defs[local_matches[0]].id);
    }

    // Tier B: the name was explicitly imported from a file that defines it.
    if let Some(sources) = imported_names.get(&reference.name) {
        let hits: Vec<usize> = matching
            .iter()
            .copied()
            .filter(|i| sources.contains(&defs[*i].file) && wanted_kind(defs[*i].kind, reference.kind))
            .collect();
        if hits.len() == 1 {
            return Outcome::Certain(defs[hits[0]].id);
        }
    }

    if let Some(receiver) = &reference.receiver {
        // Tier B: `Receiver.method` where the receiver names a class directly.
        if let Some(id) = lookup_in_hierarchy(receiver, matching, defs, parents_of) {
            return Outcome::Certain(id);
        }

        // Tier B: `self.method()` — the enclosing class is a fact of the syntax
        // tree, not a guess. This is the single most common call shape in
        // object-oriented code, and leaving it to the name heuristic would
        // downgrade most of a typical class-based codebase to "vermutet".
        if is_self_reference(receiver) {
            if let Some(class) = enclosing_class {
                if let Some(id) = lookup_in_hierarchy(class, matching, defs, parents_of) {
                    return Outcome::Certain(id);
                }
            }
        }

        // Tier B: the receiver is an imported module — `flask.Flask(...)` after
        // `import flask`. The import pins which file to look in.
        if let Some(sources) = imported_names.get(receiver) {
            let hits: Vec<usize> =
                matching.iter().copied().filter(|i| sources.contains(&defs[*i].file)).collect();
            if hits.len() == 1 {
                return Outcome::Certain(defs[hits[0]].id);
            }
        }

        // Tier B: the receiver is a variable bound to a constructor —
        // `app = Flask(...)` makes `app.route(...)` a call on `Flask`.
        if let Some(type_name) = bindings.get(receiver.as_str()) {
            if let Some(id) = lookup_in_hierarchy(type_name, matching, defs, parents_of) {
                return Outcome::Certain(id);
            }
        }
    }

    // Tier C: score every candidate and take the best, recording the tie.
    let mut best_score = i32::MIN;
    let mut best: Option<usize> = None;
    let mut tied = 0u16;

    for &index in matching {
        let score = score_candidate(&defs[index], reference, imported_files, from_path);
        if score > best_score {
            best_score = score;
            best = Some(index);
            tied = 1;
        } else if score == best_score {
            tied += 1;
        }
    }

    match best {
        Some(index) => Outcome::Guess { target: defs[index].id, competitors: tied },
        None => Outcome::Unknown,
    }
}

/// Finds a method on `class` or on any class it inherits from.
///
/// Returns a target only when exactly one class in the hierarchy defines the
/// name. Two competing definitions mean the real answer depends on the method
/// resolution order, which differs per language and is not something to guess at
/// with `Resolved` confidence — those fall through to the scored heuristic.
///
/// The walk is depth-limited and tracks what it has seen: inheritance cycles are
/// illegal in every language here, but a broken parse can still produce one.
fn lookup_in_hierarchy(
    class: &str,
    matching: &[usize],
    defs: &[Def],
    parents_of: &HashMap<&str, Vec<&str>>,
) -> Option<NodeId> {
    let mut queue = vec![class];
    let mut seen: Vec<&str> = vec![class];

    for _ in 0..8 {
        let mut next = Vec::new();
        for current in queue.drain(..) {
            let hits: Vec<usize> = matching
                .iter()
                .copied()
                .filter(|i| defs[*i].owner.as_deref() == Some(current))
                .collect();
            if hits.len() == 1 {
                return Some(defs[hits[0]].id);
            }
            if hits.len() > 1 {
                // Ambiguous at this level; a base class cannot make it clearer.
                return None;
            }
            for parent in parents_of.get(current).into_iter().flatten() {
                if !seen.contains(parent) {
                    seen.push(parent);
                    next.push(*parent);
                }
            }
        }
        if next.is_empty() {
            break;
        }
        queue = next;
    }
    None
}

/// The receiver keywords that mean "the object this code is running on".
fn is_self_reference(receiver: &str) -> bool {
    matches!(receiver, "self" | "this" | "cls" | "Self" | "$this" | "me")
}

/// Whether a definition can plausibly be the target of this kind of reference.
fn wanted_kind(kind: NodeKind, reference_kind: EdgeKind) -> bool {
    match reference_kind {
        EdgeKind::Inherits => matches!(kind, NodeKind::Class | NodeKind::Interface),
        // A read only ever targets data. Letting it match a function would
        // connect `TAX` to a same-named helper somewhere else in the repo.
        EdgeKind::Reads | EdgeKind::Writes => matches!(kind, NodeKind::Field | NodeKind::Global),
        _ => kind.is_callable() || matches!(kind, NodeKind::Class | NodeKind::Field),
    }
}

fn score_candidate(
    def: &Def,
    reference: &Reference,
    imported_files: &[FileId],
    from_path: &str,
) -> i32 {
    let mut score = 0;

    if imported_files.contains(&def.file) {
        score += 6;
    }
    if reference.receiver.as_deref() == def.owner.as_deref() && def.owner.is_some() {
        score += 5;
    }
    if same_directory(&def.path, from_path) {
        score += 3;
    }
    if wanted_kind(def.kind, reference.kind) {
        score += 2;
    }
    match def.visibility {
        Visibility::Public => score += 1,
        Visibility::Internal => score -= 1,
        Visibility::Private | Visibility::Protected => score -= 2,
    }
    // A test rarely calls into another test, and treating tests as likely
    // targets otherwise pollutes every heuristic result in repos with big test
    // suites.
    if def.kind == NodeKind::Test {
        score -= 3;
    }
    score
}

fn same_directory(a: &str, b: &str) -> bool {
    let dir = |p: &str| p.rsplit_once('/').map(|(d, _)| d.to_string()).unwrap_or_default();
    dir(a) == dir(b)
}

/// `pkg.sub.mod` → `pkg`; `@scope/name/deep` → `@scope/name`; `./x` → "".
fn root_package(module: &str) -> String {
    let module = module.trim();
    if module.starts_with('.') || module.starts_with('/') {
        return String::new();
    }
    if let Some(rest) = module.strip_prefix('@') {
        let mut parts = rest.splitn(3, '/');
        let scope = parts.next().unwrap_or_default();
        let name = parts.next().unwrap_or_default();
        return format!("@{scope}/{name}");
    }
    module
        .split(['.', '/', ':'])
        .find(|p| !p.is_empty())
        .unwrap_or(module)
        .to_string()
}

/// Builds the resolver's view of a file. Kept here so callers do not have to
/// know the shape of [`FileFacts`].
pub fn file_facts(
    file: FileId,
    path: String,
    language: Language,
    hash: ContentHash,
    parsed: cs_index::parse::ParsedFile,
) -> FileFacts {
    FileFacts {
        file,
        path,
        language,
        hash,
        symbols: parsed.symbols,
        references: parsed.references,
        imports: parsed.imports,
        bindings: parsed.bindings,
    }
}

/// The node representing a whole file, so imports and containment have somewhere
/// to point.
pub fn file_symbol(file: FileId, path: &str, language: Language, lines: u32) -> Symbol {
    Symbol {
        id: NodeId::of_symbol(file, "file", path),
        kind: NodeKind::File,
        language,
        name: path.rsplit('/').next().unwrap_or(path).to_string(),
        qualified: path.to_string(),
        span: Span::new(0, 0, 1, lines.max(1)),
        signature_span: Span::new(0, 0, 1, 1),
        parent: None,
        visibility: Visibility::Public,
        doc: None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use cs_index::parse::parse_file;
    use cs_index::registry::pack;

    fn facts_for(path: &str, language: Language, source: &str) -> FileFacts {
        let file = FileId::of_path(path);
        let parsed = parse_file(pack(language).unwrap(), file, path, source).unwrap();
        file_facts(file, path.to_string(), language, ContentHash::of(source.as_bytes()), parsed)
    }

    fn call_edge<'a>(resolved: &'a Resolved, from: &str, defs: &[&FileFacts]) -> Option<&'a Edge> {
        let from_id = defs
            .iter()
            .flat_map(|f| f.symbols.iter())
            .find(|s| s.name == from)
            .map(|s| s.id)?;
        resolved.edges.iter().find(|e| e.from == from_id && e.kind == EdgeKind::Calls)
    }

    #[test]
    fn a_call_to_a_local_helper_is_resolved_not_guessed() {
        let file = facts_for("app.py", Language::Python, "def caller():\n    helper()\n\ndef helper():\n    pass\n");
        let resolved = resolve(std::slice::from_ref(&file));

        let edge = call_edge(&resolved, "caller", &[&file]).expect("call edge exists");
        assert_eq!(edge.confidence, Confidence::Resolved);
    }

    #[test]
    fn an_explicit_import_resolves_across_files() {
        let lib = facts_for("lib.py", Language::Python, "def compute(x):\n    return x\n");
        let app = facts_for(
            "app.py",
            Language::Python,
            "from lib import compute\n\ndef run():\n    return compute(1)\n",
        );
        let resolved = resolve(&[lib, app]);

        let edge = resolved
            .edges
            .iter()
            .find(|e| e.kind == EdgeKind::Calls)
            .expect("cross-file call resolved");
        assert_eq!(edge.confidence, Confidence::Resolved);
    }

    #[test]
    fn an_ambiguous_name_is_marked_as_a_guess_with_its_competitors() {
        let a = facts_for("a/handler.py", Language::Python, "def process(x):\n    return x\n");
        let b = facts_for("b/handler.py", Language::Python, "def process(x):\n    return x\n");
        let caller = facts_for("main.py", Language::Python, "def run():\n    return process(1)\n");
        let resolved = resolve(&[a, b, caller]);

        let edge = resolved
            .edges
            .iter()
            .find(|e| e.kind == EdgeKind::Calls)
            .expect("a guess is still an edge");
        assert_eq!(edge.confidence, Confidence::Guessed);
        assert_eq!(edge.candidate_count, 2, "the user must see how ambiguous this is");
    }

    #[test]
    fn a_local_definition_beats_an_identical_name_elsewhere() {
        let other = facts_for("other.py", Language::Python, "def process(x):\n    return x\n");
        let here = facts_for(
            "here.py",
            Language::Python,
            "def process(x):\n    return x\n\ndef run():\n    return process(1)\n",
        );
        let local_target = here.symbols.iter().find(|s| s.name == "process").unwrap().id;
        let resolved = resolve(&[other, here]);

        let edge = resolved.edges.iter().find(|e| e.kind == EdgeKind::Calls).unwrap();
        assert_eq!(edge.to, local_target);
        assert_eq!(edge.confidence, Confidence::Resolved);
    }

    #[test]
    fn builtins_produce_no_edge_at_all() {
        let file = facts_for("b.py", Language::Python, "def f(xs):\n    return len(xs)\n");
        let resolved = resolve(std::slice::from_ref(&file));
        assert!(
            resolved.edges.iter().all(|e| e.kind != EdgeKind::Calls),
            "an edge into nothing is worse than no edge"
        );
    }

    #[test]
    fn reflection_becomes_a_visible_gap_rather_than_silence() {
        let file = facts_for(
            "d.py",
            Language::Python,
            "def dispatch(obj, name):\n    return getattr(obj, name)()\n",
        );
        let resolved = resolve(std::slice::from_ref(&file));

        assert_eq!(resolved.gaps.len(), 1, "the hole itself is information");
        assert_eq!(resolved.gaps[0].1.construct, "getattr");
        assert!(resolved.edges.iter().any(|e| e.to == resolved.gaps[0].0));
    }

    #[test]
    fn third_party_imports_become_external_package_nodes() {
        let file = facts_for("n.py", Language::Python, "import requests\n");
        let resolved = resolve(std::slice::from_ref(&file));

        assert_eq!(resolved.externals.len(), 1);
        assert_eq!(resolved.externals[0].1, "requests");
    }

    #[test]
    fn every_edge_carries_evidence_that_points_at_real_source() {
        let source = "def caller():\n    helper()\n\ndef helper():\n    pass\n";
        let file = facts_for("e.py", Language::Python, source);
        let resolved = resolve(std::slice::from_ref(&file));

        assert!(!resolved.edges.is_empty());
        for edge in &resolved.edges {
            assert!(edge.evidence.span.end_byte as usize <= source.len());
            assert!(edge.evidence.span.start_line >= 1);
            assert_eq!(edge.evidence.content_hash, ContentHash::of(source.as_bytes()));
        }
    }

    #[test]
    fn self_calls_resolve_through_the_enclosing_class() {
        let file = facts_for(
            "s.py",
            Language::Python,
            "class Engine:\n\
             \x20   def start(self):\n\
             \x20       return self.warm_up()\n\
             \x20   def warm_up(self):\n\
             \x20       return 1\n\
             \n\
             class Other:\n\
             \x20   def warm_up(self):\n\
             \x20       return 2\n",
        );
        let expected = file
            .symbols
            .iter()
            .find(|s| s.qualified == "Engine.warm_up")
            .unwrap()
            .id;
        let resolved = resolve(std::slice::from_ref(&file));

        let edge = resolved
            .edges
            .iter()
            .find(|e| e.kind == EdgeKind::Calls)
            .expect("self.warm_up() is a call");
        assert_eq!(edge.confidence, Confidence::Resolved);
        assert_eq!(edge.to, expected, "Other.warm_up must not win over the enclosing class");
    }

    #[test]
    fn a_method_inherited_from_a_base_class_still_resolves() {
        // The common framework shape: the method you call is defined two levels
        // up. Without walking the hierarchy this degrades to a name guess in
        // exactly the code where the graph matters most.
        let file = facts_for(
            "h.py",
            Language::Python,
            "class Base:\n\
             \x20   def route(self, rule):\n\
             \x20       return rule\n\
             \n\
             class Middle(Base):\n\
             \x20   pass\n\
             \n\
             class App(Middle):\n\
             \x20   pass\n\
             \n\
             def build():\n\
             \x20   app = App()\n\
             \x20   return app.route('/')\n",
        );
        let expected = file.symbols.iter().find(|s| s.qualified == "Base.route").unwrap().id;
        let resolved = resolve(std::slice::from_ref(&file));

        let edge = resolved
            .edges
            .iter()
            .find(|e| e.kind == EdgeKind::Calls && e.to == expected)
            .expect("app.route() should reach Base.route through App → Middle → Base");
        assert_eq!(edge.confidence, Confidence::Resolved);
    }

    #[test]
    fn repeating_the_same_binding_does_not_make_it_ambiguous() {
        // Every test in a test file starts `app = App()`. Twenty identical
        // bindings say the same thing as one; treating repetition as conflict
        // silently gave up on the largest single category of resolvable calls.
        let file = facts_for(
            "t.py",
            Language::Python,
            "class App:\n\
             \x20   def run(self):\n\
             \x20       return 1\n\
             \n\
             def test_one():\n\
             \x20   app = App()\n\
             \x20   app.run()\n\
             \n\
             def test_two():\n\
             \x20   app = App()\n\
             \x20   app.run()\n",
        );
        let resolved = resolve(std::slice::from_ref(&file));

        let call = resolved
            .edges
            .iter()
            .find(|e| e.kind == EdgeKind::Calls)
            .expect("app.run() is a call");
        assert_eq!(call.confidence, Confidence::Resolved);
    }

    #[test]
    fn a_variable_bound_to_two_different_types_is_not_used_for_resolution() {
        let file = facts_for(
            "c.py",
            Language::Python,
            "class A:\n\
             \x20   def go(self):\n\
             \x20       return 1\n\
             \n\
             class B:\n\
             \x20   def go(self):\n\
             \x20       return 2\n\
             \n\
             def pick(flag):\n\
             \x20   thing = A()\n\
             \x20   if flag:\n\
             \x20       thing = B()\n\
             \x20   return thing.go()\n",
        );
        // `A()` and `B()` are calls too, so the edge has to be picked by target.
        let go_targets: Vec<NodeId> =
            file.symbols.iter().filter(|s| s.name == "go").map(|s| s.id).collect();
        assert_eq!(go_targets.len(), 2);
        let resolved = resolve(std::slice::from_ref(&file));

        let call = resolved
            .edges
            .iter()
            .find(|e| e.kind == EdgeKind::Calls && go_targets.contains(&e.to))
            .expect("thing.go() produced an edge");
        assert_eq!(
            call.confidence,
            Confidence::Guessed,
            "a genuinely ambiguous type must not be reported as resolved"
        );
        assert_eq!(call.candidate_count, 2, "both candidates must stay visible");
    }

    #[test]
    fn a_name_re_exported_by_a_package_resolves_to_where_it_is_defined() {
        let core = facts_for("pkg/core.py", Language::Python, "class Widget:\n    pass\n");
        let init = facts_for("pkg/__init__.py", Language::Python, "from pkg.core import Widget\n");
        let app = facts_for(
            "app.py",
            Language::Python,
            "from pkg import Widget\n\ndef build():\n    return Widget()\n",
        );
        let expected = core.symbols.iter().find(|s| s.name == "Widget").unwrap().id;
        let resolved = resolve(&[core, init, app]);

        let edge = resolved
            .edges
            .iter()
            .find(|e| e.kind == EdgeKind::Calls && e.to == expected)
            .expect("Widget() must reach pkg/core.py, not stop at the package __init__");
        assert_eq!(edge.confidence, Confidence::Resolved);
    }

    #[test]
    fn scoring_prefers_an_imported_definition_over_a_distant_one() {
        let lib = facts_for("lib/util.py", Language::Python, "def helper():\n    pass\n");
        let far = facts_for("far/away/util.py", Language::Python, "def helper():\n    pass\n");
        let app = facts_for(
            "app.py",
            Language::Python,
            "import lib.util\n\ndef run():\n    helper()\n",
        );
        let expected = lib.symbols.iter().find(|s| s.name == "helper").unwrap().id;
        let resolved = resolve(&[lib, far, app]);

        let edge = resolved.edges.iter().find(|e| e.kind == EdgeKind::Calls).unwrap();
        assert_eq!(edge.to, expected);
    }
}
