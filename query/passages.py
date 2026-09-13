"""Versioned PDF passages and CPU BM25 retrieval, without model downloads."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re

from parsing.layout import PARSER_VERSION
from parsing.marker_parser import MarkerParser, PAGE_BREAK
from query.kg_retriever import Evidence, SearchHit, Source, _query_tokens
from query.source_verifier import find_pdf_path
from storage.metadata_db import MetadataDB


def fingerprint(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def split_passages(
    paper_id, text, document_hash, layout=None, max_chars=1500, overlap=180
):
    passages = []
    section = ""
    for page, page_text in enumerate(text.split(PAGE_BREAK), 1):
        for paragraph in re.finditer(r"\S[\s\S]*?(?=\n\s*\n|\Z)", page_text):
            start, end = paragraph.span()
            if (
                end - start < 110
                and "\n" not in paragraph.group()
                and not paragraph.group().endswith(".")
            ):
                section = paragraph.group()
            while start < end:
                stop = min(end, start + max_chars)
                if stop < end:
                    boundary = page_text.rfind(" ", start + max_chars // 2, stop)
                    if boundary > start:
                        stop = boundary
                chunk = page_text[start:stop]
                pid = (
                    "passage:"
                    + hashlib.sha256(
                        f"{paper_id}\0{document_hash}\0{PARSER_VERSION}\0{page}\0{start}\0{stop}".encode()
                    ).hexdigest()[:32]
                )
                positions = [
                    {"bbox": b["bbox"], "kind": b["kind"]}
                    for b in (layout or {}).get(str(page), [])
                    if b["start"] < stop and b["end"] > start
                ]
                passages.append(
                    {
                        "passage_id": pid,
                        "page": page,
                        "start": start,
                        "end": stop,
                        "text": chunk,
                        "section": section,
                        "positions": positions,
                    }
                )
                if stop == end:
                    break
                start = stop - min(overlap, (stop - start) // 4)
                while start < stop and not page_text[start - 1].isspace():
                    start += 1
    return passages


def index_document(db, paper_id, path, parsed=None):
    version = fingerprint(path)
    previous = db.passage_document(paper_id)
    if (
        previous
        and previous["fingerprint"] == version
        and previous["parser_version"] == PARSER_VERSION
    ):
        return db.list_passages([paper_id])
    parsed = parsed or MarkerParser().parse(path, paper_id)
    meta = getattr(parsed, "meta", {}) or getattr(parsed, "metadata", {}) or {}
    if meta.get("guard_aborted"):
        raise ValueError("Incomplete PDF parse: passage cache was not replaced")
    # Non-spatial parser outputs must not masquerade as this parser version.
    if meta.get("parser_version") != PARSER_VERSION:
        parsed = MarkerParser().parse(path, paper_id)
        meta = parsed.meta
    if meta.get("partial") or meta.get("partial_result") or meta.get("guard_aborted"):
        raise ValueError("Incomplete PDF parse: passage cache was not replaced")
    if fingerprint(path) != version:
        raise ValueError("PDF changed during parsing")
    passages = split_passages(paper_id, parsed.text, version, meta.get("page_layout"))
    if not passages:
        raise ValueError("PDF contains no indexable text")
    db.replace_passages(paper_id, version, PARSER_VERSION, path, passages)
    return db.list_passages([paper_id])


def passage_evidence(row, score=1.0):
    positions = row.get("positions") or []
    if isinstance(positions, str):
        positions = json.loads(positions)
    return Evidence(
        paper_id=row["paper_id"],
        kind="passage",
        field="pdf_text",
        text=row["text"],
        score=score,
        evidence_id=row["passage_id"],
        metadata={
            "passage_id": row["passage_id"],
            "document_fingerprint": row["fingerprint"],
            "parser_version": row["parser_version"],
            "page": row["page"],
            "start": row["start_pos"],
            "end": row["end_pos"],
            "section": row["section"],
            "positions": positions,
            "text_found": True,
        },
    )


def bm25(rows, query):
    tokens = set(_query_tokens(query))
    docs = [Counter(re.findall(r"\w+", row["text"].casefold())) for row in rows]
    avg = sum(sum(d.values()) for d in docs) / max(len(docs), 1)
    df = Counter(term for doc in docs for term in tokens if term in doc)
    scores = []
    for row, doc in zip(rows, docs):
        score = 0.0
        for term in tokens:
            tf = doc[term]
            if tf:
                idf = math.log(1 + (len(docs) - df[term] + 0.5) / (df[term] + 0.5))
                score += (
                    idf
                    * tf
                    * 2.2
                    / (tf + 1.2 * (0.25 + 0.75 * sum(doc.values()) / max(avg, 1)))
                )
        scores.append((score, row))
    return sorted(
        scores,
        key=lambda item: (
            -item[0],
            item[1]["paper_id"],
            item[1]["page"],
            item[1]["start_pos"],
        ),
    )


def graph_expansion(query, extractions, rows):
    """One hop only, using approved relations whose quote occurs in that paper."""
    terms = set(_query_tokens(query))
    texts = {}
    for row in rows:
        texts.setdefault(row["paper_id"], []).append(row["text"])
    expansion = []
    for extraction in extractions:
        names = {}
        for concept in extraction.get("concepts") or []:
            if isinstance(concept, dict):
                label = concept.get("name") or concept.get("label")
                if label:
                    names[str(concept.get("id") or label)] = str(label)
        for relation in extraction.get("relations") or []:
            if (
                not isinstance(relation, dict)
                or relation.get("review_status") != "approved"
            ):
                continue
            quote = (
                relation.get("evidence_span")
                or relation.get("evidence_text")
                or relation.get("quote")
            )
            if not isinstance(quote, str) or len(quote.strip()) < 12:
                continue
            normalized = " ".join(quote.casefold().split())
            if not any(
                normalized in " ".join(text.casefold().split())
                for text in texts.get(extraction.get("paper_id"), [])
            ):
                continue
            left = str(relation.get("subject_id") or relation.get("subject") or "")
            right = str(
                relation.get("object_id")
                or relation.get("object")
                or relation.get("target")
                or ""
            )
            left, right = names.get(left, left), names.get(right, right)
            candidate = (
                right
                if terms.intersection(_query_tokens(left))
                else left if terms.intersection(_query_tokens(right)) else ""
            )
            if candidate and candidate not in expansion:
                expansion.append(candidate)
            if len(expansion) >= 12:
                return expansion
    return expansion


def retrieve_passages(
    db_path, query, paper_ids=None, pdf_base_dir="data/pdfs", limit=8, whole=False
):
    """Filter before ranking. Existing PDFs are lazily indexed, once per version."""
    diagnostics = {"passage_errors": {}}
    with MetadataDB(db_path) as db:
        papers = db.list_papers(limit=1_000_000, paper_ids=paper_ids)
        sources = {}
        for paper in papers:
            pid = paper["id"]
            sources[pid] = Source(
                paper_id=pid,
                title=paper.get("title") or pid,
                year=paper.get("year"),
                doi=paper.get("doi"),
                url=paper.get("landing_page_url"),
            )
            path = paper.get("pdf_url")
            path = (
                path
                if path and Path(path).is_file()
                else find_pdf_path(pid, sources[pid].title, pdf_base_dir)
            )
            if path:
                try:
                    index_document(db, pid, path)
                except Exception as exc:
                    diagnostics["passage_errors"][pid] = str(exc)
        # Exclude stale documents whose re-indexing failed.
        rows = [
            r
            for r in db.list_passages(list(sources))
            if r["paper_id"] not in diagnostics["passage_errors"]
        ]
        # A bounded one-hop expansion over the same persisted relations used to build
        # Kuzu. This also works when Kuzu is absent; relations only supply search terms.
        extractions = db.list_extraction_results(
            limit=1_000_000, paper_ids=list(sources), latest_successful=True
        )
        expansion = graph_expansion(query, extractions, rows)
    diagnostics["graph_expansion_terms"] = expansion
    ranked = bm25(rows, query + " " + " ".join(diagnostics["graph_expansion_terms"]))
    overview = bool(
        re.search(
            r"überblick|zusammenfass|overview|summari|wichtigsten|über.?sicht",
            query,
            re.I,
        )
    )
    hits = {}
    per_paper = Counter()
    seen_sections = set()
    if whole:
        selected = ranked
    else:
        selected = [item for item in ranked if item[0] > 0]
        if overview:
            # Cover section starts as well as high scoring passages, without a
            # minimum citation count or a requirement to use every section.
            section_rows = []
            for row in rows:
                key = (row["paper_id"], row["section"] or row["page"])
                if key not in seen_sections:
                    section_rows.append((0.5, row))
                    seen_sections.add(key)
            selected = selected[: max(12, limit * 3)] + section_rows
    seen = set()
    for score, row in selected:
        pid = row["paper_id"]
        if row["passage_id"] in seen or (not whole and per_paper[pid] >= 24):
            continue
        if pid not in hits and len(hits) >= limit:
            continue
        seen.add(row["passage_id"])
        hit = hits.setdefault(pid, SearchHit(source=sources[pid]))
        hit.add_evidence(passage_evidence(row, max(score, 0.1)))
        per_paper[pid] += 1
    diagnostics["passages_indexed"] = len(rows)
    diagnostics["passages_retrieved"] = sum(len(h.evidence) for h in hits.values())
    return list(hits.values()), diagnostics
