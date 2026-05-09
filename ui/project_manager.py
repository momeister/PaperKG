from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import streamlit as st

from graph.kuzu_schema import initialize_kuzu_schema
from graph.project_global_merge import merge_project_records_into_global
from storage.metadata_db import MetadataDB


PROJECTS_PATH = Path("data/projects.json")


def load_projects(path: Path = PROJECTS_PATH) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {
        str(name): [str(paper_id) for paper_id in paper_ids]
        for name, paper_ids in data.items()
        if isinstance(paper_ids, list)
    }


def save_projects(projects: dict[str, list[str]], path: Path = PROJECTS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(projects, indent=2, sort_keys=True), encoding="utf-8")


def _paper_options(db_path: str, limit: int = 5000) -> dict[str, str]:
    with MetadataDB(db_path) as db:
        papers = db.list_papers(limit=limit)
    options = {}
    for paper in papers:
        pid = str(paper.get("id") or f"{paper.get('source')}:{paper.get('source_id')}")
        title = str(paper.get("title") or pid)
        options[f"{title[:90]} | {pid}"] = pid
    return options


def _records_for_project(db_path: str, paper_ids: list[str]) -> list[dict[str, Any]]:
    records = []
    with MetadataDB(db_path) as db:
        for pid in paper_ids:
            record = db.get_paper(pid)
            if record is not None:
                records.append(record)
    return records


def run_project_manager_app() -> None:
    st.set_page_config(page_title="ScienceKG Projects", layout="wide")
    st.title("Projects")

    with st.sidebar:
        metadata_db_path = st.text_input("DuckDB path", value="data/metadata.duckdb")
        global_graph_path = st.text_input("Global Kuzu path", value="data/graphs/global_kg")

    projects = load_projects()
    options = _paper_options(metadata_db_path)

    left, right = st.columns([1, 2])
    with left:
        project_name = st.text_input("Project name", value=next(iter(projects), "default"))
        selected_project = projects.get(project_name, [])
        labels_by_id = {paper_id: label for label, paper_id in options.items()}
        default_labels = [labels_by_id[paper_id] for paper_id in selected_project if paper_id in labels_by_id]
        selected_labels = st.multiselect(
            "Papers",
            options=list(options.keys()),
            default=default_labels,
        )
        selected_ids = [options[label] for label in selected_labels]

        if st.button("Save Project", width="stretch", type="primary"):
            if project_name.strip():
                projects[project_name.strip()] = selected_ids
                save_projects(projects)
                st.success("Project saved.")
                st.rerun()

        if st.button("Merge Project to Global KG", width="stretch"):
            records = _records_for_project(metadata_db_path, selected_ids)
            try:
                graph = initialize_kuzu_schema(global_graph_path)
                report = merge_project_records_into_global(graph, records)
                st.success(f"Merged {report.unique_records} unique paper(s).")
                st.write(report.__dict__)
            except Exception as exc:
                st.error(f"Merge failed: {exc}")

    with right:
        rows = [
            {
                "project": name,
                "papers": len(paper_ids),
                "paper_ids": ", ".join(paper_ids[:5]),
            }
            for name, paper_ids in sorted(projects.items())
        ]
        st.dataframe(rows, width="stretch", hide_index=True)

        if selected_ids:
            st.subheader(project_name)
            st.dataframe(
                [{"paper_id": paper_id, "label": labels_by_id.get(paper_id, paper_id)} for paper_id in selected_ids],
                width="stretch",
                hide_index=True,
            )


if __name__ == "__main__":
    run_project_manager_app()
