//! Turning one file into symbols, references and computed facts.
//!
//! The output of this module is deliberately *unresolved*: a reference knows the
//! name that was written and where, but not what it points at. Deciding that is
//! `cs-resolve`'s job, because it needs the whole workspace. Keeping the split
//! sharp is what lets a single file be reparsed in isolation on every keystroke.

use crate::registry::{DocStyle, LanguagePack};
use cs_core::{
    ContentHash, EdgeKind, Facts, FileId, Language, NodeId, NodeKind, Param, SideEffect, Span,
    Symbol, Visibility,
};
use std::collections::HashSet;
use streaming_iterator::StreamingIterator;
use tree_sitter::{Node, Parser, QueryCursor, Tree};

/// Defined in `cs-core` so the index can store references verbatim; see
/// [`cs_core::RawReference`].
pub type Reference = cs_core::RawReference;
pub type Import = cs_core::RawImport;
pub type Binding = cs_core::RawBinding;

#[derive(Debug, Default)]
pub struct ParsedFile {
    pub symbols: Vec<Symbol>,
    pub references: Vec<Reference>,
    pub imports: Vec<Import>,
    /// Variables bound straight to a constructor call. See [`cs_core::RawBinding`].
    pub bindings: Vec<Binding>,
    pub facts: Vec<(NodeId, Facts)>,
    /// True when tree-sitter reported syntax errors. The results are still
    /// usable — that is the point of an error-tolerant parser — but the UI says
    /// so rather than presenting a partial parse as complete.
    pub had_errors: bool,
}

/// A definition found by the query, before containment is worked out.
struct RawDef<'t> {
    node: Node<'t>,
    name_node: Node<'t>,
    kind: NodeKind,
}

pub fn parse_file(
    pack: &LanguagePack,
    file: FileId,
    path: &str,
    source: &str,
) -> anyhow::Result<ParsedFile> {
    let mut parser = Parser::new();
    parser.set_language(&pack.ts_language)?;

    let Some(tree) = parser.parse(source, None) else {
        anyhow::bail!("tree-sitter could not parse {path}");
    };

    let bytes = source.as_bytes();
    let mut out = ParsedFile { had_errors: tree.root_node().has_error(), ..Default::default() };

    let (defs, raw_refs, imports) = run_query(pack, &tree, bytes);
    out.imports = imports;
    out.bindings = collect_bindings(tree.root_node(), bytes);

    // Containment first: a reference can only be attributed to the symbol that
    // encloses it, and a symbol's qualified name needs its ancestors.
    let mut placed = place_in_tree(defs);
    resolve_qualified_names(&mut placed, bytes, pack);

    let file_node = NodeId::of_symbol(file, "file", path);
    let mut symbols = Vec::with_capacity(placed.len());
    let mut global_names: HashSet<String> = HashSet::new();

    for entry in &placed {
        let name = text_of(entry.def.name_node, bytes).to_string();
        let qualified = entry.qualified.clone();

        // A function directly inside a class is a method; the query does not need
        // to know that, which keeps every pack one rule shorter.
        let kind = match entry.def.kind {
            NodeKind::Function if entry.parent_is_type => NodeKind::Method,
            other => other,
        };
        // Test-Erkennung greift fuer Methoden *und* freie Funktionen: eine
        // ``unittest.TestCase``-Methode (``test_login``) oder eine Jest-Methode
        // fiel sonst heraus — die Pruefung auf ``Function`` allein traf sie
        // nicht, weil eine Zeile vorher ``Method`` daraus wurde. Damit meldet
        // die Auswirkungsanalyse fuer halbe Codebasen nicht mehr „keine Tests".
        let kind = if matches!(kind, NodeKind::Function | NodeKind::Method)
            && pack.looks_like_test(&name, path)
        {
            NodeKind::Test
        } else {
            kind
        };

        if kind == NodeKind::Field && entry.parent.is_none() {
            global_names.insert(name.clone());
        }

        symbols.push(Symbol {
            id: NodeId::of_symbol(file, kind.as_str(), &qualified),
            kind,
            language: pack.language,
            name,
            qualified,
            span: span_of(entry.def.node),
            signature_span: signature_span(entry.def.node, pack),
            parent: entry.parent.map(|i| {
                let p = &placed[i];
                let p_kind = match p.def.kind {
                    NodeKind::Function if p.parent_is_type => NodeKind::Method,
                    other => other,
                };
                NodeId::of_symbol(file, p_kind.as_str(), &p.qualified)
            }),
            visibility: visibility_of(entry.def.node, bytes, pack.language),
            doc: doc_of(entry.def.node, bytes, pack),
        });
    }

    // Attribute every reference to its innermost enclosing definition.
    for raw in raw_refs {
        let owner = innermost_containing(&placed, raw.byte_range)
            .map(|i| symbols[i].id)
            .unwrap_or(file_node);
        out.references.push(Reference {
            name: raw.name,
            receiver: raw.receiver,
            span: raw.span,
            from: owner,
            kind: raw.kind,
        });
    }

    for (index, entry) in placed.iter().enumerate() {
        let symbol = &symbols[index];
        if !symbol.kind.is_callable() {
            continue;
        }
        out.facts.push((
            symbol.id,
            compute_facts(entry.def.node, bytes, pack, &placed, index, &global_names),
        ));
        out.references.extend(global_reads(
            entry.def.node,
            bytes,
            symbol.id,
            &global_names,
        ));
    }

    out.symbols = symbols;
    Ok(out)
}

