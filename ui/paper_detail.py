from __future__ import annotations

from typing import Any

import streamlit as st

from query.kg_retriever import KGRetriever


@st.cache_resource
def init_retriever(metadata_db_path: str, graph_db_path: str) -> KGRetriever:
    return KGRetriever(metadata_db_path=metadata_db_path, graph_db_path=graph_db_path)


def _list_rows(items: list[dict[str, Any]], fields: list[str]) -> list[dict[str, Any]]:
    rows = []
    for item in items:
        rows.append({field: item.get(field) for field in fields})
    return rows


def run_paper_detail_app() -> None:
    st.set_page_config(page_title="ScienceKG Paper Detail", layout="wide")
    st.title("Paper Detail")

    with st.sidebar:
        metadata_db_path = st.text_input("DuckDB path", value="data/metadata.duckdb")
        graph_db_path = st.text_input("Kuzu graph path", value="data/graphs/global_kg")
        paper_id = st.text_input("Paper ID")
        limit = st.slider("Neighborhood limit", min_value=5, max_value=50, value=20)

    if not paper_id.strip():
        st.info("Enter a paper ID to inspect.")
        return

    retriever = init_retriever(metadata_db_path, graph_db_path)
    detail = retriever.paper_detail(paper_id.strip())
    if detail is None:
        st.error("Paper not found.")
        return

    paper = detail["paper"]
    source = detail["source"]
    latest = detail.get("latest_extraction") or {}

    st.subheader(paper.get("title") or source["paper_id"])
    meta_cols = st.columns(4)
    meta_cols[0].metric("Year", paper.get("year") or "n/a")
    meta_cols[1].metric("Source", paper.get("source") or "n/a")
    meta_cols[2].metric("Citations", paper.get("citation_count") or 0)
    meta_cols[3].metric("Extractions", len(detail.get("extractions") or []))

    st.write(
        {
            "paper_id": source["paper_id"],
            "doi": source.get("doi"),
            "url": source.get("url"),
            "has_full_text": paper.get("has_full_text"),
            "retracted": paper.get("retracted"),
            "conflict_flag": paper.get("conflict_flag"),
        }
    )

    if paper.get("abstract"):
        with st.expander("Abstract", expanded=True):
            st.write(paper["abstract"])

    tabs = st.tabs(["Extraction", "Neighborhood", "History"])

    with tabs[0]:
        if not latest:
            st.info("No extraction result found for this paper.")
        else:
            concept_rows = _list_rows(latest.get("concepts") or [], ["label", "confidence", "openalx_id", "context"])
            method_rows = _list_rows(latest.get("methods") or [], ["label", "domain", "source_type", "description"])
            claim_rows = _list_rows(latest.get("claims") or [], ["statement", "evidence_type", "negated", "attributed_to"])

            if concept_rows:
                st.subheader("Concepts")
                st.dataframe(concept_rows, width="stretch", hide_index=True)
            if method_rows:
                st.subheader("Methods")
                st.dataframe(method_rows, width="stretch", hide_index=True)
            if claim_rows:
                st.subheader("Claims")
                st.dataframe(claim_rows, width="stretch", hide_index=True)
            if latest.get("cross_domain_hints"):
                st.subheader("Cross-Domain Hints")
                st.dataframe(latest["cross_domain_hints"], width="stretch", hide_index=True)

    with tabs[1]:
        neighborhood = retriever.paper_neighborhood(source["paper_id"], limit=limit)
        if not neighborhood:
            st.info("No neighborhood data found.")
        else:
            left, middle, right = st.columns(3)
            with left:
                st.subheader("Cites")
                st.dataframe(neighborhood["citations"], width="stretch", hide_index=True)
            with middle:
                st.subheader("Cited By")
                st.dataframe(neighborhood["cited_by"], width="stretch", hide_index=True)
            with right:
                st.subheader("Similar")
                st.dataframe(neighborhood["similar"], width="stretch", hide_index=True)

    with tabs[2]:
        rows = [
            {
                "id": item.get("id"),
                "timestamp": item.get("extraction_timestamp"),
                "status": item.get("extraction_status"),
                "model": item.get("llm_model"),
                "concepts": len(item.get("concepts") or []),
                "methods": len(item.get("methods") or []),
                "claims": len(item.get("claims") or []),
            }
            for item in detail.get("extractions") or []
        ]
        st.dataframe(rows, width="stretch", hide_index=True)


if __name__ == "__main__":
    run_paper_detail_app()
