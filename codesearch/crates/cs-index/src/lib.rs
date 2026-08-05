//! Turning source files into symbols, references and computed facts.
//!
//! Three layers, deliberately kept apart:
//!
//! * [`walk`] decides what is worth reading and records why anything was skipped.
//! * [`registry`] holds the per-language grammar, tag query and manifest.
//! * [`parse`] turns one file into symbols and *unresolved* references.
//!
//! Nothing here knows about the rest of the workspace. Deciding what a name
//! points at needs a global view and belongs to `cs-resolve`; keeping that out of
//! this crate is what lets a single file be reparsed on its own.

pub mod parse;
pub mod registry;
pub mod walk;
