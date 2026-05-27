from __future__ import annotations

import base64
import html
import re
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from parsing.marker_parser import MarkerParser
from query.grounded_responder import GroundedResponder
from query.hybrid_retriever import HybridRetriever
from query.kg_retriever import KGRetriever
from query.llm_router import LLMRouter


@st.cache_resource
def init_llm_router() -> LLMRouter:
    return LLMRouter.from_config_file("config.yaml")


@st.cache_resource
def init_retriever(metadata_db_path: str, graph_db_path: str) -> HybridRetriever:
    return HybridRetriever(
        KGRetriever(
            metadata_db_path=metadata_db_path,
            graph_db_path=graph_db_path,
        )
    )


def _source_rows(hits: list[Any]) -> list[dict[str, Any]]:
    rows = []
    for hit in hits:
        rows.append(
            {
                "paper_id": hit.source.paper_id,
                "title": hit.source.title,
                "year": hit.source.year,
                "score": round(hit.score, 3),
                "doi": hit.source.doi,
            }
        )
    return rows


def _evidence_rows(hits: list[Any]) -> list[dict[str, Any]]:
    rows = []
    for hit in hits:
        for item in hit.evidence:
            rows.append(
                {
                    "paper_id": item.paper_id,
                    "kind": item.kind,
                    "score": round(item.score, 3),
                    "text": item.text,
                }
            )
    return rows


def _source_rows_from_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "paper_id": source.get("paper_id"),
            "title": source.get("title"),
            "year": source.get("year"),
            "doi": source.get("doi"),
            "url": source.get("url"),
        }
        for source in sources
    ]


def _evidence_rows_from_answer(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "paper_id": item.get("paper_id"),
            "kind": item.get("kind"),
            "score": item.get("score"),
            "field": item.get("field"),
            "text": item.get("text"),
        }
        for item in evidence
    ]


@st.cache_data(show_spinner=False)
def _find_pdf_path(paper_id: str, title: str, pdf_base_dir: str) -> str | None:
    base = Path(pdf_base_dir)
    if not base.exists():
        return None

    tokens = _pdf_lookup_tokens(paper_id, title)
    candidates = sorted(base.rglob("*.pdf"))
    for token in tokens:
        token_lower = token.lower()
        for candidate in candidates:
            if token_lower in str(candidate).lower():
                return str(candidate)
    return None


@st.cache_data(show_spinner=False)
def _parsed_pdf_text(pdf_path: str, paper_id: str) -> str:
    parsed = MarkerParser().parse(pdf_path, paper_id)
    return parsed.text


def _render_source_verifier(answer: dict[str, Any], pdf_base_dir: str) -> None:
    sources = answer.get("sources") or []
    evidence = answer.get("evidence") or []
    if not sources or not evidence:
        return

    with st.expander("Verify cited source", expanded=False):
        labels = {
            _source_label(source): source
            for source in sources
        }
        selected_label = st.selectbox("Source", options=list(labels), key="phase4_verify_source")
        selected_source = labels[selected_label]
        paper_id = str(selected_source.get("paper_id") or "")
        source_evidence = [item for item in evidence if item.get("paper_id") == paper_id]
        if not source_evidence:
            st.info("No evidence rows are attached to this source.")
            return

        evidence_labels = {
            _evidence_label(item, index): item
            for index, item in enumerate(source_evidence, start=1)
        }
        selected_evidence_label = st.selectbox(
            "Evidence",
            options=list(evidence_labels),
            key="phase4_verify_evidence",
        )
        selected_evidence = evidence_labels[selected_evidence_label]
        reference_text = _reference_text(selected_evidence)
        pdf_path = _find_pdf_path(
            paper_id,
            str(selected_source.get("title") or ""),
            pdf_base_dir,
        )

        left, right = st.columns([1.25, 1])
        with left:
            if pdf_path:
                st.caption(str(Path(pdf_path)))
                st.download_button(
                    "Download PDF",
                    data=Path(pdf_path).read_bytes(),
                    file_name=Path(pdf_path).name,
                    mime="application/pdf",
                    width="stretch",
                )
                _render_pdf(pdf_path)
                st.caption(
                    "If the embedded viewer stays blank, use the download button; "
                    "the verified text span is highlighted on the right."
                )
            else:
                st.warning("No local PDF was found for this source.")
                if selected_source.get("url"):
                    st.link_button("Open source URL", str(selected_source["url"]))

        with right:
            st.caption("Referenced evidence")
            st.markdown(_highlight_terms(reference_text, reference_text), unsafe_allow_html=True)
            if pdf_path:
                parsed_text = _parsed_pdf_text(pdf_path, paper_id)
                excerpt = _best_excerpt(parsed_text, reference_text)
                if excerpt:
                    st.caption("Nearest extracted PDF text")
                    st.markdown(_highlight_terms(excerpt, reference_text), unsafe_allow_html=True)
                else:
                    st.info("The selected evidence was not found in extracted PDF text.")


