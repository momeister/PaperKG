//! Turning an import string into the file it refers to.
//!
//! Every language spells this differently — `pkg.mod`, `./utils`,
//! `github.com/org/repo/pkg`, `crate::a::b`, `com.example.Thing`, `"header.h"` —
//! but they all end up as a path suffix. Producing candidate suffixes per
//! language and looking them up in one index keeps thirteen conventions in one
//! readable function instead of thirteen resolvers.
//!
//! An import that matches no file is not a failure: it is a third-party package,
//! which is worth its own node in the graph.

use cs_core::Language;
use std::collections::HashMap;

/// Index from path suffix to file path, so `pkg/mod.py` finds
/// `src/app/pkg/mod.py`.
///
/// Built once per index run. Ambiguity is kept rather than resolved: two files
/// ending in the same suffix mean the import genuinely could be either, and the
/// caller downgrades confidence accordingly.
pub struct PathIndex {
    by_suffix: HashMap<String, Vec<String>>,
}

impl PathIndex {
    pub fn build(paths: &[String]) -> Self {
        let mut by_suffix: HashMap<String, Vec<String>> = HashMap::new();
        for path in paths {
            // Index every trailing path fragment: `a/b/c.py` registers under
            // `c.py`, `b/c.py` and `a/b/c.py`.
            let parts: Vec<&str> = path.split('/').collect();
            for start in 0..parts.len() {
                let suffix = parts[start..].join("/");
                by_suffix.entry(suffix).or_default().push(path.clone());
            }
        }
        Self { by_suffix }
    }

    fn lookup(&self, suffix: &str) -> &[String] {
        self.by_suffix.get(suffix).map(|v| v.as_slice()).unwrap_or(&[])
    }

    /// Resolves `module` as imported from `from_path`.
    ///
    /// Returns every file it could mean, best first. An empty result means the
    /// import points outside the workspace.
    pub fn resolve(&self, module: &str, language: Language, from_path: &str) -> Vec<String> {
        let from_dir = from_path.rsplit_once('/').map(|(dir, _)| dir).unwrap_or("");

        for candidate in candidates(module, language, from_dir) {
            let hits = self.lookup(&candidate);
            if !hits.is_empty() {
                let mut hits = hits.to_vec();
                // Prefer a file nearer the importer: in a monorepo the same
                // module name genuinely exists in several packages, and the local
                // one is nearly always the intended one.
                hits.sort_by_key(|p| shared_prefix_len(p, from_path));
                hits.reverse();
                return hits;
            }
        }
        Vec::new()
    }
}

fn shared_prefix_len(a: &str, b: &str) -> usize {
    a.split('/').zip(b.split('/')).take_while(|(x, y)| x == y).count()
}

/// Candidate path suffixes for an import, most specific first.
fn candidates(module: &str, language: Language, from_dir: &str) -> Vec<String> {
    let module = module.trim();
    if module.is_empty() {
        return Vec::new();
    }

    match language {
        Language::Python => python_candidates(module, from_dir),
        Language::JavaScript | Language::TypeScript | Language::Tsx => {
            js_candidates(module, from_dir)
        }
        Language::Go => {
            // Import paths are URLs; only the tail can match a local directory.
            let tail = module.rsplit('/').next().unwrap_or(module);
            vec![format!("{tail}.go"), tail.to_string()]
        }
        Language::Rust => {
            let path = module
                .trim_start_matches("crate::")
                .trim_start_matches("self::")
                .trim_start_matches("super::")
                .replace("::", "/");
            // Only the module part matters; the last segment is usually an item.
            let parent = path.rsplit_once('/').map(|(p, _)| p).unwrap_or(&path);
            vec![
                format!("{path}.rs"),
                format!("{path}/mod.rs"),
                format!("{parent}.rs"),
                format!("{parent}/mod.rs"),
            ]
        }
        Language::Java => vec![format!("{}.java", module.replace('.', "/"))],
        Language::CSharp => vec![format!("{}.cs", module.replace('.', "/"))],
        Language::C | Language::Cpp => vec![module.trim_matches(['"', '<', '>']).to_string()],
        Language::Ruby => {
            let base = module.trim_end_matches(".rb");
            let mut out = vec![format!("{base}.rb")];
            if !from_dir.is_empty() {
                out.insert(0, join(from_dir, &format!("{base}.rb")));
            }
            out
        }
        Language::Php => {
            let path = module.trim_start_matches('\\').replace('\\', "/");
            vec![format!("{path}.php"), path]
        }
        Language::Bash => {
            let base = module.trim_start_matches("./");
            let mut out = vec![base.to_string()];
            if !from_dir.is_empty() {
                out.insert(0, join(from_dir, base));
            }
            out
        }
    }
}