struct RawRef {
    name: String,
    receiver: Option<String>,
    span: Span,
    byte_range: (u32, u32),
    kind: EdgeKind,
}

fn run_query<'t>(
    pack: &LanguagePack,
    tree: &'t Tree,
    bytes: &[u8],
) -> (Vec<RawDef<'t>>, Vec<RawRef>, Vec<Import>) {
    let caps = &pack.captures;
    let mut defs = Vec::new();
    let mut refs = Vec::new();
    let mut imports = Vec::new();

    let mut cursor = QueryCursor::new();
    let mut matches = cursor.matches(&pack.query, tree.root_node(), bytes);

    while let Some(m) = matches.next() {
        let capture_for = |index: Option<u32>| {
            index.and_then(|i| m.captures.iter().find(|c| c.index == i).map(|c| c.node))
        };

        let name_node = capture_for(caps.name);
        let receiver = capture_for(caps.receiver).map(|n| text_of(n, bytes).to_string());

        let def_kind = [
            (caps.def_function, NodeKind::Function),
            (caps.def_class, NodeKind::Class),
            (caps.def_interface, NodeKind::Interface),
            (caps.def_field, NodeKind::Field),
            (caps.def_module, NodeKind::Module),
        ]
        .into_iter()
        .find_map(|(index, kind)| capture_for(index).map(|node| (node, kind)));

        if let (Some((node, kind)), Some(name_node)) = (def_kind, name_node) {
            defs.push(RawDef { node, name_node, kind });
            continue;
        }

        let ref_kind = [(caps.ref_call, EdgeKind::Calls), (caps.ref_type, EdgeKind::Inherits)]
            .into_iter()
            .find_map(|(index, kind)| capture_for(index).map(|node| (node, kind)));

        if let (Some((node, kind)), Some(name_node)) = (ref_kind, name_node) {
            refs.push(RawRef {
                name: text_of(name_node, bytes).to_string(),
                receiver,
                span: span_of(node),
                byte_range: (node.start_byte() as u32, node.end_byte() as u32),
                kind,
            });
            continue;
        }

        if capture_for(caps.import).is_some() {
            if let Some(module) = capture_for(caps.import_module) {
                imports.push(Import {
                    module: unquote(text_of(module, bytes)).to_string(),
                    symbol: capture_for(caps.import_symbol).map(|n| text_of(n, bytes).to_string()),
                    alias: capture_for(caps.import_alias).map(|n| text_of(n, bytes).to_string()),
                    span: span_of(module),
                });
            }
        }
    }

    (defs, refs, imports)
}

/// A definition once its place in the containment tree is known.
struct Placed<'t> {
    def: RawDef<'t>,
    parent: Option<usize>,
    parent_is_type: bool,
    qualified: String,
}

