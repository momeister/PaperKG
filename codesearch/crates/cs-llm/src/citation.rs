//! Checking what the model claims against what it was actually shown.
//!
//! Asking a model to cite its sources is not a safeguard — models produce
//! well-formed citations to files that do not exist with complete confidence.
//! The safeguard is checking them, mechanically, before the answer is rendered:
//!
//! 1. **Does the file exist** in the index?
//! 2. **Was this range retrieved** during this conversation? A citation to code
//!    the model never received is a fabrication regardless of how right it looks.
//! 3. **Is the file unchanged** since it was read? A correct line number in a
//!    file that has since moved on is a wrong answer with a true-looking address.
//! 4. **Does a verbatim quote match the bytes?** One altered character means the
//!    quote was reconstructed from memory rather than copied.
//!
//! What fails is labelled, not hidden. A marked-unverifiable sentence still
//! carries information — it tells you exactly which part of the answer to go and
//! check yourself.

use crate::tools::Session;
use cs_core::{ContentHash, FileId};
use cs_graph::{Graph, SpanRead};
use serde::Serialize;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CitationStatus {
    /// The range exists, was retrieved in this conversation, and the file is
    /// unchanged.
    Verified,
    /// The file is real but this range was never handed to the model.
    NotRetrieved,
    /// The file changed since indexing; the line numbers no longer mean anything.
    Stale,
    /// No such file in the index.
    UnknownFile,
}

impl CitationStatus {
    pub fn label(self) -> &'static str {
        match self {
            CitationStatus::Verified => "belegt",
            CitationStatus::NotRetrieved => "nicht nachgeschlagen",
            CitationStatus::Stale => "Datei geändert",
            CitationStatus::UnknownFile => "Datei unbekannt",
        }
    }

    pub fn is_trustworthy(self) -> bool {
        self == CitationStatus::Verified
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct Citation {
    pub path: String,
    pub from_line: u32,
    pub to_line: u32,
    pub status: CitationStatus,
    /// Byte offsets of the citation inside the answer text, so the UI can turn
    /// exactly that run of characters into a chip.
    pub start: usize,
    pub end: usize,
}

#[derive(Debug, Clone, Serialize)]
pub struct VerifiedAnswer {
    pub text: String,
    pub citations: Vec<Citation>,
    /// Fenced code blocks whose contents do not appear in the cited file.
    pub quote_mismatches: Vec<String>,
    /// Sentences that make a claim without pointing at anything.
    pub uncited_sentences: usize,
}

impl VerifiedAnswer {
    /// Whether every claim that *was* cited holds up.
    ///
    /// Deliberately not the same question as "is this answer trustworthy". An
    /// answer with no citations at all passes this vacuously, which is why
    /// [`Self::verdict`] exists and why callers should use that instead when
    /// reporting to a person.
    pub fn is_clean(&self) -> bool {
        self.quote_mismatches.is_empty()
            && self.citations.iter().all(|citation| citation.status.is_trustworthy())
    }

    /// What to actually tell the user.
    pub fn verdict(&self) -> Verdict {
        if !self.quote_mismatches.is_empty()
            || self.citations.iter().any(|c| !c.status.is_trustworthy())
        {
            Verdict::Broken
        } else if self.citations.is_empty() {
            Verdict::Uncited
        } else {
            Verdict::Sound
        }
    }
}

/// The three honest outcomes of checking an answer.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Verdict {
    /// Every citation was retrieved, current, and quoted accurately.
    Sound,
    /// Nothing was cited, so there was nothing to check. Not the same as correct —
    /// reporting this as "all citations hold" would be a lie of omission.
    Uncited,
    /// At least one citation or quotation does not hold.
    Broken,
}

impl Verdict {
    pub fn label(self) -> &'static str {
        match self {
            Verdict::Sound => "Alle Belege halten.",
            Verdict::Uncited => {
                "Die Antwort nennt keine Belege — es gibt also nichts zu prüfen. \
                 Die Spur zeigt, wo du selbst nachsehen kannst."
            }
            Verdict::Broken => "Mindestens ein Beleg hält nicht.",
        }
    }
}

