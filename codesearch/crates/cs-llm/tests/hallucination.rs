//! The test the whole product rests on.
//!
//! Every category of confident fabrication a model actually produces, checked
//! against a real index. If this file goes green while the verifier is broken,
//! nothing else in CodeSearch is worth trusting either — so each case is written
//! as the specific lie it is meant to catch, not as an abstract assertion.

use cs_llm::citation::{verify, CitationStatus};
use cs_llm::tools::{dispatch, Session};
use cs_workspace::Workspace;

/// A small workspace with one file whose contents the test knows exactly.
fn fixture() -> (tempfile::TempDir, Workspace) {
    let dir = tempfile::tempdir().unwrap();
    std::fs::create_dir_all(dir.path().join("src")).unwrap();
    std::fs::write(
        dir.path().join("src/pricing.py"),
        "\"\"\"Preisberechnung.\"\"\"\n\
         \n\
         TAX = 0.19\n\
         \n\
         \n\
         def apply_discount(amount, pct):\n\
         \x20   \"\"\"Zieht einen Rabatt ab.\"\"\"\n\
         \x20   return amount * (1 - pct)\n\
         \n\
         \n\
         def total(amount, pct):\n\
         \x20   net = apply_discount(amount, pct)\n\
         \x20   return net * (1 + TAX)\n",
    )
    .unwrap();

    let mut workspace = Workspace::open(dir.path()).unwrap();
    workspace.index(&|_| {}).unwrap();
    (dir, workspace)
}

/// Retrieves the file the way the assistant would, so the session has a record.
fn retrieve(workspace: &Workspace, session: &mut Session) {
    let result = dispatch(
        workspace.graph(),
        session,
        "read_lines",
        r#"{"path":"src/pricing.py","from_line":1,"to_line":13}"#,
    );
    assert!(result.contains("apply_discount"), "the fixture should have been read: {result}");
}

#[test]
fn a_citation_into_retrieved_code_is_accepted() {
    let (_dir, workspace) = fixture();
    let mut session = Session::default();
    retrieve(&workspace, &mut session);

    let answer = verify(
        workspace.graph(),
        &session,
        "Der Rabatt wird in src/pricing.py:8 abgezogen.",
    );

    assert_eq!(answer.citations.len(), 1);
    assert_eq!(answer.citations[0].status, CitationStatus::Verified);
    assert!(answer.is_clean());
}

#[test]
fn a_citation_to_a_file_that_does_not_exist_is_caught() {
    // The most common fabrication: a plausible path in a plausible layout.
    let (_dir, workspace) = fixture();
    let mut session = Session::default();
    retrieve(&workspace, &mut session);

    let answer = verify(
        workspace.graph(),
        &session,
        "Die Steuer wird in src/tax/calculator.py:42 angewendet.",
    );

    assert_eq!(answer.citations[0].status, CitationStatus::UnknownFile);
    assert!(!answer.is_clean());
}

#[test]
fn a_citation_into_a_real_file_that_was_never_looked_at_is_caught() {
    // The subtler failure: the file is real, the line number is even plausible,
    // but the model never received those lines and is reconstructing from
    // training data.
    let (_dir, workspace) = fixture();
    let session = Session::default(); // nothing retrieved

    let answer = verify(
        workspace.graph(),
        &session,
        "Der Rabatt wird in src/pricing.py:8 abgezogen.",
    );

    assert_eq!(answer.citations[0].status, CitationStatus::NotRetrieved);
    assert!(!answer.is_clean());
}

#[test]
fn a_quotation_that_was_tidied_up_is_caught() {
    // A quote that reads exactly like the real code but is not the real code is
    // the most convincing wrong answer there is.
    let (_dir, workspace) = fixture();
    let mut session = Session::default();
    retrieve(&workspace, &mut session);

    let answer = verify(
        workspace.graph(),
        &session,
        "Siehe src/pricing.py:6-8:\n\
         ```python\n\
         def apply_discount(amount, percentage):\n\
         \x20   return amount * (1.0 - percentage)\n\
         ```\n",
    );

    assert!(
        !answer.quote_mismatches.is_empty(),
        "a rewritten signature must not pass as a quotation"
    );
    assert!(!answer.is_clean());
}

#[test]
fn a_verbatim_quotation_passes() {
    let (_dir, workspace) = fixture();
    let mut session = Session::default();
    retrieve(&workspace, &mut session);

    let answer = verify(
        workspace.graph(),
        &session,
        "Siehe src/pricing.py:6-8:\n\
         ```python\n\
         def apply_discount(amount, pct):\n\
         \x20   return amount * (1 - pct)\n\
         ```\n",
    );

    assert!(answer.quote_mismatches.is_empty(), "got {:?}", answer.quote_mismatches);
    assert!(answer.is_clean());
}

#[test]
fn a_citation_into_a_file_edited_since_indexing_is_marked_stale() {
    // Correct line number, wrong world: the file moved on and the address no
    // longer points where the answer says it does.
    let (dir, workspace) = fixture();
    let mut session = Session::default();
    retrieve(&workspace, &mut session);

    std::fs::write(
        dir.path().join("src/pricing.py"),
        "# eine völlig andere Datei\nTAX = 0.07\n",
    )
    .unwrap();

    let answer = verify(
        workspace.graph(),
        &session,
        "Der Rabatt wird in src/pricing.py:8 abgezogen.",
    );

    assert_eq!(answer.citations[0].status, CitationStatus::Stale);
    assert!(!answer.is_clean());
}

#[test]
fn tool_results_carry_the_confidence_the_model_must_hedge_with() {
    // The model can only hedge correctly if the tool result tells it to. This
    // asserts the word is actually in the payload the model receives.
    let (_dir, workspace) = fixture();
    let mut session = Session::default();

    let hits = workspace.graph().search_symbols("apply_discount", &[], 1).unwrap();
    let arguments = format!(r#"{{"id":"{}"}}"#, hits[0].id);
    let callers = dispatch(workspace.graph(), &mut session, "callers_of", &arguments);

    assert!(callers.contains("total"), "total() calls it: {callers}");
    assert!(
        callers.contains("aufgelöst") || callers.contains("vermutet") || callers.contains("verifiziert"),
        "every neighbour must arrive with its confidence: {callers}"
    );
    assert!(callers.contains("beleg"), "and with the line that proves it: {callers}");
}

#[test]
fn an_answer_with_no_citations_is_not_reported_as_verified() {
    // The reporting bug this guards against: an answer citing nothing passed the
    // "all citations hold" check vacuously and was shown as "Alle Belege halten",
    // which reads as an endorsement of a claim nobody checked.
    use cs_llm::citation::Verdict;

    let (_dir, workspace) = fixture();
    let mut session = Session::default();
    retrieve(&workspace, &mut session);

    let answer = verify(
        workspace.graph(),
        &session,
        "Der Betrag wird zuerst rabattiert und dann versteuert.",
    );

    assert!(answer.citations.is_empty());
    assert_eq!(answer.verdict(), Verdict::Uncited);
    assert!(!answer.verdict().label().contains("halten"));
}

#[test]
fn reading_a_file_is_what_licenses_quoting_it() {
    // Retrieval and permission to quote are the same act; this pins that they
    // cannot drift apart.
    let (_dir, workspace) = fixture();
    let mut session = Session::default();

    assert!(!session.covers("src/pricing.py", 6, 8));
    retrieve(&workspace, &mut session);
    assert!(session.covers("src/pricing.py", 6, 8));
}