def _source_label(source: dict[str, Any]) -> str:
    paper_id = str(source.get("paper_id") or "")
    title = str(source.get("title") or paper_id)
    return f"{paper_id} | {title[:110]}"


def _evidence_label(item: dict[str, Any], index: int) -> str:
    kind = str(item.get("kind") or "evidence")
    text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
    return f"{index}. {kind} | {text[:120]}"


def _pdf_lookup_tokens(paper_id: str, title: str) -> list[str]:
    values = [paper_id]
    if ":" in paper_id:
        values.append(paper_id.split(":", 1)[1])
    if title:
        words = re.findall(r"[a-z0-9]+", title.lower())
        if words:
            values.append("-".join(words[:8]))
    tokens: list[str] = []
    for value in values:
        clean = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value)).strip("-").lower()
        if len(clean) >= 4 and clean not in tokens:
            tokens.append(clean)
    return tokens


def _reference_text(evidence: dict[str, Any]) -> str:
    metadata = evidence.get("metadata") if isinstance(evidence.get("metadata"), dict) else {}
    preferred = [
        "evidence_span",
        "context",
        "statement",
        "description",
        "why_applicable",
        "label",
    ]
    parts = [str(metadata.get(key) or "") for key in preferred]
    parts.append(str(evidence.get("text") or ""))
    return re.sub(r"\s+", " ", " ".join(part for part in parts if part)).strip()


def _render_pdf(pdf_path: str) -> None:
    data = Path(pdf_path).read_bytes()
    encoded = base64.b64encode(data).decode("ascii")
    components.html(
        f"""
        <iframe
            src="data:application/pdf;base64,{encoded}"
            width="100%"
            height="720"
            style="border: 1px solid #30343f; border-radius: 6px;"
            type="application/pdf">
        </iframe>
        """,
        height=740,
    )