/// Verifies every citation and quotation in a model answer.
pub fn verify(graph: &Graph, session: &Session, text: &str) -> VerifiedAnswer {
    let mut citations: Vec<Citation> = find_citations(text)
        .into_iter()
        .map(|found| {
            let status = check(graph, session, &found);
            Citation {
                path: found.path,
                from_line: found.from_line,
                to_line: found.to_line,
                status,
                start: found.start,
                end: found.end,
            }
        })
        .collect();
    citations.sort_by_key(|citation| citation.start);

    let quote_mismatches = check_quotes(graph, text, &citations);
    let uncited_sentences = count_uncited_claims(text, &citations);

    VerifiedAnswer { text: text.to_string(), citations, quote_mismatches, uncited_sentences }
}

struct Found {
    path: String,
    from_line: u32,
    to_line: u32,
    start: usize,
    end: usize,
}

fn check(graph: &Graph, session: &Session, found: &Found) -> CitationStatus {
    let file = FileId::of_path(&found.path);

    // A single line still needs a span to look up; the width does not matter
    // because only the hash comparison is used from the result.
    let probe = cs_core::Span::new(0, u32::MAX, found.from_line, found.to_line);
    match graph.read_span(file, probe) {
        Ok(SpanRead::Text { .. }) => {
            if session.covers(&found.path, found.from_line, found.to_line) {
                CitationStatus::Verified
            } else {
                CitationStatus::NotRetrieved
            }
        }
        Ok(SpanRead::Stale { .. }) => CitationStatus::Stale,
        // OutOfRange here means the file is known but empty at that offset.
        Ok(SpanRead::OutOfRange { .. }) => CitationStatus::NotRetrieved,
        Err(_) => CitationStatus::UnknownFile,
    }
}

/// Finds `path.ext:12` and `path.ext:12-30` anywhere in the text.
///
/// Written as a scanner rather than a regex so the crate keeps one fewer
/// dependency, and because the grammar is small enough to read.
fn find_citations(text: &str) -> Vec<Found> {
    let bytes = text.as_bytes();
    let mut found = Vec::new();
    let mut index = 0usize;

    while index < bytes.len() {
        if bytes[index] != b':' {
            index += 1;
            continue;
        }

        // Walk backwards over the path.
        let mut path_start = index;
        while path_start > 0 && is_path_byte(bytes[path_start - 1]) {
            path_start -= 1;
        }
        let path = &text[path_start..index];

        // A path needs an extension; otherwise "Hinweis: 12" would parse as one.
        if path.is_empty() || !path.contains('.') || path.ends_with('.') {
            index += 1;
            continue;
        }

        let mut cursor = index + 1;
        let from_start = cursor;
        while cursor < bytes.len() && bytes[cursor].is_ascii_digit() {
            cursor += 1;
        }
        if cursor == from_start {
            index += 1;
            continue;
        }
        let from_line: u32 = text[from_start..cursor].parse().unwrap_or(0);

        let mut to_line = from_line;
        // Plain hyphen only. An en dash is three bytes in UTF-8 and models write
        // it in prose, not inside a line range.
        if cursor < bytes.len() && bytes[cursor] == b'-' {
            let range_start = cursor + 1;
            let mut range_end = range_start;
            while range_end < bytes.len() && bytes[range_end].is_ascii_digit() {
                range_end += 1;
            }
            if range_end > range_start {
                to_line = text[range_start..range_end].parse().unwrap_or(from_line);
                cursor = range_end;
            }
        }

        if from_line > 0 {
            found.push(Found {
                path: path.to_string(),
                from_line,
                to_line: to_line.max(from_line),
                start: path_start,
                end: cursor,
            });
        }
        index = cursor.max(index + 1);
    }

    found
}

fn is_path_byte(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || matches!(byte, b'/' | b'.' | b'_' | b'-' | b'\\')
}

/// Compares fenced code blocks against the files cited nearby.
///
/// A quotation that has been silently "tidied up" is the most convincing kind of
/// wrong answer there is, because it reads exactly like code that exists.
fn check_quotes(graph: &Graph, text: &str, citations: &[Citation]) -> Vec<String> {
    let mut mismatches = Vec::new();

    for block in fenced_blocks(text) {
        let meaningful: Vec<&str> = block
            .lines()
            .map(str::trim)
            .filter(|line| line.len() > 12 && !line.starts_with("//") && !line.starts_with('#'))
            .collect();
        if meaningful.is_empty() {
            continue;
        }

        // Compare against every verified file mentioned in the answer: models do
        // not reliably place the citation next to the block it belongs to.
        let sources: Vec<String> = citations
            .iter()
            .filter(|citation| citation.status.is_trustworthy())
            .filter_map(|citation| {
                std::fs::read_to_string(graph.root().join(&citation.path)).ok()
            })
            .collect();

        if sources.is_empty() {
            continue;
        }

        for line in meaningful {
            let appears = sources.iter().any(|source| source.contains(line));
            if !appears {
                mismatches.push(line.to_string());
            }
        }
    }

    mismatches
}

