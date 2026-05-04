from __future__ import annotations

from pathlib import Path
from typing import Any

import networkx as nx
import streamlit as st
from pyvis.network import Network

from storage.metadata_db import MetadataDB


def _normalize_node_label(paper_id: str, title: str | None) -> str:
	if title:
		return title[:80]
	return paper_id


def build_network_from_metadata(records: list[dict[str, Any]]) -> nx.DiGraph:
	graph = nx.DiGraph()

	for record in records:
		pid = str(record.get("id"))
		graph.add_node(pid, label=_normalize_node_label(pid, record.get("title")))
		for cited in record.get("citations") or record.get("references") or []:
			graph.add_edge(pid, str(cited), relation="CITES")

	return graph


def render_pyvis(graph: nx.DiGraph, output_file: Path) -> Path:
	net = Network(height="750px", width="100%", directed=True)
	net.from_nx(graph)
	output_file.parent.mkdir(parents=True, exist_ok=True)
	net.save_graph(str(output_file))
	return output_file


def run_graph_visualization_app() -> None:
	st.set_page_config(page_title="ScienceKG Graph", layout="wide")
	st.title("ScienceKG - Citation Graph Visualization")

	db_path = st.text_input("DuckDB path", value="data/metadata.duckdb")
	limit = st.slider("Max papers", min_value=10, max_value=5000, value=300, step=10)

	if st.button("Build Graph"):
		db = MetadataDB(db_path)
		try:
			records = db.list_papers(limit=limit)
		finally:
			db.close()

		graph = build_network_from_metadata(records)
		html_path = render_pyvis(graph, Path("data/graphs/project_kgs/graph_preview.html"))

		st.success(
			f"Graph built: {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges"
		)
		st.components.v1.html(html_path.read_text(encoding="utf-8"), height=760, scrolling=True)


if __name__ == "__main__":
	run_graph_visualization_app()