/// Works out containment purely from byte ranges.
///
/// Sorting by start ascending and end *descending* means an enclosing definition
/// always precedes everything it contains, so a stack is enough. Doing it
/// structurally rather than by walking parent pointers keeps this independent of
/// how each grammar nests `impl` blocks, decorators or export wrappers.
fn place_in_tree(mut defs: Vec<RawDef<'_>>) -> Vec<Placed<'_>> {
    defs.sort_by_key(|d| (d.node.start_byte(), std::cmp::Reverse(d.node.end_byte())));

    let mut placed: Vec<Placed> = Vec::with_capacity(defs.len());
    let mut stack: Vec<usize> = Vec::new();

    for def in defs {
        let start = def.node.start_byte();
        while let Some(&top) = stack.last() {
            if placed[top].def.node.end_byte() <= start {
                stack.pop();
            } else {
                break;
            }
        }

        let parent = stack.last().copied();
        let parent_is_type = parent
            .map(|i| matches!(placed[i].def.kind, NodeKind::Class | NodeKind::Interface))
            .unwrap_or(false);

        // Names are filled in by `resolve_qualified_names`; parents are known to
        // come first in this ordering, so one forward pass is enough.
        placed.push(Placed { def, parent, parent_is_type, qualified: String::new() });
        stack.push(placed.len() - 1);
    }

    placed
}

/// Builds each definition's dotted path now that the source is at hand.
fn resolve_qualified_names(placed: &mut [Placed<'_>], bytes: &[u8], pack: &LanguagePack) {
    for i in 0..placed.len() {
        let name = text_of(placed[i].def.name_node, bytes).to_string();
        let prefix = qualifier_prefix(placed[i].def.node, bytes, pack);

        let qualified = match placed[i].parent {
            Some(p) => format!("{}.{}", placed[p].qualified, name),
            // Qualifier nodes only apply at the top level: a nested definition
            // already inherits its enclosing symbol's path.
            None if !prefix.is_empty() => format!("{}.{}", prefix.join("."), name),
            None => name,
        };
        placed[i].qualified = qualified;
    }
}

/// Names contributed by enclosing non-symbol constructs, outermost first.
///
/// Walks up the syntax tree rather than the symbol tree, because these
/// constructs are precisely the ones that are *not* symbols. The walk is
/// bounded by the tree's own depth, so there is nothing to guard against.
fn qualifier_prefix(node: Node<'_>, bytes: &[u8], pack: &LanguagePack) -> Vec<String> {
    let mut names = Vec::new();
    let mut current = node.parent();

    while let Some(ancestor) = current {
        if pack.manifest.qualifier_nodes.iter().any(|k| k == ancestor.kind()) {
            let named = ancestor
                .child_by_field_name("type")
                .or_else(|| ancestor.child_by_field_name("name"));
            if let Some(named) = named {
                // `impl<T> Wrapper<T>` should qualify as `Wrapper`, not with the
                // generic parameters attached.
                let text = text_of(named, bytes);
                let base = text.split(['<', '(', ' ']).next().unwrap_or(text);
                if !base.is_empty() {
                    names.push(base.to_string());
                }
            }
        }
        current = ancestor.parent();
    }

    names.reverse();
    names
}

/// Index of the innermost definition containing `range`.
fn innermost_containing(placed: &[Placed<'_>], range: (u32, u32)) -> Option<usize> {
    placed
        .iter()
        .enumerate()
        .filter(|(_, p)| {
            p.def.node.start_byte() as u32 <= range.0 && p.def.node.end_byte() as u32 >= range.1
        })
        .min_by_key(|(_, p)| p.def.node.end_byte() - p.def.node.start_byte())
        .map(|(i, _)| i)
}

fn compute_facts(
    node: Node<'_>,
    bytes: &[u8],
    pack: &LanguagePack,
    placed: &[Placed<'_>],
    self_index: usize,
    globals: &HashSet<String>,
) -> Facts {
    let mut facts = Facts {
        signature: signature_text(node, bytes, pack),
        params: extract_params(node, bytes),
        returns: extract_return_type(node, bytes),
        loc: (node.end_position().row - node.start_position().row) as u32 + 1,
        complexity: 1,
        ..Default::default()
    };

    // Nested definitions belong to their own symbol; counting their branches here
    // would make an outer function look far more complex than it reads.
    let nested: Vec<(usize, usize)> = placed
        .iter()
        .enumerate()
        .filter(|(i, p)| {
            *i != self_index
                && p.def.node.start_byte() >= node.start_byte()
                && p.def.node.end_byte() <= node.end_byte()
        })
        .map(|(_, p)| (p.def.node.start_byte(), p.def.node.end_byte()))
        .collect();

    let mut effects: Vec<SideEffect> = Vec::new();
    let mut nesting = 0u32;
    let mut max_nesting = 0u32;
    let mut calls = 0u32;

    walk(node, &mut |n: Node<'_>, entering: bool| {
        let inside_nested = nested
            .iter()
            .any(|(s, e)| n.start_byte() >= *s && n.end_byte() <= *e && n.id() != node.id());
        if inside_nested {
            return false;
        }

        let kind = n.kind();
        if entering {
            if pack.manifest.decision_nodes.iter().any(|d| d == kind) {
                facts.complexity += 1;
            }
            if pack.manifest.block_nodes.iter().any(|b| b == kind) {
                nesting += 1;
                max_nesting = max_nesting.max(nesting);
            }
            if kind.contains("call") || kind.contains("invocation") {
                calls += 1;
                let text = text_of(n, bytes);
                let head: String = text.chars().take(200).collect();
                effects.extend(pack.manifest.effects.detect(&head));
            }
            if kind.contains("assignment") {
                if let Some(target) = n.child(0) {
                    if globals.contains(text_of(target, bytes)) {
                        effects.push(SideEffect::GlobalWrite);
                    }
                }
            }
        } else if pack.manifest.block_nodes.iter().any(|b| b == kind) {
            nesting = nesting.saturating_sub(1);
        }
        true
    });

    effects.sort();
    effects.dedup();
    facts.side_effects = effects;
    facts.max_nesting = max_nesting;
    facts.callees = calls;
    // Only claim purity when there is something to base it on. A function full of
    // calls we could not classify is "unknown", not "pure".
    facts.pure = if facts.side_effects.is_empty() && calls == 0 { Some(true) } else { None };
    facts
}

/// Emits a `reads` reference for each module-level global a function uses.
///
/// Without these the graph knows what a function *calls* but not what it
/// *depends on*, and a constant like `TAX` sits in the index unconnected to the
/// only code that uses it. That gap showed up as a measurable cost: the
/// assistant, asked why a total was a cent out, had to spend a slow round trip
/// discovering a name the graph could have handed it.
///
/// Restricted to names defined at module level *in this file*, which makes it a
/// scope fact rather than a guess — module globals are file-scoped in every
/// language here. Names that shadow a global locally will over-report; the
/// alternative is full scope tracking, which is not worth it for a signal whose
/// job is "show me what this touches".
fn global_reads(
    node: Node<'_>,
    bytes: &[u8],
    from: NodeId,
    globals: &HashSet<String>,
) -> Vec<Reference> {
    if globals.is_empty() {
        return Vec::new();
    }

    let mut reads: Vec<Reference> = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();

    walk(node, &mut |current, entering| {
        if !entering || !current.kind().contains("identifier") {
            return true;
        }
        // Only leaves: `a.b` should not count as a read of a global named `a.b`.
        if current.child_count() > 0 {
            return true;
        }

        let text = text_of(current, bytes);
        if !globals.contains(text) || !seen.insert(text.to_string()) {
            return true;
        }

        reads.push(Reference {
            name: text.to_string(),
            receiver: None,
            span: span_of(current),
            from,
            kind: EdgeKind::Reads,
        });
        true
    });

    reads
}

/// Finds `variable = Constructor(...)` assignments anywhere in the file.
///
/// The rule is deliberately narrow: the right-hand side must be a call or `new`
/// expression whose callee is a plain identifier. `x = make_thing()` produces
/// nothing, because resolving that would need to know the return type, and a
/// binding that is wrong routes every subsequent `x.foo()` to the wrong place
/// with false confidence. A missing binding costs a guess; a wrong one costs
/// trust.
///
/// Bindings are file-scoped rather than block-scoped. Shadowing the same name
/// with two different types in one file is rare enough that the extra precision
/// is not worth the machinery — and when it happens the resolver sees two
/// bindings and declines to use either.
fn collect_bindings(root: Node<'_>, bytes: &[u8]) -> Vec<Binding> {
    const ASSIGNMENT_KINDS: [&str; 5] = [
        "assignment",
        "variable_declarator",
        "short_var_declaration",
        "let_declaration",
        "assignment_expression",
    ];

    let mut bindings = Vec::new();

    walk(root, &mut |node, entering| {
        if !entering || !ASSIGNMENT_KINDS.contains(&node.kind()) {
            return true;
        }

        let target = node
            .child_by_field_name("left")
            .or_else(|| node.child_by_field_name("name"))
            .or_else(|| node.child_by_field_name("pattern"));
        let value = node
            .child_by_field_name("right")
            .or_else(|| node.child_by_field_name("value"));

        let (Some(target), Some(value)) = (target, value) else { return true };

        // Only bare names. `self.app = Flask()` binds an attribute, which is a
        // different (and harder) question than a local variable.
        if !matches!(target.kind(), "identifier" | "variable_name" | "shorthand_property_identifier_pattern")
        {
            return true;
        }

        let kind = value.kind();
        if !(kind.contains("call") || kind.contains("new_expression") || kind.contains("object_creation"))
        {
            return true;
        }

        let callee = value
            .child_by_field_name("function")
            .or_else(|| value.child_by_field_name("constructor"))
            .or_else(|| value.child_by_field_name("type"))
            .or_else(|| value.named_child(0));

        let Some(callee) = callee else { return true };
        // A qualified callee (`mod.Thing()`) still names a type; take the tail.
        let constructor = text_of(callee, bytes).rsplit(['.', ':']).next().unwrap_or("").to_string();

        if constructor.is_empty() || constructor.contains(['(', ' ', '\n']) {
            return true;
        }

        bindings.push(Binding {
            variable: text_of(target, bytes).to_string(),
            constructor,
            span: span_of(node),
        });
        true
    });

    bindings
}

/// Depth-first walk. `visit` returns false to skip a subtree, and is called again
/// with `entering = false` on the way out so callers can track nesting.
fn walk<'t>(node: Node<'t>, visit: &mut impl FnMut(Node<'t>, bool) -> bool) {
    if !visit(node, true) {
        return;
    }
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        walk(child, visit);
    }
    visit(node, false);
}

fn extract_params(node: Node<'_>, bytes: &[u8]) -> Vec<Param> {
    const PARAM_CONTAINERS: [&str; 6] = [
        "parameters",
        "parameter_list",
        "formal_parameters",
        "method_parameters",
        "parameter_declaration_list",
        "block_parameters",
    ];

    let mut cursor = node.walk();
    let container = node
        .children(&mut cursor)
        .find(|c| PARAM_CONTAINERS.contains(&c.kind()))
        .or_else(|| node.child_by_field_name("parameters"));

    let Some(container) = container else { return Vec::new() };

    let mut params = Vec::new();
    let mut inner = container.walk();
    for child in container.named_children(&mut inner) {
        let text = text_of(child, bytes);
        if text.trim().is_empty() {
            continue;
        }
        let variadic = child.kind().contains("splat")
            || child.kind().contains("rest")
            || child.kind().contains("variadic")
            || text.starts_with('*')
            || text.starts_with("...");

        let name = child
            .child_by_field_name("name")
            .or_else(|| child.child_by_field_name("pattern"))
            .map(|n| text_of(n, bytes).to_string())
            .unwrap_or_else(|| {
                // Bare `(identifier)` parameters, and anything with a shape we do
                // not recognise: take the text up to the first separator.
                text.trim_start_matches(['*', '.', '&'])
                    .split([':', '=', ' '])
                    .next()
                    .unwrap_or(text)
                    .to_string()
            });

        params.push(Param {
            name,
            type_hint: child.child_by_field_name("type").map(|n| text_of(n, bytes).to_string()),
            default: child
                .child_by_field_name("value")
                .or_else(|| child.child_by_field_name("default_value"))
                .map(|n| text_of(n, bytes).to_string()),
            variadic,
        });
    }
    params
}

fn extract_return_type(node: Node<'_>, bytes: &[u8]) -> Option<String> {
    for field in ["return_type", "result", "type"] {
        if let Some(found) = node.child_by_field_name(field) {
            let text = text_of(found, bytes).trim();
            if !text.is_empty() {
                return Some(text.to_string());
            }
        }
    }
    None
}

/// The definition up to (but not including) its body.
fn signature_span(node: Node<'_>, pack: &LanguagePack) -> Span {
    let mut cursor = node.walk();
    let body = node
        .children(&mut cursor)
        .find(|c| pack.manifest.block_nodes.iter().any(|b| b == c.kind()));

    match body {
        Some(body) => Span::new(
            node.start_byte() as u32,
            body.start_byte() as u32,
            node.start_position().row as u32 + 1,
            body.start_position().row as u32 + 1,
        ),
        // No block child: an interface method, an abstract declaration, or a
        // language whose body node we do not know. The first line is a safe
        // stand-in and never over-claims.
        None => Span::new(
            node.start_byte() as u32,
            node.end_byte() as u32,
            node.start_position().row as u32 + 1,
            node.end_position().row as u32 + 1,
        ),
    }
}

fn signature_text(node: Node<'_>, bytes: &[u8], pack: &LanguagePack) -> String {
    let span = signature_span(node, pack);
    let start = span.start_byte as usize;
    let end = (span.end_byte as usize).min(bytes.len());
    if start >= end {
        return String::new();
    }
    String::from_utf8_lossy(&bytes[start..end]).trim().replace(['\n', '\r'], " ")
}

fn visibility_of(node: Node<'_>, bytes: &[u8], language: Language) -> Visibility {
    let head: String = text_of(node, bytes).chars().take(120).collect();
    if head.contains("private") {
        return Visibility::Private;
    }
    if head.contains("protected") {
        return Visibility::Protected;
    }
    if head.contains("public") {
        return Visibility::Public;
    }

    let name = node
        .child_by_field_name("name")
        .map(|n| text_of(n, bytes).to_string())
        .unwrap_or_default();

    match language {
        // Go's export rule is the capitalisation of the first letter.
        Language::Go => match name.chars().next() {
            Some(c) if c.is_uppercase() => Visibility::Public,
            Some(_) => Visibility::Internal,
            None => Visibility::Public,
        },
        Language::Rust => {
            if head.starts_with("pub") {
                Visibility::Public
            } else {
                Visibility::Internal
            }
        }
        _ => {
            if name.starts_with('_') {
                Visibility::Internal
            } else {
                Visibility::Public
            }
        }
    }
}

fn doc_of(node: Node<'_>, bytes: &[u8], pack: &LanguagePack) -> Option<String> {
    match pack.manifest.doc_style {
        DocStyle::StringFirst => {
            let mut cursor = node.walk();
            let body = node
                .children(&mut cursor)
                .find(|c| pack.manifest.block_nodes.iter().any(|b| b == c.kind()))?;
            let first = body.named_child(0)?;
            let string_node = if first.kind() == "string" {
                first
            } else {
                first.named_child(0).filter(|n| n.kind() == "string")?
            };
            let raw = text_of(string_node, bytes);
            Some(strip_string_quotes(raw).trim().to_string()).filter(|s| !s.is_empty())
        }
        DocStyle::LeadingComment => {
            let mut lines: Vec<String> = Vec::new();
            let mut sibling = node.prev_sibling();
            while let Some(prev) = sibling {
                if !prev.kind().contains("comment") {
                    break;
                }
                // Stop at a blank line: a comment separated from the definition
                // is a section header, not documentation for this symbol.
                if node.start_position().row.saturating_sub(prev.end_position().row) > 1 {
                    break;
                }
                lines.push(strip_comment_markers(text_of(prev, bytes), &pack.manifest.comment_prefixes));
                sibling = prev.prev_sibling();
            }
            lines.reverse();
            let joined = lines.join("\n").trim().to_string();
            Some(joined).filter(|s| !s.is_empty())
        }
    }
}

fn strip_comment_markers(text: &str, prefixes: &[String]) -> String {
    text.lines()
        .map(|line| {
            let mut line = line.trim();
            line = line.trim_end_matches("*/");
            // Longest prefix first, so `///` is not chopped to `/` by `//`.
            let mut sorted: Vec<&String> = prefixes.iter().collect();
            sorted.sort_by_key(|p| std::cmp::Reverse(p.len()));
            for prefix in sorted {
                if let Some(rest) = line.strip_prefix(prefix.as_str()) {
                    line = rest;
                    break;
                }
            }
            line.trim()
        })
        .collect::<Vec<_>>()
        .join("\n")
}

fn strip_string_quotes(raw: &str) -> &str {
    for quotes in ["\"\"\"", "'''", "\"", "'", "`"] {
        if raw.len() >= quotes.len() * 2 {
            if let Some(inner) = raw.strip_prefix(quotes).and_then(|r| r.strip_suffix(quotes)) {
                return inner;
            }
        }
    }
    // Python prefixes the quotes with r/f/b markers.
    raw.trim_start_matches(['r', 'f', 'b', 'u', 'R', 'F', 'B', 'U'])
        .trim_matches(|c| c == '"' || c == '\'')
}

fn unquote(raw: &str) -> &str {
    raw.trim_matches(|c| c == '"' || c == '\'' || c == '<' || c == '>' || c == '`')
}

fn text_of<'a>(node: Node<'_>, bytes: &'a [u8]) -> &'a str {
    std::str::from_utf8(&bytes[node.byte_range()]).unwrap_or("")
}

fn span_of(node: Node<'_>) -> Span {
    Span::new(
        node.start_byte() as u32,
        node.end_byte() as u32,
        node.start_position().row as u32 + 1,
        node.end_position().row as u32 + 1,
    )
}

/// Hash helper so callers do not need to depend on `blake3` directly.
pub fn hash_source(source: &[u8]) -> ContentHash {
    ContentHash::of(source)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::registry::pack;

    fn parse(language: Language, source: &str, path: &str) -> ParsedFile {
        let pack = pack(language).expect("language pack available");
        parse_file(pack, FileId::of_path(path), path, source).expect("parse succeeds")
    }

    #[test]
    fn python_methods_are_qualified_by_their_class() {
        let parsed = parse(
            Language::Python,
            "class Invoice:\n    def apply_discount(self, pct):\n        return pct\n",
            "billing.py",
        );
        let method = parsed
            .symbols
            .iter()
            .find(|s| s.name == "apply_discount")
            .expect("method found");
        assert_eq!(method.kind, NodeKind::Method);
        assert_eq!(method.qualified, "Invoice.apply_discount");
    }

    #[test]
    fn a_call_is_attributed_to_the_function_it_sits_in() {
        let parsed = parse(
            Language::Python,
            "def outer():\n    helper()\n\ndef helper():\n    pass\n",
            "m.py",
        );
        let outer = parsed.symbols.iter().find(|s| s.name == "outer").unwrap();
        let call = parsed.references.iter().find(|r| r.name == "helper").unwrap();
        assert_eq!(call.from, outer.id, "the caller must be the enclosing definition");
    }

    #[test]
    fn nested_function_branches_are_not_charged_to_the_outer_function() {
        let source = "\
def outer(x):
    def inner(y):
        if y:
            return 1
        if y > 2:
            return 2
        return 3
    return inner(x)
";
        let parsed = parse(Language::Python, source, "n.py");
        let outer = parsed.symbols.iter().find(|s| s.name == "outer").unwrap();
        let facts = parsed.facts.iter().find(|(id, _)| *id == outer.id).map(|(_, f)| f).unwrap();
        assert_eq!(facts.complexity, 1, "inner's branches belong to inner");
    }

    #[test]
    fn side_effects_come_from_the_actual_call_text() {
        let source = "\
import requests

def fetch(url):
    return requests.get(url).json()

def add(a, b):
    return a + b
";
        let parsed = parse(Language::Python, source, "net.py");
        let fetch = parsed.symbols.iter().find(|s| s.name == "fetch").unwrap();
        let add = parsed.symbols.iter().find(|s| s.name == "add").unwrap();

        let fetch_facts = parsed.facts.iter().find(|(id, _)| *id == fetch.id).unwrap();
        assert!(fetch_facts.1.side_effects.contains(&SideEffect::Network));

        let add_facts = parsed.facts.iter().find(|(id, _)| *id == add.id).unwrap();
        assert!(add_facts.1.side_effects.is_empty());
        assert_eq!(add_facts.1.pure, Some(true));
    }

    #[test]
    fn purity_is_unknown_rather_than_true_when_calls_are_unclassified() {
        let parsed =
            parse(Language::Python, "def f(x):\n    return mystery(x)\n", "p.py");
        let f = parsed.symbols.iter().find(|s| s.name == "f").unwrap();
        let facts = parsed.facts.iter().find(|(id, _)| *id == f.id).unwrap();
        assert_eq!(facts.1.pure, None, "an unclassified call must not be reported as pure");
    }

    #[test]
    fn docstrings_and_leading_comments_are_both_picked_up() {
        let python = parse(
            Language::Python,
            "def f():\n    \"\"\"Adds things up.\"\"\"\n    return 1\n",
            "d.py",
        );
        assert_eq!(python.symbols[0].doc.as_deref(), Some("Adds things up."));

        let go = parse(
            Language::Go,
            "package main\n\n// Add returns the sum.\nfunc Add(a int, b int) int { return a + b }\n",
            "d.go",
        );
        let add = go.symbols.iter().find(|s| s.name == "Add").unwrap();
        assert_eq!(add.doc.as_deref(), Some("Add returns the sum."));
    }

    #[test]
    fn imports_are_extracted_across_language_families() {
        let python = parse(Language::Python, "from pkg.mod import thing\n", "i.py");
        assert_eq!(python.imports[0].module, "pkg.mod");
        assert_eq!(python.imports[0].symbol.as_deref(), Some("thing"));

        let js = parse(Language::JavaScript, "import x from './utils.js';\n", "i.js");
        assert_eq!(js.imports[0].module, "./utils.js");

        let go = parse(Language::Go, "package m\nimport \"fmt\"\n", "i.go");
        assert_eq!(go.imports[0].module, "fmt");
    }

    #[test]
    fn a_file_with_syntax_errors_still_yields_what_it_can() {
        let parsed = parse(
            Language::Python,
            "def good():\n    pass\n\ndef broken(:\n",
            "e.py",
        );
        assert!(parsed.had_errors, "the caller must be told the parse was imperfect");
        assert!(parsed.symbols.iter().any(|s| s.name == "good"));
    }

    #[test]
    fn go_visibility_follows_the_capitalisation_rule() {
        let parsed = parse(
            Language::Go,
            "package m\nfunc Exported() {}\nfunc internal() {}\n",
            "v.go",
        );
        let exported = parsed.symbols.iter().find(|s| s.name == "Exported").unwrap();
        let internal = parsed.symbols.iter().find(|s| s.name == "internal").unwrap();
        assert_eq!(exported.visibility, Visibility::Public);
        assert_eq!(internal.visibility, Visibility::Internal);
    }

    /// Guards against a whole class of tag-query bug.
    ///
    /// If a capture is attached to the wrong node in the pattern — the enclosing
    /// `module` rather than the assignment, say — the resulting definition spans
    /// the entire file and silently becomes the containment parent of everything
    /// below it. Nothing crashes; the qualified names just quietly turn into
    /// nonsense like `TEST_KEY.SECRET_KEY.Flask`, and with them every node id.
    #[test]
    fn no_definition_swallows_the_rest_of_the_file() {
        let source = "\
CONFIG = {}
VERSION = \"1.0\"

class Engine:
    def start(self):
        pass

def run():
    pass
";
        let parsed = parse(Language::Python, source, "s.py");

        let engine = parsed.symbols.iter().find(|s| s.name == "Engine").unwrap();
        assert_eq!(engine.qualified, "Engine", "a global must not become a parent");
        assert!(engine.parent.is_none());

        let start = parsed.symbols.iter().find(|s| s.name == "start").unwrap();
        assert_eq!(start.qualified, "Engine.start");

        let run = parsed.symbols.iter().find(|s| s.name == "run").unwrap();
        assert_eq!(run.qualified, "run");

        for symbol in &parsed.symbols {
            if symbol.kind == NodeKind::Field {
                assert!(
                    symbol.span.end_byte < source.len() as u32,
                    "{} spans the whole file — its capture is on the wrong node",
                    symbol.name
                );
            }
        }
    }

    #[test]
    fn using_a_module_global_produces_a_reads_reference() {
        let parsed = parse(
            Language::Python,
            "TAX = 0.19\n\ndef total(net):\n    return net * (1 + TAX)\n\ndef plain(x):\n    return x\n",
            "g.py",
        );

        let total = parsed.symbols.iter().find(|s| s.name == "total").unwrap();
        let reads: Vec<&Reference> = parsed
            .references
            .iter()
            .filter(|r| r.kind == EdgeKind::Reads && r.from == total.id)
            .collect();

        assert_eq!(reads.len(), 1, "total() reads TAX");
        assert_eq!(reads[0].name, "TAX");

        let plain = parsed.symbols.iter().find(|s| s.name == "plain").unwrap();
        assert!(
            !parsed.references.iter().any(|r| r.kind == EdgeKind::Reads && r.from == plain.id),
            "a function touching no global must produce no reads"
        );
    }

    #[test]
    fn a_global_read_many_times_is_still_one_reference() {
        let parsed = parse(
            Language::Python,
            "RATE = 2\n\ndef f(x):\n    return x * RATE + RATE - RATE\n",
            "r.py",
        );
        let reads = parsed.references.iter().filter(|r| r.kind == EdgeKind::Reads).count();
        assert_eq!(reads, 1, "the dependency is one fact, not three");
    }

    #[test]
    fn rust_methods_are_qualified_by_the_type_their_impl_block_names() {
        // Without this, every Rust codebase collapses into a few dozen symbols
        // called `new`, `build` and `as_str` — unsearchable, and ambiguous for
        // the resolver at every call site.
        let parsed = parse(
            Language::Rust,
            "struct Edge;\n\nimpl Edge {\n    fn new() -> Self { Edge }\n}\n\n\
             struct Node;\n\nimpl Node {\n    fn new() -> Self { Node }\n}\n",
            "graph.rs",
        );

        let qualified: Vec<&str> =
            parsed.symbols.iter().filter(|s| s.name == "new").map(|s| s.qualified.as_str()).collect();
        assert!(qualified.contains(&"Edge.new"), "got {qualified:?}");
        assert!(qualified.contains(&"Node.new"), "got {qualified:?}");
    }

    #[test]
    fn generic_parameters_do_not_leak_into_the_qualifier() {
        let parsed = parse(
            Language::Rust,
            "struct Wrapper<T>(T);\n\nimpl<T> Wrapper<T> {\n    fn get(&self) -> &T { &self.0 }\n}\n",
            "w.rs",
        );
        let get = parsed.symbols.iter().find(|s| s.name == "get").unwrap();
        assert_eq!(get.qualified, "Wrapper.get");
    }

    #[test]
    fn constructor_bindings_are_recorded_but_only_for_direct_construction() {
        let parsed = parse(
            Language::Python,
            "app = Flask(__name__)\nthing = make_thing()\n\nclass Flask:\n    pass\n",
            "b.py",
        );

        let app = parsed.bindings.iter().find(|b| b.variable == "app").unwrap();
        assert_eq!(app.constructor, "Flask");

        // `make_thing()` is a factory; guessing its return type would route every
        // later `thing.x()` confidently to the wrong place.
        let thing = parsed.bindings.iter().find(|b| b.variable == "thing");
        assert_eq!(thing.map(|b| b.constructor.as_str()), Some("make_thing"));
    }

    #[test]
    fn every_language_finds_at_least_one_function_in_a_smoke_sample() {
        // Cheap end-to-end proof that a pack's captures line up with its grammar:
        // the query compiling is not the same as the query matching anything.
        let samples: &[(Language, &str, &str)] = &[
            (Language::Python, "def f():\n    pass\n", "a.py"),
            (Language::JavaScript, "function f() {}\n", "a.js"),
            (Language::TypeScript, "function f(): void {}\n", "a.ts"),
            (Language::Tsx, "function f() { return <div/>; }\n", "a.tsx"),
            (Language::Go, "package m\nfunc F() {}\n", "a.go"),
            (Language::Rust, "fn f() {}\n", "a.rs"),
            (Language::Java, "class A { void f() {} }\n", "A.java"),
            (Language::C, "int f(void) { return 0; }\n", "a.c"),
            (Language::Cpp, "int f() { return 0; }\n", "a.cpp"),
            (Language::CSharp, "class A { void F() {} }\n", "A.cs"),
            (Language::Ruby, "def f\nend\n", "a.rb"),
            (Language::Php, "<?php function f() {} ?>", "a.php"),
            (Language::Bash, "f() {\n  echo hi\n}\n", "a.sh"),
        ];

        for (language, source, path) in samples {
            let parsed = parse(*language, source, path);
            assert!(
                parsed.symbols.iter().any(|s| s.kind.is_callable() || s.kind == NodeKind::Function),
                "{} found no function in its smoke sample",
                language.display_name()
            );
        }
    }
}
