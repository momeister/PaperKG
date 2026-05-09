from __future__ import annotations

from typing import Any

import streamlit as st

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


def run_chat_interface() -> None:
    st.set_page_config(page_title="ScienceKG Assistant", layout="wide")
    st.title("ScienceKG Assistant")

    with st.sidebar:
        metadata_db_path = st.text_input("DuckDB path", value="data/metadata.duckdb")
        graph_db_path = st.text_input("Kuzu graph path", value="data/graphs/global_kg")
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
                        [source.to_dict() for source in answer.sources],
                        width="stretch",
                        hide_index=True,
                    )
                if answer.evidence:
                    with st.expander("Evidence"):
                        st.dataframe(
                            [item.to_dict() for item in answer.evidence],
                            width="stretch",
                            hide_index=True,
                        )
            st.session_state.phase4_messages.append({"role": "assistant", "content": answer.answer})


if __name__ == "__main__":
    run_chat_interface()