def _best_excerpt(pdf_text: str, reference_text: str, window_chars: int = 1000) -> str:
    clean = re.sub(r"\s+", " ", pdf_text or "").strip()
    reference = re.sub(r"\s+", " ", reference_text or "").strip()
    if not clean or not reference:
        return ""

    exact = _find_longest_substring(clean, reference)
    if exact >= 0:
        start = max(0, exact - window_chars // 3)
        end = min(len(clean), exact + window_chars)
        return clean[start:end].strip()

    tokens = _highlightable_terms(reference)
    if not tokens:
        return clean[:window_chars]

    best_start = 0
    best_score = -1
    step = max(window_chars // 3, 200)
    lower = clean.lower()
    for start in range(0, max(len(clean) - window_chars, 1), step):
        window = lower[start : start + window_chars]
        score = sum(1 for token in tokens if token in window)
        if score > best_score:
            best_score = score
            best_start = start
    if best_score <= 0:
        return ""
    return clean[best_start : best_start + window_chars].strip()


def _find_longest_substring(text: str, reference: str) -> int:
    lower = text.lower()
    reference_lower = reference.lower()
    chunks = [
        reference_lower[index : index + 120]
        for index in range(0, max(len(reference_lower) - 120, 1), 80)
    ]
    chunks.append(reference_lower[:120])
    for chunk in sorted(set(chunks), key=len, reverse=True):
        chunk = chunk.strip()
        if len(chunk) < 30:
            continue
        position = lower.find(chunk)
        if position >= 0:
            return position
    return -1


def _highlight_terms(text: str, reference_text: str) -> str:
    escaped = html.escape(text or "")
    for term in _highlightable_terms(reference_text)[:18]:
        escaped = re.sub(
            rf"(?i)\b({re.escape(term)})\b",
            r"<mark>\1</mark>",
            escaped,
        )
    return f"<div style='line-height:1.55; font-size:0.94rem'>{escaped}</div>"


def _highlightable_terms(text: str) -> list[str]:
    stopwords = {
        "about",
        "also",
        "and",
        "are",
        "for",
        "from",
        "into",
        "that",
        "the",
        "this",
        "used",
        "using",
        "with",
    }
    terms = re.findall(r"[A-Za-z0-9][A-Za-z0-9-]{2,}", text or "")
    unique: list[str] = []
    for term in sorted(terms, key=len, reverse=True):
        lower = term.lower()
        if lower in stopwords or lower in unique:
            continue
        unique.append(lower)
    return unique


def run_chat_interface() -> None:
    st.set_page_config(page_title="ScienceKG Assistant", layout="wide")
    st.title("ScienceKG Assistant")

    with st.sidebar:
        metadata_db_path = st.text_input("DuckDB path", value="data/metadata.duckdb")
        graph_db_path = st.text_input("Kuzu graph path", value="data/graphs/global_kg")
        pdf_base_dir = st.text_input("PDF path", value="data/pdfs")
        limit = st.slider("Result limit", min_value=3, max_value=25, value=8)
        llm_router = init_llm_router()
        provider = st.selectbox("Provider", options=llm_router.available_providers())
        model_options = llm_router.provider_model_options(provider)
        model = st.selectbox("Model", options=model_options)

    retriever = init_retriever(metadata_db_path, graph_db_path)
    responder = GroundedResponder(retriever=retriever, llm_router=llm_router)

    search_tab, answer_tab = st.tabs(["Search", "Answer"])

    with search_tab:
        query = st.text_input("Search query", key="phase4_search_query")
        if st.button("Search", width="stretch", type="primary") and query.strip():
            hits = retriever.search(query.strip(), limit=limit)
            if not hits:
                st.info("No matching local KG evidence found.")
            else:
                st.dataframe(_source_rows(hits), width="stretch", hide_index=True)
                with st.expander("Evidence", expanded=True):
                    st.dataframe(_evidence_rows(hits), width="stretch", hide_index=True)

    with answer_tab:
        question = st.chat_input("Ask the local KG")
        if "phase4_messages" not in st.session_state:
            st.session_state.phase4_messages = []

        for message in st.session_state.phase4_messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        if question:
            st.session_state.phase4_messages.append({"role": "user", "content": question})
            with st.chat_message("user"):
                st.markdown(question)

            with st.chat_message("assistant"):
                with st.spinner("Retrieving local KG evidence..."):
                    answer = responder.answer(
                        question,
                        limit=limit,
                        provider=provider,
                        model=model,
                )
                st.markdown(answer.answer)
                if answer.generation_error:
                    st.warning(f"Answer generation fell back to evidence mode: {answer.generation_error}")
                if answer.sources:
                    st.caption("Sources")
                    st.dataframe(
                        _source_rows_from_sources([source.to_dict() for source in answer.sources]),
                        width="stretch",
                        hide_index=True,
                    )
                if answer.evidence:
                    with st.expander("Evidence"):
                        st.dataframe(
                            _evidence_rows_from_answer([item.to_dict() for item in answer.evidence]),
                            width="stretch",
                            hide_index=True,
                        )
                st.session_state.phase4_last_answer = answer.to_dict()
            st.session_state.phase4_messages.append({"role": "assistant", "content": answer.answer})

        if st.session_state.get("phase4_last_answer"):
            _render_source_verifier(st.session_state.phase4_last_answer, pdf_base_dir)


if __name__ == "__main__":
    run_chat_interface()