fn python_candidates(module: &str, from_dir: &str) -> Vec<String> {
    // Leading dots are relative levels: `.` is this package, `..` the parent.
    let dots = module.chars().take_while(|c| *c == '.').count();
    let rest = module.trim_start_matches('.');
    let path = rest.replace('.', "/");

    if dots > 0 {
        let mut base = from_dir.to_string();
        for _ in 1..dots {
            base = base.rsplit_once('/').map(|(p, _)| p.to_string()).unwrap_or_default();
        }
        let joined = if path.is_empty() { base.clone() } else { join(&base, &path) };
        return vec![format!("{joined}.py"), format!("{joined}/__init__.py")];
    }

    let mut out = vec![format!("{path}.py"), format!("{path}/__init__.py")];
    // `from pkg.mod import thing` also matches `pkg/mod/thing.py` when `thing`
    // happens to be a submodule rather than a name.
    if let Some((parent, _)) = path.rsplit_once('/') {
        out.push(format!("{parent}.py"));
        out.push(format!("{parent}/__init__.py"));
    }
    out
}

fn js_candidates(module: &str, from_dir: &str) -> Vec<String> {
    const EXTENSIONS: [&str; 6] = ["ts", "tsx", "js", "jsx", "mjs", "cjs"];

    let relative = module.starts_with('.');
    let cleaned = module.trim_start_matches("./");

    let base = if relative && !from_dir.is_empty() {
        let mut dir = from_dir.to_string();
        let mut rest = cleaned;
        while let Some(stripped) = rest.strip_prefix("../") {
            dir = dir.rsplit_once('/').map(|(p, _)| p.to_string()).unwrap_or_default();
            rest = stripped;
        }
        join(&dir, rest)
    } else {
        // A bare specifier is usually a package, but path aliases like
        // `@/components/Button` are common enough to be worth trying.
        cleaned.trim_start_matches('@').trim_start_matches('~').trim_start_matches('/').to_string()
    };

    let already_has_extension = EXTENSIONS.iter().any(|e| base.ends_with(&format!(".{e}")));
    if already_has_extension {
        return vec![base];
    }

    let mut out = Vec::with_capacity(EXTENSIONS.len() * 2);
    for ext in EXTENSIONS {
        out.push(format!("{base}.{ext}"));
    }
    for ext in EXTENSIONS {
        out.push(format!("{base}/index.{ext}"));
    }
    out
}

fn join(dir: &str, rest: &str) -> String {
    if dir.is_empty() {
        rest.to_string()
    } else {
        format!("{dir}/{rest}")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn index(paths: &[&str]) -> PathIndex {
        PathIndex::build(&paths.iter().map(|s| s.to_string()).collect::<Vec<_>>())
    }

    #[test]
    fn python_packages_and_relative_imports_both_resolve() {
        let idx = index(&["src/pkg/mod.py", "src/pkg/__init__.py", "src/pkg/sibling.py"]);

        assert_eq!(idx.resolve("pkg.mod", Language::Python, "src/app.py"), vec!["src/pkg/mod.py"]);
        assert_eq!(
            idx.resolve(".sibling", Language::Python, "src/pkg/mod.py"),
            vec!["src/pkg/sibling.py"]
        );
    }

    #[test]
    fn javascript_extension_guessing_covers_index_files() {
        let idx = index(&["web/src/utils.ts", "web/src/widgets/index.tsx"]);

        assert_eq!(
            idx.resolve("./utils", Language::TypeScript, "web/src/app.ts"),
            vec!["web/src/utils.ts"]
        );
        assert_eq!(
            idx.resolve("./widgets", Language::Tsx, "web/src/app.tsx"),
            vec!["web/src/widgets/index.tsx"]
        );
    }

    #[test]
    fn parent_relative_imports_walk_up_the_tree() {
        let idx = index(&["web/lib/format.ts", "web/src/deep/app.ts"]);
        assert_eq!(
            idx.resolve("../../lib/format", Language::TypeScript, "web/src/deep/app.ts"),
            vec!["web/lib/format.ts"]
        );
    }

    #[test]
    fn an_import_that_leaves_the_workspace_resolves_to_nothing() {
        let idx = index(&["src/main.py"]);
        // Correct behaviour: the caller turns this into an external package node
        // rather than pretending it found a local file.
        assert!(idx.resolve("requests", Language::Python, "src/main.py").is_empty());
    }

    #[test]
    fn the_nearest_match_wins_in_a_monorepo() {
        let idx = index(&["services/a/pkg/mod.py", "services/b/pkg/mod.py"]);
        let hits = idx.resolve("pkg.mod", Language::Python, "services/b/main.py");
        assert_eq!(hits.first().map(String::as_str), Some("services/b/pkg/mod.py"));
        assert_eq!(hits.len(), 2, "the other candidate stays visible as ambiguity");
    }
}
