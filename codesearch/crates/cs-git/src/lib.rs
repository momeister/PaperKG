//! History as a relevance signal.
//!
//! Structure tells you what the code *can* reach; history tells you what people
//! actually keep touching. A function nobody has edited in four years and a
//! function edited in nine of the last twenty commits are very different things,
//! and only one of them is where your bug probably lives.
//!
//! Signals are per file rather than per symbol. Symbol-level attribution needs a
//! line-history walk (`git log -L`) whose cost grows with history length, and it
//! would be the wrong trade for a background pass that has to stay out of the
//! way. The UI labels the churn figure as file-level so the number is not read as
//! something it is not.

use anyhow::Result;
use serde::Serialize;
use std::collections::{HashMap, HashSet};

/// How far back to walk. Older history says little about today's code, and an
/// unbounded walk on a repository like the kernel would run for minutes.
const MAX_COMMITS: usize = 3_000;

/// Commit subjects that mean something was broken here.
const FIX_MARKERS: &[&str] = &[
    "fix", "bug", "hotfix", "patch", "revert", "regression", "crash", "broken", "fehler", "behebt",
];

#[derive(Debug, Clone, Default, Serialize)]
pub struct FileHistory {
    /// Commits touching this file within the walked window.
    pub churn: u32,
    /// How many of those looked like repairs.
    pub risk: u32,
    pub authors: u32,
    /// Unix seconds of the most recent commit touching it.
    pub last_touched: Option<i64>,
}

#[derive(Debug, Clone, Default, Serialize)]
pub struct HistoryReport {
    pub by_path: HashMap<String, FileHistory>,
    /// Commits actually examined. Shown in the UI so "9 Änderungen" is read as
    /// "9 of the last 3000 commits", not "9 ever".
    pub commits_walked: usize,
    /// True when the window cut history short.
    pub truncated: bool,
}

/// Collects per-file history for the repository at `root`.
///
/// Returns an empty report rather than an error when `root` is not a git
/// repository: plenty of code worth exploring is not under version control, and
/// losing one ranking signal is not a reason to refuse to index.
pub fn collect(root: &std::path::Path) -> Result<HistoryReport> {
    let repo = match gix::open(root) {
        Ok(repo) => repo,
        Err(err) => {
            tracing::info!(error = %err, "not a git repository, skipping history signals");
            return Ok(HistoryReport::default());
        }
    };

    let Ok(head) = repo.head_commit() else {
        // A repository with no commits yet.
        return Ok(HistoryReport::default());
    };

    let mut report = HistoryReport::default();
    let mut authors_per_path: HashMap<String, HashSet<String>> = HashMap::new();

    let walk = head.ancestors().all()?;
    for info in walk.take(MAX_COMMITS) {
        let Ok(info) = info else { continue };
        let Ok(commit) = repo.find_commit(info.id) else { continue };

        report.commits_walked += 1;

        let message = commit.message_raw_sloppy().to_string().to_lowercase();
        let is_fix = FIX_MARKERS.iter().any(|marker| message.contains(marker));

        let author = commit
            .author()
            .map(|a| a.email.to_string())
            .unwrap_or_default();
        let time = commit.time().ok().map(|t| t.seconds);

        for path in changed_paths(&repo, &commit) {
            let entry = report.by_path.entry(path.clone()).or_default();
            entry.churn += 1;
            if is_fix {
                entry.risk += 1;
            }
            // Ancestors are walked newest first, so the first time we see a path
            // is its most recent change.
            if entry.last_touched.is_none() {
                entry.last_touched = time;
            }
            if !author.is_empty() {
                authors_per_path.entry(path).or_default().insert(author.clone());
            }
        }
    }

    report.truncated = report.commits_walked >= MAX_COMMITS;
    for (path, authors) in authors_per_path {
        if let Some(entry) = report.by_path.get_mut(&path) {
            entry.authors = authors.len() as u32;
        }
    }

    Ok(report)
}

/// Paths changed by a commit relative to its first parent.
///
/// Merges are compared against the first parent only. Comparing against every
/// parent would count each side of a merge twice and make merge-heavy repos look
/// uniformly churned.
fn changed_paths(repo: &gix::Repository, commit: &gix::Commit<'_>) -> Vec<String> {
    let Ok(tree) = commit.tree() else { return Vec::new() };

    let parent_tree = commit
        .parent_ids()
        .next()
        .and_then(|id| repo.find_commit(id).ok())
        .and_then(|parent| parent.tree().ok());

    let mut paths = Vec::new();

    let Some(parent_tree) = parent_tree else {
        // The root commit: everything in it is new.
        if let Ok(mut recorder) = tree.traverse().breadthfirst.files() {
            for entry in recorder.drain(..) {
                if entry.mode.is_blob() {
                    paths.push(entry.filepath.to_string());
                }
            }
        }
        return paths;
    };

    let mut changes = match parent_tree.changes() {
        Ok(changes) => changes,
        Err(_) => return paths,
    };

    let _ = changes.for_each_to_obtain_tree(&tree, |change| {
        if change.entry_mode().is_blob() {
            paths.push(change.location().to_string());
        }
        Ok::<_, std::convert::Infallible>(gix::object::tree::diff::Action::Continue(()))
    });

    paths
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_directory_without_git_yields_an_empty_report_not_an_error() {
        let dir = tempfile::tempdir().unwrap();
        let report = collect(dir.path()).expect("missing git must not fail indexing");
        assert!(report.by_path.is_empty());
        assert_eq!(report.commits_walked, 0);
    }

    #[test]
    fn fix_markers_are_matched_case_insensitively_in_both_languages() {
        for subject in ["Fix rounding", "BUGFIX: totals", "behebt Rundungsfehler"] {
            let lower = subject.to_lowercase();
            assert!(
                FIX_MARKERS.iter().any(|m| lower.contains(m)),
                "{subject} should count as a repair"
            );
        }
        assert!(!FIX_MARKERS.iter().any(|m| "add new endpoint".contains(m)));
    }
}