fn fenced_blocks(text: &str) -> Vec<String> {
    let mut blocks = Vec::new();
    let mut current: Option<Vec<&str>> = None;

    for line in text.lines() {
        if line.trim_start().starts_with("```") {
            match current.take() {
                Some(collected) => blocks.push(collected.join("\n")),
                None => current = Some(Vec::new()),
            }
            continue;
        }
        if let Some(collected) = current.as_mut() {
            collected.push(line);
        }
    }

    blocks
}

/// Counts sentences that assert something without pointing anywhere.
///
/// Deliberately crude and used only as a signal in the UI ("3 Sätze ohne Beleg"),
/// never to reject an answer: plenty of legitimate sentences carry no citation
/// because they are transitions rather than claims.
fn count_uncited_claims(text: &str, citations: &[Citation]) -> usize {
    // Positions covered by any citation.
    let cited_ranges: Vec<(usize, usize)> =
        citations.iter().map(|citation| (citation.start, citation.end)).collect();

    let mut uncited = 0;
    let mut sentence_start = 0usize;

    for (offset, character) in text.char_indices() {
        if !matches!(character, '.' | '!' | '?' | '\n') {
            continue;
        }
        let sentence = &text[sentence_start..offset];
        let has_citation = cited_ranges
            .iter()
            .any(|(start, end)| *start >= sentence_start && *end <= offset + 1);

        // Only sentences long enough to be making a claim.
        if !has_citation && sentence.split_whitespace().count() >= 6 {
            uncited += 1;
        }
        sentence_start = offset + character.len_utf8();
    }

    uncited
}

/// Hash of a file as it is on disk right now. Used by the UI to decide whether a
/// stored citation is still live.
pub fn current_hash(graph: &Graph, path: &str) -> Option<ContentHash> {
    std::fs::read(graph.root().join(path)).ok().map(|bytes| ContentHash::of(&bytes))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn citations_are_found_in_running_text() {
        let found = find_citations("Der Wert wird in src/pricing.py:31 gerundet, siehe auch a/b.rs:10-24.");
        assert_eq!(found.len(), 2);
        assert_eq!(found[0].path, "src/pricing.py");
        assert_eq!(found[0].from_line, 31);
        assert_eq!(found[0].to_line, 31);
        assert_eq!(found[1].path, "a/b.rs");
        assert_eq!(found[1].from_line, 10);
        assert_eq!(found[1].to_line, 24);
    }

    #[test]
    fn plain_numbers_are_not_mistaken_for_citations() {
        // "Hinweis: 12" and clock times must not become file references.
        assert!(find_citations("Hinweis: 12 Aufrufer").is_empty());
        assert!(find_citations("um 14:30 Uhr").is_empty());
        assert!(find_citations("Verhältnis 3:1").is_empty());
    }

    #[test]
    fn fenced_blocks_are_extracted_for_quote_checking() {
        let blocks = fenced_blocks("Text\n```python\ndef f():\n    pass\n```\nmehr");
        assert_eq!(blocks.len(), 1);
        assert!(blocks[0].contains("def f():"));
    }

    #[test]
    fn uncited_sentences_are_counted_but_short_ones_are_not() {
        let citations = vec![];
        // Long enough to be a claim.
        assert_eq!(
            count_uncited_claims("Diese Funktion rundet den Betrag immer kaufmännisch ab.", &citations),
            1
        );
        // A transition, not a claim.
        assert_eq!(count_uncited_claims("Kurz gesagt.", &citations), 0);
    }

    #[test]
    fn a_citation_to_a_file_that_does_not_exist_is_rejected() {
        let graph = Graph::open_in_memory("/tmp/nowhere").unwrap();
        let session = Session::default();
        let answer = verify(&graph, &session, "Das passiert in erfunden/datei.py:42.");

        assert_eq!(answer.citations.len(), 1);
        assert_eq!(answer.citations[0].status, CitationStatus::UnknownFile);
        assert!(!answer.is_clean());
    }
}
