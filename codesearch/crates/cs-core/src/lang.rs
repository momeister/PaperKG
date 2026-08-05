//! The languages the index can parse.
//!
//! Adding a language is meant to be data, not code: a grammar entry here, a
//! `tags.scm` and a `manifest.toml` under `languages/`. Nothing in the resolver,
//! the ranking or the UI knows about specific languages.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Language {
    Python,
    JavaScript,
    TypeScript,
    Tsx,
    Go,
    Rust,
    Java,
    C,
    Cpp,
    CSharp,
    Ruby,
    Php,
    Bash,
}

impl Language {
    pub const ALL: &'static [Language] = &[
        Language::Python,
        Language::JavaScript,
        Language::TypeScript,
        Language::Tsx,
        Language::Go,
        Language::Rust,
        Language::Java,
        Language::C,
        Language::Cpp,
        Language::CSharp,
        Language::Ruby,
        Language::Php,
        Language::Bash,
    ];

    /// The directory name under `languages/` holding this language's pack.
    pub fn slug(self) -> &'static str {
        match self {
            Language::Python => "python",
            Language::JavaScript => "javascript",
            Language::TypeScript => "typescript",
            Language::Tsx => "tsx",
            Language::Go => "go",
            Language::Rust => "rust",
            Language::Java => "java",
            Language::C => "c",
            Language::Cpp => "cpp",
            Language::CSharp => "csharp",
            Language::Ruby => "ruby",
            Language::Php => "php",
            Language::Bash => "bash",
        }
    }

    pub fn display_name(self) -> &'static str {
        match self {
            Language::Python => "Python",
            Language::JavaScript => "JavaScript",
            Language::TypeScript => "TypeScript",
            Language::Tsx => "TSX",
            Language::Go => "Go",
            Language::Rust => "Rust",
            Language::Java => "Java",
            Language::C => "C",
            Language::Cpp => "C++",
            Language::CSharp => "C#",
            Language::Ruby => "Ruby",
            Language::Php => "PHP",
            Language::Bash => "Bash",
        }
    }

    pub fn from_slug(slug: &str) -> Option<Self> {
        Language::ALL.iter().copied().find(|l| l.slug() == slug)
    }

    /// Detection by extension. `.h` is deliberately C rather than C++: the C
    /// grammar parses the common subset of both without choking, whereas the C++
    /// grammar mis-parses plain C headers more often than the reverse.
    pub fn from_extension(ext: &str) -> Option<Self> {
        Some(match ext {
            "py" | "pyi" | "pyw" => Language::Python,
            "js" | "mjs" | "cjs" | "jsx" => Language::JavaScript,
            "ts" | "mts" | "cts" => Language::TypeScript,
            "tsx" => Language::Tsx,
            "go" => Language::Go,
            "rs" => Language::Rust,
            "java" => Language::Java,
            "c" | "h" => Language::C,
            "cc" | "cpp" | "cxx" | "hpp" | "hh" | "hxx" => Language::Cpp,
            "cs" => Language::CSharp,
            "rb" | "rake" | "gemspec" => Language::Ruby,
            "php" | "phtml" => Language::Php,
            "sh" | "bash" | "zsh" => Language::Bash,
            _ => return None,
        })
    }

    /// Files with no extension that are still worth parsing.
    pub fn from_filename(name: &str) -> Option<Self> {
        Some(match name {
            "Rakefile" | "Gemfile" | "Guardfile" => Language::Ruby,
            "Makefile" | "makefile" => return None,
            _ => return None,
        })
    }
}
