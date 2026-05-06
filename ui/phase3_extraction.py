"""
Phase 3 Streamlit UI: Interactive Entity Extraction Dashboard

Features:
- Paste paper text or upload PDF
- Extract entities with configurable LLM providers
- Browse extracted concepts, methods, claims
- Manage custom vocabulary
- Batch process multiple papers
"""

from __future__ import annotations

import asyncio
import base64
import httpx
import json
import shutil
import re
import time
from pathlib import Path

import streamlit as st
from pypdf import PdfReader

from extraction.entity_extractor import EntityExtractor
from extraction.entity_linker import ExtractionPipeline
from extraction.conflict_detector import ConflictDetector
from extraction.embedding_engine import EmbeddingEngine
from extraction.batch_processor import BatchProcessor
from extraction.vocabulary import VocabularyManager
from harvester.arxiv_client import ArxivClient, ArxivClientConfig
from harvester.deduplication import deduplicate_papers
from harvester.openalex_client import OpenAlexClient, OpenAlexConfig
from harvester.semantic_scholar_client import SemanticScholarClient, SemanticScholarConfig
from parsing.marker_parser import MarkerParser
from parsing.parser_router import ParserRouter, ParserType
from query.llm_router import LLMRouter
from storage.file_manager import FileManager
from storage.metadata_db import MetadataDB

# Page config
st.set_page_config(
    page_title="ScienceKG Phase 3",
    page_icon="SKG",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("ScienceKG Phase 3: Entity Extraction")
st.markdown("Extract concepts, methods, and claims from research papers with configurable LLMs")


# Initialize session state
@st.cache_resource
def init_llm_router():
    """Initialize LLM router with caching."""
    return LLMRouter.from_config_file("config.yaml")


@st.cache_resource
def init_extraction_pipeline():
    """Initialize extraction pipeline with caching."""
    llm_router = init_llm_router()
    return ExtractionPipeline(llm_router)


@st.cache_resource
def init_parser_router():
    """Initialize parser router with caching."""
    return ParserRouter()


@st.cache_resource
def init_embedding_engine():
    """Initialize embedding engine with caching."""
    return EmbeddingEngine()


@st.cache_resource
def init_conflict_detector():
    """Initialize conflict detector with caching."""
    return ConflictDetector(init_llm_router())


@st.cache_resource
def init_vocabulary_manager():
    """Initialize vocabulary manager with caching."""
    vocab_file = Path("data/vocabulary.json")

    if vocab_file.exists():
        import json

        try:
            with open(vocab_file) as f:
                content = f.read().strip()
                if content:
                    data = json.loads(content)
                    return VocabularyManager.from_dict(data)
        except (json.JSONDecodeError, ValueError):
            pass

    return VocabularyManager()


@st.cache_resource
def init_file_manager():
    """Initialize local PDF storage manager with caching."""
    return FileManager("data/pdfs")


@st.cache_resource(validate=lambda db: not db.is_closed)
def init_metadata_db():
    """Initialize metadata database with caching."""
    return MetadataDB("data/metadata.duckdb")


@st.cache_resource
def init_batch_processor():
    """Initialize in-process batch processor with caching."""
    return BatchProcessor(init_llm_router(), init_parser_router(), init_embedding_engine())


def _normalize_arxiv_entry(entry: dict) -> dict:
    return {
        "source": "arxiv",
        "source_id": str(entry.get("source_id") or entry.get("id") or "unknown"),
        "version": entry.get("version", 1),
        "title": entry.get("title") or "",
        "abstract": entry.get("abstract") or "",
        "authors": entry.get("authors") or [],
        "year": entry.get("year"),
        "doi": entry.get("doi"),
        "pdf_url": entry.get("pdf_url"),
        "landing_page_url": entry.get("landing_page_url"),
        "raw": entry,
    }


def _normalize_s2_paper(paper: dict) -> dict:
    external_ids = paper.get("externalIds") or {}
    open_access_pdf = paper.get("openAccessPdf") or {}
    return {
        "source": "semantic_scholar",
        "source_id": str(paper.get("paperId") or paper.get("corpusId") or "unknown"),
        "version": 1,
        "title": paper.get("title") or "",
        "abstract": paper.get("abstract") or "",
        "authors": [a.get("name", "") for a in paper.get("authors", [])],
        "year": paper.get("year"),
        "doi": external_ids.get("DOI") or paper.get("doi"),
        "pdf_url": open_access_pdf.get("url"),
        "landing_page_url": paper.get("url"),
        "raw": paper,
    }


def _normalize_openalex_work(work: dict) -> dict:
    ids = work.get("ids") or {}
    doi = ids.get("doi") or work.get("doi")
    if isinstance(doi, str):
        doi = doi.removeprefix("https://doi.org/")
    oa_location = work.get("best_oa_location") or {}
    authorships = work.get("authorships") or []
    return {
        "source": "openalex",
        "source_id": str(work.get("id") or "unknown"),
        "version": 1,
        "title": work.get("title") or "",
        "abstract": "",
        "authors": [
            (authorship.get("author") or {}).get("display_name", "")
            for authorship in authorships
        ],
        "year": work.get("publication_year"),
        "doi": doi,
        "pdf_url": oa_location.get("pdf_url"),
        "landing_page_url": work.get("id"),
        "raw": work,
    }


async def _search_phase1_papers(query: str, sources: list[str], max_results: int) -> list[dict]:
    combined: list[dict] = []
    arxiv = ArxivClient(ArxivClientConfig()) if "arxiv" in sources else None
    s2 = SemanticScholarClient(SemanticScholarConfig()) if "semantic_scholar" in sources else None
    openalex = OpenAlexClient(OpenAlexConfig()) if "openalex" in sources else None

    try:
        if arxiv is not None:
            combined.extend(await arxiv.search(query, max_results=max_results))
        if s2 is not None:
            payload = await s2.search_papers(
                query,
                limit=min(max_results, 20),
                fields="paperId,title,abstract,authors,year,externalIds,openAccessPdf,url",
            )
            combined.extend(_normalize_s2_paper(p) for p in payload.get("data", []))
        if openalex is not None:
            payload = await openalex.list_works(search=query, per_page=min(max_results, 20), page=1)
            combined.extend(_normalize_openalex_work(w) for w in payload.get("results", []))
    finally:
        if arxiv is not None:
            await arxiv.close()
        if s2 is not None:
            await s2.close()
        if openalex is not None:
            await openalex.close()

    unique, _ = deduplicate_papers(combined)
    return unique


async def _download_search_results(results: list[dict], pdf_dir: str = "data/pdfs") -> tuple[int, int, int]:
    file_manager = FileManager(pdf_dir)
    downloaded = 0
    skipped = 0
    failed = 0

    async with httpx.AsyncClient(timeout=60.0) as client:
        for paper in results:
            url = paper.get("pdf_url")
            paper_id = _paper_metadata_id(paper)
            storage_id = _paper_pdf_storage_id(paper)
            version = paper.get("version") or 1
            if not url:
                skipped += 1
                continue
            if file_manager.exists(storage_id, version):
                skipped += 1
                continue
            try:
                response = await client.get(url, follow_redirects=True)
                response.raise_for_status()
                file_manager.save_pdf(storage_id, response.content, version)
                downloaded += 1
            except Exception:
                failed += 1

    return downloaded, skipped, failed


def _paper_metadata_id(paper: dict) -> str:
    return str(paper.get("id") or f"{paper.get('source')}:{paper.get('source_id')}")


def _paper_pdf_storage_id(paper: dict) -> str:
    return FileManager.safe_storage_id(
        str(paper.get("source_id") or paper.get("id") or "paper"),
        display_name=str(paper.get("title") or "untitled"),
        source=str(paper.get("source") or "unknown"),
    )


def _list_harvested_pdfs(pdf_dir: str = "data/pdfs") -> list[tuple[str, str]]:
    pdf_root = Path(pdf_dir)
    if not pdf_root.exists():
        return []

    pdfs: list[tuple[str, str]] = []
    for pdf_file in sorted(pdf_root.rglob("*.pdf"), key=lambda path: path.stat().st_mtime, reverse=True):
        try:
            label = str(pdf_file.relative_to(pdf_root))
        except ValueError:
            label = pdf_file.name
        pdfs.append((label, str(pdf_file)))
    return pdfs


def _normalize_parser_name(parser_value: object) -> str:
    if isinstance(parser_value, ParserType):
        return parser_value.value
    return str(parser_value)


def _safe_json_parse(value: str) -> object | None:
    try:
        return json.loads(value)
    except Exception:
        return None


def _render_pdf_preview(pdf_path: str, title: str = "PDF Preview", key_scope: str = "default") -> None:
    path = Path(pdf_path)
    if not path.exists():
        st.info("PDF preview is unavailable because the file does not exist.")
        return

    try:
        size_bytes = path.stat().st_size
        size_mb = size_bytes / 1024 / 1024
        st.caption(str(path))
        st.markdown(f"### {title}")
        st.download_button(
            "Download PDF",
            data=path.read_bytes(),
            file_name=path.name,
            mime="application/pdf",
            use_container_width=True,
            key=f"download_preview_{key_scope}_{path.as_posix()}",
        )
        if size_mb <= 10:
            encoded_pdf = base64.b64encode(path.read_bytes()).decode("utf-8")
            st.markdown(
                f'<iframe src="data:application/pdf;base64,{encoded_pdf}" width="100%" height="800" type="application/pdf"></iframe>',
                unsafe_allow_html=True,
            )
        else:
            st.info(
                f"Embedded preview is disabled for this {size_mb:.1f} MB PDF. "
                "Large base64 PDF embeds are unstable in Streamlit/browser tabs."
            )
            preview_text = _pdf_text_preview(path)
            if preview_text:
                st.text_area(
                    "Text preview",
                    value=preview_text,
                    height=360,
                    key=f"text_preview_{path.as_posix()}",
                )
            else:
                st.caption("No text preview could be extracted from the first pages.")
    except Exception as exc:
        st.warning(f"Could not render PDF preview: {exc}")


def _pdf_text_preview(path: Path, max_pages: int = 3, max_chars: int = 6000) -> str:
    try:
        reader = PdfReader(str(path))
        chunks = []
        for page in reader.pages[:max_pages]:
            chunks.append(page.extract_text() or "")
        return "\n\n".join(chunks).strip()[:max_chars]
    except Exception:
        return ""


def _default_paper_id_from_pdf(label_or_path: str) -> str:
    stem = Path(label_or_path).stem
    return stem.rsplit("_v", 1)[0] or "document"


def _set_loaded_paper(
    paper_id: str,
    paper_text: str,
    pdf_path: str | None = None,
    parse_debug: dict[str, object] | None = None,
) -> None:
    st.session_state.loaded_paper_id = paper_id or "document"
    st.session_state.loaded_paper_text = paper_text
    st.session_state.last_pdf_path = pdf_path
    st.session_state.last_parse_debug = parse_debug or {}
    st.session_state.loaded_source = str(pdf_path) if pdf_path else "Pasted text"
    st.session_state.loaded_char_count = len(paper_text or "")
    st.session_state.loaded_page_count = (parse_debug or {}).get("page_count")


def _clear_loaded_input() -> None:
    for key in [
        "loaded_paper_id",
        "loaded_paper_text",
        "last_pdf_path",
        "last_parse_debug",
        "loaded_source",
        "loaded_char_count",
        "loaded_page_count",
    ]:
        st.session_state.pop(key, None)


def _clear_pdf_storage(pdf_dir: str = "data/pdfs") -> int:
    return _clear_directory_contents(pdf_dir)


def _clear_directory_contents(directory: str) -> int:
    root = Path(directory).resolve()
    project_root = Path.cwd().resolve()
    if not root.is_relative_to(project_root):
        raise ValueError(f"Refusing to clear directory outside project: {root}")
    if not root.exists():
        return 0

    removed = 0
    for child in root.iterdir():
        if child.is_dir():
            removed += sum(1 for nested in child.rglob("*") if nested.is_file())
            shutil.rmtree(child)
        else:
            child.unlink()
            removed += 1
    return removed


def _clear_kg_storage(graph_dir: str = "data/graphs") -> int:
    return _clear_directory_contents(graph_dir)


def _close_cached_metadata_db() -> None:
    try:
        init_metadata_db().close()
    except Exception:
        pass
    init_metadata_db.clear()


def _reset_metadata_database_files(db_path: str = "data/metadata.duckdb") -> int:
    _close_cached_metadata_db()
    db_file = Path(db_path).resolve()
    project_root = Path.cwd().resolve()
    if not db_file.is_relative_to(project_root):
        raise ValueError(f"Refusing to reset database outside project: {db_file}")

    removed = 0
    for candidate in [db_file, db_file.with_name(f"{db_file.name}.wal")]:
        if candidate.exists():
            candidate.unlink()
            removed += 1
    return removed


def _reset_vocabulary_file(vocab_path: str = "data/vocabulary.json") -> None:
    path = Path(vocab_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")


def _embedding_rows(labels: list[str]) -> list[dict[str, object]]:
    rows = []
    for label in labels:
        result = embedding_engine.embed_entity(label)
        rows.append(
            {
                "label": label,
                "dimension": result.dimension,
                "norm": float((result.vector ** 2).sum() ** 0.5),
            }
        )
    return rows


def _parse_pdf_document(
    pdf_path: str,
    paper_id: str,
) -> tuple[object, dict[str, object]]:
    parser_router = init_parser_router()
    preview_parser = MarkerParser()
    preview = preview_parser.parse(pdf_path, paper_id)
    selection = parser_router.select_parser_details(pdf_path, preview.text)
    parsed = parser_router.parse(pdf_path, paper_id, force_parser=selection.parser)
    diagnostics = {
        "paper_id": paper_id,
        "selected_parser": _normalize_parser_name(selection.parser),
        "selection_reason": selection.reason,
        "parser_indicators": selection.indicators,
        "page_count": getattr(parsed, "page_count", None),
        "preview_excerpt": preview.text[:1200],
        "parsed_excerpt": parsed.text[:1200],
        "parsed_metadata": getattr(parsed, "metadata", None) or getattr(parsed, "meta", None) or {},
    }
    return parsed, diagnostics


def _extract_pdf_document(
    pdf_path: str,
    paper_id: str,
    provider: str,
    model: str,
    temperature: float,
    top_p: float,
    context_size: int,
    max_tokens: int,
    request_timeout_seconds: int,
    link_concepts: bool,
) -> tuple[object, float, object, dict[str, object]]:
    parsed, diagnostics = _parse_pdf_document(pdf_path, paper_id)
    overrides = {
        "model": model,
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens,
        "context_size": context_size,
        "timeout_seconds": request_timeout_seconds,
    }
    start_time = time.time()
    result = pipeline.process(
        paper_id,
        parsed.text,
        provider=provider,
        overrides=overrides,
        link_concepts=link_concepts,
    )
    duration = time.time() - start_time
    return parsed, duration, result, diagnostics


def _run_pdf_batch_extraction(
    selected_pdfs: list[str],
    pdf_options: dict[str, str],
    provider: str,
    model: str,
    temperature: float,
    top_p: float,
    context_size: int,
    max_tokens: int,
    request_timeout_seconds: int,
    link_concepts: bool,
) -> tuple[list[dict[str, object]], list[str]]:
    batch_results = []
    batch_errors = []
    metadata_db = init_metadata_db()

    progress = st.progress(0)
    status = st.empty()

    for index, pdf_name in enumerate(selected_pdfs, start=1):
        pdf_path = pdf_options[pdf_name]
        batch_paper_id = _default_paper_id_from_pdf(pdf_name)
        status.info(f"Processing {index}/{len(selected_pdfs)}: {pdf_name}")
        try:
            parsed, duration, result, parse_debug = _extract_pdf_document(
                pdf_path,
                batch_paper_id,
                provider,
                model,
                temperature,
                top_p,
                context_size,
                max_tokens,
                request_timeout_seconds,
                link_concepts,
            )
            result_id = metadata_db.save_extraction_result(
                paper_id=batch_paper_id,
                llm_provider=provider,
                llm_model=model,
                concepts=result.concepts,
                methods=result.methods,
                claims=result.claims,
                cross_domain_hints=result.cross_domain_hints,
                raw_response=result.raw_response,
                duration_seconds=duration,
            )
            added_vocabulary = _sync_vocabulary_from_concepts(result.concepts)
            batch_results.append(
                {
                    "paper_id": batch_paper_id,
                    "pdf_name": pdf_name,
                    "parsed_pages": parsed.page_count,
                    "parse_debug": parse_debug,
                    "result": result,
                    "result_id": result_id,
                    "added_vocabulary": added_vocabulary,
                }
            )
        except Exception as exc:
            batch_errors.append(f"{pdf_name}: {exc}")
        progress.progress(index / len(selected_pdfs))

    status.empty()
    progress.empty()
    return batch_results, batch_errors


def _snapshot_extraction_result(result: object) -> dict[str, object]:
    if isinstance(result, dict):
        return {
            "paper_id": result.get("paper_id", ""),
            "result_id": result.get("id") or result.get("result_id"),
            "extraction_timestamp": result.get("extraction_timestamp"),
            "concepts": result.get("concepts") or [],
            "methods": result.get("methods") or [],
            "claims": result.get("claims") or [],
            "cross_domain_hints": result.get("cross_domain_hints") or [],
            "raw_response": result.get("raw_response") or "",
            "extraction_status": result.get("extraction_status"),
        }

    return {
        "paper_id": getattr(result, "paper_id", ""),
        "result_id": None,
        "extraction_timestamp": None,
        "concepts": list(getattr(result, "concepts", []) or []),
        "methods": list(getattr(result, "methods", []) or []),
        "claims": list(getattr(result, "claims", []) or []),
        "cross_domain_hints": list(getattr(result, "cross_domain_hints", []) or []),
        "raw_response": getattr(result, "raw_response", "") or "",
        "extraction_status": None,
    }


def _entity_label_rows(items: list[dict[str, object]], label_key: str, secondary_key: str | None = None) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in items:
        label = str(item.get(label_key, "")).strip()
        if not label:
            continue
        row = {"label": label}
        if secondary_key is not None:
            row[secondary_key] = item.get(secondary_key, "")
        if "confidence" in item:
            row["confidence"] = item.get("confidence", 0.0)
        rows.append(row)
    return rows


def _extract_label_set(items: list[dict[str, object]], key: str) -> set[str]:
    return {str(item.get(key, "")).strip() for item in items if str(item.get(key, "")).strip()}


def _sync_vocabulary_from_concepts(concepts: list[dict[str, object]]) -> list[str]:
    added_labels: list[str] = []

    for concept in concepts:
        label = str(concept.get("label", "")).strip()
        if not label:
            continue

        confidence_value = concept.get("confidence", 0.0)
        try:
            confidence = float(confidence_value)
        except (TypeError, ValueError):
            confidence = 0.0

        if confidence < 0.65:
            continue

        if vocabulary.normalize(label):
            continue

        aliases = []
        lower_label = label.lower()
        if lower_label != label:
            aliases.append(lower_label)

        vocabulary.register(
            label,
            aliases=aliases,
            openalx_id=concept.get("openalx_id") or None,
            domain=concept.get("domain") or None,
        )
        added_labels.append(label)

    if added_labels:
        with open("data/vocabulary.json", "w", encoding="utf-8") as f:
            json.dump(vocabulary.to_dict(), f, indent=2, ensure_ascii=False)

    return added_labels


def _render_extraction_diff(current: dict[str, object], previous: dict[str, object]) -> None:
    st.subheader("Re-run Diff")

    current_concepts = current.get("concepts", []) or []
    previous_concepts = previous.get("concepts", []) or []
    current_methods = current.get("methods", []) or []
    previous_methods = previous.get("methods", []) or []
    current_claims = current.get("claims", []) or []
    previous_claims = previous.get("claims", []) or []

    current_concept_labels = _extract_label_set(current_concepts, "label")
    previous_concept_labels = _extract_label_set(previous_concepts, "label")
    current_method_labels = _extract_label_set(current_methods, "label")
    previous_method_labels = _extract_label_set(previous_methods, "label")
    current_claim_labels = _extract_label_set(current_claims, "statement")
    previous_claim_labels = _extract_label_set(previous_claims, "statement")

    previous_cols, current_cols = st.columns(2)

    with previous_cols:
        st.markdown("**Previous extraction**")
        st.caption(
            f"Result ID: {previous.get('result_id', 'n/a')} | Timestamp: {previous.get('extraction_timestamp', 'n/a')}"
        )
        st.write(f"Concepts: {len(previous_concepts)}")
        st.write(f"Methods: {len(previous_methods)}")
        st.write(f"Claims: {len(previous_claims)}")
        st.dataframe(
            _entity_label_rows(previous_concepts, "label", "confidence"),
            use_container_width=True,
            hide_index=True,
        )

    with current_cols:
        st.markdown("**Current extraction**")
        st.caption(
            f"Result ID: {current.get('result_id', 'n/a')} | Timestamp: {current.get('extraction_timestamp', 'n/a')}"
        )
        st.write(f"Concepts: {len(current_concepts)}")
        st.write(f"Methods: {len(current_methods)}")
        st.write(f"Claims: {len(current_claims)}")
        st.dataframe(
            _entity_label_rows(current_concepts, "label", "confidence"),
            use_container_width=True,
            hide_index=True,
        )

    concept_added = sorted(current_concept_labels - previous_concept_labels)
    concept_removed = sorted(previous_concept_labels - current_concept_labels)
    method_added = sorted(current_method_labels - previous_method_labels)
    method_removed = sorted(previous_method_labels - current_method_labels)
    claim_added = sorted(current_claim_labels - previous_claim_labels)
    claim_removed = sorted(previous_claim_labels - current_claim_labels)

    st.markdown("**Change summary**")
    st.write(f"Concepts added: {len(concept_added)} | removed: {len(concept_removed)}")
    if concept_added:
        st.success("Added concepts: " + ", ".join(concept_added))
    if concept_removed:
        st.warning("Removed concepts: " + ", ".join(concept_removed))

    st.write(f"Methods added: {len(method_added)} | removed: {len(method_removed)}")
    if method_added:
        st.success("Added methods: " + ", ".join(method_added))
    if method_removed:
        st.warning("Removed methods: " + ", ".join(method_removed))

    st.write(f"Claims added: {len(claim_added)} | removed: {len(claim_removed)}")
    if claim_added:
        st.success("Added claims: " + ", ".join(claim_added[:3]))
    if claim_removed:
        st.warning("Removed claims: " + ", ".join(claim_removed[:3]))

    with st.expander("Raw output diff", expanded=False):
        left_raw, right_raw = st.columns(2)
        with left_raw:
            st.markdown("**Previous raw response**")
            previous_raw = previous.get("raw_response", "") or ""
            parsed_previous_raw = _safe_json_parse(str(previous_raw))
            if parsed_previous_raw is not None:
                st.json(parsed_previous_raw)
            else:
                st.code(str(previous_raw))
        with right_raw:
            st.markdown("**Current raw response**")
            current_raw = current.get("raw_response", "") or ""
            parsed_current_raw = _safe_json_parse(str(current_raw))
            if parsed_current_raw is not None:
                st.json(parsed_current_raw)
            else:
                st.code(str(current_raw))


# Initialize components
llm_router = init_llm_router()
pipeline = init_extraction_pipeline()
embedding_engine = init_embedding_engine()
vocabulary = init_vocabulary_manager()
file_manager = init_file_manager()
batch_processor = init_batch_processor()


# Sidebar configuration
with st.sidebar:
    st.header("Configuration")

    # Provider selection
    providers = llm_router.available_providers()
    selected_provider = st.selectbox(
        "LLM Provider",
        options=providers,
        index=providers.index(llm_router.default_provider) if llm_router.default_provider in providers else 0,
        help="Select which LLM provider to use for extraction",
    )

    # Always refresh models on page load to show all available models
    provider_models = llm_router.provider_model_options(selected_provider, refresh=True)
    default_model = llm_router.provider_default_model(selected_provider)
    selected_model = st.selectbox(
        "Model",
        options=provider_models,
        index=provider_models.index(default_model) if default_model in provider_models else 0,
        help="Choose the model that should answer the extraction prompt",
    )
    recommended_settings = llm_router.recommended_settings(selected_provider, selected_model, refresh=True)
    settings_key = re.sub(r"[^A-Za-z0-9_]+", "_", f"{selected_provider}_{selected_model}")
    
    if st.button("Refresh models manually"):
        st.rerun()

    st.divider()

    # LLM Settings
    st.subheader("Generation Settings")

    temperature = st.slider(
        "Temperature",
        min_value=0.0,
        max_value=1.0,
        value=float(recommended_settings.temperature),
        step=0.05,
        help="Lower = more deterministic, Higher = more creative",
        key=f"temperature_{settings_key}",
    )

    top_p = st.slider(
        "Top P (Nucleus Sampling)",
        min_value=0.0,
        max_value=1.0,
        value=float(recommended_settings.top_p),
        step=0.05,
        key=f"top_p_{settings_key}",
    )

    context_size = st.slider(
        "Context Size",
        min_value=1024,
        max_value=262144,
        value=int(recommended_settings.context_size),
        step=1024,
        help="How much input text the model can consider at once",
        key=f"context_size_{settings_key}",
    )

    max_tokens = st.slider(
        "Max Tokens",
        min_value=256,
        max_value=65536,
        value=int(recommended_settings.max_tokens),
        step=256,
        help="Maximum length of the model answer",
        key=f"max_tokens_{settings_key}",
    )

    request_timeout_seconds = st.slider(
        "LLM Request Timeout (seconds)",
        min_value=60,
        max_value=3600,
        value=max(900, int(llm_router.provider_config(selected_provider).timeout_seconds)),
        step=60,
        help="How long Streamlit waits for a local model response before aborting the request.",
    )

    st.divider()

    # Features
    st.subheader("Features")
    link_concepts = st.checkbox("Link to OpenAlex", value=True)
    embed_concepts = st.checkbox("Generate embeddings", value=False)

    st.divider()

    if st.button("Reload Components"):
        _close_cached_metadata_db()
        st.cache_resource.clear()
        st.rerun()

    with st.expander("Local Data Reset", expanded=False):
        reset_scope = st.selectbox(
            "Reset scope",
            options=[
                "Session only",
                "Extraction history only",
                "Database, KG, PDFs, and vocabulary",
            ],
            help="Session reset clears Streamlit state. Full reset removes the local DuckDB database files, Kuzu/graph output, PDFs, vocabulary, and cached components.",
        )
        reset_confirm = st.text_input(
            "Type RESET to confirm",
            key="debug_reset_confirm",
        )
        if st.button("Run Reset", use_container_width=True, type="secondary"):
            if reset_confirm != "RESET":
                st.warning("Type RESET before running a reset.")
            elif reset_scope == "Session only":
                st.session_state.clear()
                st.cache_data.clear()
                st.rerun()
            elif reset_scope == "Extraction history only":
                init_metadata_db().clear_extraction_results()
                for key in ["last_extraction", "last_extraction_previous", "batch_extractions"]:
                    st.session_state.pop(key, None)
                st.success("Extraction history cleared.")
            else:
                removed_db_files = _reset_metadata_database_files()
                removed_files = _clear_pdf_storage()
                removed_graph_items = _clear_kg_storage()
                _reset_vocabulary_file()
                st.session_state.clear()
                st.cache_data.clear()
                st.cache_resource.clear()
                st.success(
                    "Local data reset complete. "
                    f"Removed {removed_db_files} database file(s), "
                    f"{removed_graph_items} graph file(s), and {removed_files} PDF file(s)."
                )
                st.rerun()

    st.caption("Graph visualization stays in Phase 2: streamlit run ui/graph_visualization.py")


# Main content
tabs = st.tabs(["Extract", "PDF Library", "Vocabulary", "Batch", "Harvest", "History"])

# Tab 1: Extract
with tabs[0]:
    st.header("Extract Entities from Paper")
    if st.session_state.get("loaded_paper_text"):
        parse_debug = st.session_state.get("last_parse_debug", {})
        metadata = parse_debug.get("parsed_metadata", {}) if isinstance(parse_debug, dict) else {}
        st.info(
            f"Current input: {st.session_state.get('loaded_paper_id', 'document')} | "
            f"{st.session_state.get('loaded_char_count', 0):,} chars | "
            f"parser={parse_debug.get('selected_parser', 'n/a') if isinstance(parse_debug, dict) else 'n/a'} | "
            f"method={metadata.get('extraction_method', 'n/a') if isinstance(metadata, dict) else 'n/a'}"
        )
        if st.button("Clear loaded input", use_container_width=False):
            _clear_loaded_input()
            st.rerun()

    col1, col2 = st.columns([1, 1])

    paper_text = ""
    paper_id = ""

    with col1:
        st.subheader("Input Text")

        input_method = st.radio(
            "Input method",
            options=["Paste text", "Upload PDF", "PDF URL", "From Harvest"],
            horizontal=True,
        )

        if input_method == "Paste text":
            paper_text = st.text_area(
                "Paper text",
                height=200,
                placeholder="Paste research paper text here...",
                label_visibility="collapsed",
            )
            paper_id = st.text_input("Paper ID (optional)")

        elif input_method == "Upload PDF":
            uploaded_file = st.file_uploader("Upload PDF", type="pdf")

            if uploaded_file:
                paper_id = st.text_input(
                    "Paper ID",
                    value=_default_paper_id_from_pdf(uploaded_file.name),
                    help="Stable ID used to group extraction history and later KG records.",
                )

                file_bytes = uploaded_file.getvalue()
                st.download_button(
                    "Download uploaded PDF",
                    data=file_bytes,
                    file_name=uploaded_file.name,
                    mime="application/pdf",
                    use_container_width=True,
                )

                saved_path = file_manager.save_pdf(paper_id or uploaded_file.name, file_bytes)

                with st.spinner("Parsing PDF..."):
                    try:
                        parsed, parse_debug = _parse_pdf_document(
                            str(saved_path),
                            paper_id or "pdf_document",
                        )
                        paper_text = parsed.text
                        _set_loaded_paper(paper_id or "pdf_document", paper_text, str(saved_path), parse_debug)
                        st.success(f"Parsed {parsed.page_count} pages and saved to {saved_path}")
                        with st.expander("PDF Preview", expanded=False):
                            _render_pdf_preview(str(saved_path), title=uploaded_file.name, key_scope="upload")

                    except Exception as e:
                        st.error(f"Failed to parse PDF: {e}")
                        paper_text = ""
            else:
                paper_text = ""
                paper_id = ""

        elif input_method == "PDF URL":
            pdf_url = st.text_input("PDF URL")
            paper_id = st.text_input(
                "Paper ID",
                value="pdf_from_url",
                help="Stable ID used to group extraction history and later KG records.",
            )

            if st.button("Download PDF", use_container_width=True):
                if not pdf_url:
                    st.error("Please provide a PDF URL")
                    paper_text = ""
                else:
                    with st.spinner("Downloading PDF..."):
                        try:
                            response = httpx.get(pdf_url, follow_redirects=True, timeout=60.0)
                            response.raise_for_status()
                            content_type = response.headers.get("content-type", "")
                            if "pdf" not in content_type.lower():
                                st.warning(f"Content type looks unusual: {content_type}")

                            saved_path = file_manager.save_pdf(paper_id or "pdf_from_url", response.content)
                            st.success(f"PDF downloaded to {saved_path}")

                            parsed, parse_debug = _parse_pdf_document(
                                str(saved_path),
                                paper_id or "pdf_from_url",
                            )
                            paper_text = parsed.text
                            _set_loaded_paper(paper_id or "pdf_from_url", paper_text, str(saved_path), parse_debug)
                            st.success(f"Parsed {parsed.page_count} pages")
                            with st.expander("PDF Preview", expanded=False):
                                _render_pdf_preview(str(saved_path), title=paper_id or "pdf_from_url", key_scope="url")

                        except Exception as e:
                            st.error(f"Failed to download or parse PDF: {e}")
                            paper_text = ""
            else:
                paper_text = ""

        else:  # input_method == "From Harvest"
            st.subheader("Select Recently Harvested PDF")

            harvested_pdfs = _list_harvested_pdfs()
            if harvested_pdfs:
                st.write(f"Found {len(harvested_pdfs)} harvested PDFs")

                pdf_options = {label: path for label, path in harvested_pdfs}
                selected_pdf_name = st.selectbox("Select PDF", options=list(pdf_options.keys()), key="phase3_single_harvest_pdf")
                selected_pdf_path = pdf_options[selected_pdf_name]

                single_paper_id = st.text_input(
                    "Paper ID",
                    value=_default_paper_id_from_pdf(selected_pdf_name),
                    key="phase3_single_harvest_paper_id",
                    help="Stable ID used to group extraction history and later KG records.",
                )

                if st.button("Load and Parse PDF", use_container_width=True):
                    with st.spinner(f"Parsing {selected_pdf_name}..."):
                        try:
                            parsed, parse_debug = _parse_pdf_document(
                                selected_pdf_path,
                                single_paper_id,
                            )
                            paper_text = parsed.text
                            paper_id = single_paper_id
                            _set_loaded_paper(single_paper_id, paper_text, selected_pdf_path, parse_debug)
                            st.success(f"Parsed {parsed.page_count} pages from {selected_pdf_name}")
                            with st.expander("PDF Preview", expanded=False):
                                _render_pdf_preview(selected_pdf_path, title=selected_pdf_name, key_scope="extract_harvest")
                        except Exception as e:
                            st.error(f"Failed to parse PDF: {e}")
                            paper_text = ""

                st.divider()
                st.caption("Use the Batch tab to run extraction for multiple PDFs.")

            else:
                st.warning("No harvested PDFs found. Use Harvest tab to download PDFs first.")

        if not paper_text and st.session_state.get("loaded_paper_text"):
            paper_text = st.session_state.loaded_paper_text
            paper_id = st.session_state.get("loaded_paper_id", paper_id or "document")
            st.info(f"Using loaded parsed text for {paper_id}.")

    with col2:
        st.subheader("Extraction Options")

        st.info(
            f"""
            **Provider:** {selected_provider}
            **Model:** {selected_model}
            
            **Settings:**
            - Temperature: {temperature}
            - Top P: {top_p}
            - Context Size: {context_size}
            - Max Tokens: {max_tokens}
            - Link Concepts: {link_concepts}
            """
        )

    # Extract button
    if st.button("Extract Entities", use_container_width=True):
        if not paper_text:
            st.error("Please provide paper text")

        else:
            import time
            start_time = time.time()
            
            with st.spinner("Extracting entities..."):
                overrides = {
                    "model": selected_model,
                    "temperature": temperature,
                    "top_p": top_p,
                    "max_tokens": max_tokens,
                    "context_size": context_size,
                    "timeout_seconds": request_timeout_seconds,
                }

                try:
                    result = pipeline.process(
                        paper_id or "document",
                        paper_text,
                        provider=selected_provider,
                        overrides=overrides,
                        link_concepts=link_concepts,
                    )
                    
                    duration = time.time() - start_time

                    # Store result in session
                    st.session_state.last_extraction = result
                    st.session_state.last_extract_paper_id = paper_id or "document"
                    if "last_pdf_path" not in st.session_state and input_method == "Paste text":
                        st.session_state.last_pdf_path = None
                    st.session_state.last_parse_debug = st.session_state.get("last_parse_debug", {})

                    # Save to database
                    metadata_db = init_metadata_db()
                    result_id = metadata_db.save_extraction_result(
                        paper_id=paper_id or "document",
                        llm_provider=selected_provider,
                        llm_model=selected_model,
                        concepts=result.concepts,
                        methods=result.methods,
                        claims=result.claims,
                        cross_domain_hints=result.cross_domain_hints,
                        raw_response=result.raw_response,
                        duration_seconds=duration,
                    )
                    added_vocabulary = _sync_vocabulary_from_concepts(result.concepts)
                    previous_runs = metadata_db.get_paper_extractions(paper_id or "document", limit=2)
                    previous_snapshot = _snapshot_extraction_result(previous_runs[1]) if len(previous_runs) > 1 else None

                    st.success(f"Extraction complete. Result ID: {result_id}, Duration: {duration:.2f}s")
                    if added_vocabulary:
                        st.info(f"Vocabulary grew by {len(added_vocabulary)} concept(s): {', '.join(added_vocabulary[:12])}")
                    st.session_state.last_extraction_previous = previous_snapshot

                    claim_texts = [claim.get("statement", "") for claim in result.claims if claim.get("statement")]
                    if len(claim_texts) >= 2:
                        detector = init_conflict_detector()
                        st.session_state.last_conflict_analyses = detector.analyze_claims_batch(
                            claim_texts,
                            provider=selected_provider,
                            overrides=overrides,
                        )

                except Exception as e:
                    import traceback
                    error_msg = f"Extraction failed: {e}"
                    st.error(error_msg)
                    
                    # Save error to database
                    try:
                        metadata_db = init_metadata_db()
                        metadata_db.save_extraction_result(
                            paper_id=paper_id or "document",
                            llm_provider=selected_provider,
                            llm_model=selected_model,
                            error_message=str(e),
                        )
                    except Exception:
                        pass

    # Display results
    if "last_extraction" in st.session_state:
        result = st.session_state.last_extraction
        parse_debug = st.session_state.get("last_parse_debug", {})
        paper_identifier = st.session_state.get("last_extract_paper_id", "document")
        metadata_db = init_metadata_db()

        st.divider()

        if result.raw_response:
            st.subheader("Raw LLM Output")
            parsed_raw = _safe_json_parse(result.raw_response)
            if parsed_raw is not None:
                st.json(parsed_raw)
            else:
                st.code(result.raw_response)

        previous_snapshot = st.session_state.get("last_extraction_previous")
        if previous_snapshot:
            with st.expander("Re-run Diff", expanded=True):
                _render_extraction_diff(_snapshot_extraction_result(result), previous_snapshot)

        if parse_debug:
            with st.expander("Parsing Diagnostics", expanded=True):
                st.write(f"**Selected parser:** {parse_debug.get('selected_parser', 'n/a')}")
                st.write(f"**Reason:** {parse_debug.get('selection_reason', 'n/a')}")
                st.write(f"**Indicators:** {parse_debug.get('parser_indicators', {})}")
                st.text_area(
                    "Preview excerpt",
                    value=str(parse_debug.get("preview_excerpt", "")),
                    height=180,
                    key="last_preview_excerpt",
                )
                st.text_area(
                    "Parsed excerpt",
                    value=str(parse_debug.get("parsed_excerpt", "")),
                    height=180,
                    key="last_parsed_excerpt",
                )
                st.json(parse_debug.get("parsed_metadata", {}))

        if st.session_state.get("last_pdf_path"):
            with st.expander("PDF Preview", expanded=False):
                _render_pdf_preview(str(st.session_state.last_pdf_path), title="Current paper PDF", key_scope="current")

        with st.expander("Stored KG Snapshot", expanded=False):
            paper_record = metadata_db.get_paper(paper_identifier)
            if paper_record:
                st.write("**Paper node:**")
                st.json(paper_record)
            else:
                st.info("No paper node stored yet for this paper_id.")

            stored_results = metadata_db.get_paper_extractions(paper_identifier, limit=5)
            if stored_results:
                st.write("**Recent stored extractions:**")
                st.dataframe(
                    [
                        {
                            "id": item.get("id"),
                            "status": item.get("extraction_status"),
                            "provider": item.get("llm_provider"),
                            "model": item.get("llm_model"),
                            "timestamp": item.get("extraction_timestamp"),
                        }
                        for item in stored_results
                    ],
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.caption("No stored extraction rows for this paper yet.")

        # Concepts
        col1, col2, col3 = st.columns(3)

        with col1:
            st.subheader("Concepts")
            for concept in result.concepts:
                with st.expander(f"{concept.get('label')}"):
                    st.write(f"**Context:** {concept.get('context')}")
                    st.write(f"**Confidence:** {concept.get('confidence'):.2%}")

                    if "openalx_id" in concept and concept["openalx_id"]:
                        st.write(f"**OpenAlex ID:** {concept['openalx_id']}")

        with col2:
            st.subheader("Methods")
            for method in result.methods:
                with st.expander(f"{method.get('label')}"):
                    st.write(method.get("description", ""))

        with col3:
            st.subheader("Claims")
            for claim in result.claims:
                with st.expander(claim.get("statement", "")[:50]):
                    st.write(f"**Type:** {claim.get('evidence_type')}")

        # Cross-domain hints
        if result.cross_domain_hints:
            st.subheader("Cross-Domain Hints")
            for hint in result.cross_domain_hints:
                st.info(f"- {hint}")

        with st.expander("Entity Linking Details", expanded=True):
            linked_rows = [
                {
                    "label": concept.get("label", ""),
                    "context": concept.get("context", ""),
                    "confidence": concept.get("confidence", 0.0),
                    "openalx_id": concept.get("openalx_id"),
                    "openalx_label": concept.get("openalx_label"),
                }
                for concept in result.concepts
            ]
            if linked_rows:
                st.dataframe(linked_rows, use_container_width=True, hide_index=True)
            else:
                st.info("No concepts were extracted, so no linking could be shown.")

        with st.expander("Embedding Diagnostics", expanded=True):
            labels = [concept.get("label", "").strip() for concept in result.concepts if concept.get("label")]
            if labels:
                demo_labels = labels[:3]
                st.dataframe(_embedding_rows(demo_labels), use_container_width=True, hide_index=True)

                same_a = embedding_engine.embed(demo_labels[0])
                same_b = embedding_engine.embed(demo_labels[0])
                st.write(
                    f"Same-label similarity for '{demo_labels[0]}': {embedding_engine.similarity(same_a, same_b):.3f}"
                )

                if len(demo_labels) >= 2:
                    diff_a = embedding_engine.embed(demo_labels[0])
                    diff_b = embedding_engine.embed(demo_labels[1])
                    st.write(
                        f"Cross-label similarity '{demo_labels[0]}' vs '{demo_labels[1]}': {embedding_engine.similarity(diff_a, diff_b):.3f}"
                    )
            else:
                st.info("No concept labels available for an embedding demo.")

        with st.expander("Conflict Detection", expanded=True):
            analyses = st.session_state.get("last_conflict_analyses", [])
            if analyses:
                conflict_rows = [
                    {
                        "claim_1": analysis.claim_pair[0],
                        "claim_2": analysis.claim_pair[1],
                        "type": analysis.conflict_type,
                        "confidence": analysis.confidence,
                        "reasoning": analysis.reasoning,
                        "resolution": analysis.resolution,
                    }
                    for analysis in analyses
                ]
                st.dataframe(conflict_rows, use_container_width=True, hide_index=True)
                contradictions = [row for row in conflict_rows if row["type"] == "contradictory" and row["confidence"] >= 0.7]
                if contradictions:
                    st.error(f"Found {len(contradictions)} high-confidence contradictions.")
                else:
                    st.success("No high-confidence contradictions detected for the current claims.")
            else:
                st.info("Need at least two claims from a successful extraction to run conflict detection.")

    if "batch_extractions" in st.session_state and st.session_state.batch_extractions:
        st.divider()
        st.subheader("Batch Extraction Results")

        for item in st.session_state.batch_extractions:
            result = item["result"]
            with st.expander(f"{item['paper_id']} | Result {item['result_id']} | {item['parsed_pages']} pages"):
                parse_debug = item.get("parse_debug", {})
                if result.raw_response:
                    parsed_raw = _safe_json_parse(result.raw_response)
                    if parsed_raw is not None:
                        st.json(parsed_raw)
                    else:
                        st.code(result.raw_response)
                st.write(f"**Paper ID:** {item['paper_id']}")
                st.write(f"**PDF:** {item['pdf_name']}")
                if parse_debug:
                    st.write(
                        f"**Parser:** {parse_debug.get('selected_parser', 'n/a')} - {parse_debug.get('selection_reason', 'n/a')}"
                    )
                st.write(f"**Concepts:** {len(result.concepts)}")
                st.write(f"**Methods:** {len(result.methods)}")
                st.write(f"**Claims:** {len(result.claims)}")
                if result.cross_domain_hints:
                    st.write("**Cross-domain hints:**")
                    for hint in result.cross_domain_hints:
                        st.info(f"- {hint}")
                if parse_debug:
                    with st.expander("Parser details"):
                        st.write(parse_debug.get("parser_indicators", {}))
                        st.text_area(
                            "Parsed excerpt",
                            value=str(parse_debug.get("parsed_excerpt", "")),
                            height=160,
                            key=f"batch_parsed_excerpt_{item['result_id']}",
                        )

    st.info(
        "Die Graphansicht gehoert zu Phase 2 und laeuft separat ueber `streamlit run ui/graph_visualization.py`. "
        "Phase 3 konzentriert sich auf PDF-Parsing, Modellwahl und Entity Extraction."
    )


# Tab 2: PDFs
with tabs[1]:
    st.header("Local PDF Library")
    harvested_pdfs = _list_harvested_pdfs()

    if not harvested_pdfs:
        st.info("No local PDFs found in data/pdfs. Use Upload PDF, PDF URL, or Harvest first.")
    else:
        pdf_options = {label: path for label, path in harvested_pdfs}
        selected_pdf_name = st.selectbox(
            "Select PDF",
            options=list(pdf_options.keys()),
            key="pdf_library_selection",
        )
        selected_pdf_path = pdf_options[selected_pdf_name]
        selected_path = Path(selected_pdf_path)
        selected_paper_id = st.text_input(
            "Paper ID for parsing/extraction",
            value=_default_paper_id_from_pdf(selected_pdf_name),
            key="pdf_library_paper_id",
            help="Stable ID used to group extraction history and later KG records.",
        )

        stat_cols = st.columns(3)
        stat_cols[0].metric("PDFs", len(harvested_pdfs))
        stat_cols[1].metric("Size", f"{selected_path.stat().st_size / 1024 / 1024:.2f} MB")
        stat_cols[2].metric("Modified", time.strftime("%Y-%m-%d %H:%M", time.localtime(selected_path.stat().st_mtime)))

        action_cols = st.columns([1, 1])
        with action_cols[0]:
            if st.button("Load for Extract tab", use_container_width=True):
                with st.spinner(f"Parsing {selected_pdf_name}..."):
                    try:
                        parsed, parse_debug = _parse_pdf_document(selected_pdf_path, selected_paper_id)
                        _set_loaded_paper(selected_paper_id, parsed.text, selected_pdf_path, parse_debug)
                        st.success(f"Loaded {parsed.page_count} page(s) for extraction.")
                    except Exception as exc:
                        st.error(f"Failed to parse PDF: {exc}")
        with action_cols[1]:
            if st.button("Forget loaded PDF", use_container_width=True):
                _clear_loaded_input()
                st.rerun()

        _render_pdf_preview(selected_pdf_path, title=selected_pdf_name, key_scope="library")

        if st.session_state.get("loaded_paper_text"):
            with st.expander("Loaded parsed text", expanded=False):
                st.text_area(
                    "Parsed text",
                    value=st.session_state.loaded_paper_text,
                    height=240,
                    key="pdf_library_loaded_text",
                )


# Tab 3: Vocabulary
with tabs[2]:
    st.header("Custom Vocabulary Management")

    st.write("Manage entity normalization and deduplication")

    col1, col2 = st.columns([2, 1])

    with col1:
        vocab_rows = [
            {
                "canonical_label": canonical,
                "aliases": ", ".join(entry.aliases),
                "openalx_id": entry.openalx_id,
                "domain": entry.domain,
                "confidence": entry.confidence,
            }
            for canonical, entry in sorted(vocabulary.entries.items())
        ]
        st.dataframe(vocab_rows, use_container_width=True, hide_index=True)

    with col2:
        st.subheader("Add Entry")

        canonical = st.text_input("Canonical label")
        aliases = st.text_area("Aliases (comma-separated)")
        openalx_id = st.text_input("OpenAlex ID (optional)")
        domain = st.text_input("Domain (optional)")

        if st.button("Add Entry"):
            if canonical:
                alias_list = [a.strip() for a in aliases.split(",") if a.strip()]
                vocabulary.register(
                    canonical,
                    aliases=alias_list,
                    openalx_id=openalx_id or None,
                    domain=domain or None,
                )

                # Save
                import json

                with open("data/vocabulary.json", "w") as f:
                    json.dump(vocabulary.to_dict(), f, indent=2)

                st.success("Entry added")
                st.rerun()


# Tab 4: Batch
with tabs[3]:
    st.header("Batch Processing")
    st.write("Run the same extraction settings over multiple PDFs from the local PDF Library.")

    harvested_pdfs = _list_harvested_pdfs()
    if not harvested_pdfs:
        st.info("No local PDFs found. Add PDFs via Upload, PDF URL, or Harvest first.")
    else:
        pdf_options = {label: path for label, path in harvested_pdfs}
        select_all = st.checkbox("Select all local PDFs", value=False)
        batch_selection = st.multiselect(
            "PDFs to process",
            options=list(pdf_options.keys()),
            default=list(pdf_options.keys()) if select_all else list(pdf_options.keys())[:1],
            key="batch_pdf_selection",
        )
        batch_preview_rows = [
            {
                "PDF": name,
                "Paper ID": _default_paper_id_from_pdf(name),
                "Size MB": round(Path(pdf_options[name]).stat().st_size / 1024 / 1024, 2),
            }
            for name in batch_selection
        ]
        st.dataframe(batch_preview_rows, use_container_width=True, hide_index=True)
        st.caption("Paper ID groups extraction history and later KG records. It is derived from the PDF filename.")

        if st.button("Start Batch Extraction", use_container_width=True, type="primary"):
            if not batch_selection:
                st.error("Select at least one PDF.")
            else:
                batch_results, batch_errors = _run_pdf_batch_extraction(
                    batch_selection,
                    pdf_options,
                    selected_provider,
                    selected_model,
                    temperature,
                    top_p,
                    context_size,
                    max_tokens,
                    request_timeout_seconds,
                    link_concepts,
                )
                st.session_state.batch_extractions = batch_results
                if batch_results:
                    st.success(f"Batch extraction complete for {len(batch_results)} PDF(s).")
                if batch_errors:
                    st.warning("Some PDFs failed:\n" + "\n".join(f"- {error}" for error in batch_errors))

    if st.session_state.get("batch_extractions"):
        st.subheader("Last Batch Results")
        rows = [
            {
                "paper_id": item["paper_id"],
                "pdf": item["pdf_name"],
                "result_id": item["result_id"],
                "pages": item["parsed_pages"],
                "concepts": len(item["result"].concepts),
                "methods": len(item["result"].methods),
                "claims": len(item["result"].claims),
            }
            for item in st.session_state.batch_extractions
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)


# Tab 5: Phase 1 Harvest
with tabs[4]:
    st.header("Phase 1: Research Search & PDF Download")
    st.write("Search a research topic, inspect the results, and download PDFs locally.")

    col1, col2 = st.columns([2, 1])

    with col1:
        harvest_query = st.text_input("Research topic / query", value="machine learning")
        harvest_sources = st.multiselect(
            "Sources",
            options=["arxiv", "semantic_scholar", "openalex"],
            default=["arxiv"],
        )
        harvest_limit = st.slider("Max results", 5, 50, 10)
        save_to_db = st.checkbox("Save results to metadata database", value=True)

    with col2:
        st.subheader("Options")
        download_pdfs = st.checkbox("Download available PDFs", value=False)
        run_search = st.button("Search Papers", use_container_width=True)

    if run_search:
        if not harvest_query.strip():
            st.error("Please enter a research topic or query")
        elif not harvest_sources:
            st.error("Please choose at least one source")
        else:
            with st.spinner("Searching papers..."):
                try:
                    results = asyncio.run(_search_phase1_papers(harvest_query.strip(), harvest_sources, harvest_limit))
                    st.session_state.harvest_results = results

                    if save_to_db:
                        metadata_db = init_metadata_db()
                        metadata_db.batch_insert_papers(results)

                    if download_pdfs and results:
                        downloaded, skipped, failed = asyncio.run(_download_search_results(results))
                        st.info(f"PDF download summary: downloaded={downloaded}, skipped={skipped}, failed={failed}")

                    st.success(f"Found {len(results)} unique papers")
                except Exception as exc:
                    st.error(f"Search failed: {exc}")

    if "harvest_results" in st.session_state:
        results = st.session_state.harvest_results
        st.subheader("Search Results")
        st.write("Use the download buttons to store PDFs in data/pdfs.")

        for index, paper in enumerate(results, start=1):
            title = paper.get("title") or "Untitled paper"
            paper_id = paper.get("id") or f"{paper.get('source')}:{paper.get('source_id')}"
            with st.expander(f"{index}. {title}"):
                st.write(f"**ID:** {paper_id}")
                st.write(f"**Source:** {paper.get('source')}")
                st.write(f"**Year:** {paper.get('year')}")
                st.write(f"**DOI:** {paper.get('doi') or 'n/a'}")
                st.write(f"**PDF URL:** {paper.get('pdf_url') or 'n/a'}")
                st.write(f"**Landing page:** {paper.get('landing_page_url') or 'n/a'}")

                if paper.get("pdf_url"):
                    if st.button("Download PDF", key=f"download_{paper_id}"):
                        with st.spinner("Downloading PDF..."):
                            try:
                                downloaded, skipped, failed = asyncio.run(_download_search_results([paper]))
                                st.success(f"Download summary: downloaded={downloaded}, skipped={skipped}, failed={failed}")
                            except Exception as exc:
                                st.error(f"Download failed: {exc}")
                else:
                    st.caption("No direct PDF URL available for this record.")


# Tab 6: History
with tabs[5]:
    st.header("Extraction History")
    st.write("View and manage past extraction results")
    
    col1, col2 = st.columns([2, 1])
    
    with col1:
        paper_id_filter = st.text_input("Filter by paper ID (optional)", placeholder="e.g., arxiv_2301.12345")
    
    with col2:
        if st.button("Refresh History"):
            st.rerun()
    
    metadata_db = init_metadata_db()
    
    if paper_id_filter.strip():
        # Show specific paper's extractions
        extractions = metadata_db.get_paper_extractions(paper_id_filter.strip(), limit=100)
        st.subheader(f"Extractions for {paper_id_filter}")
    else:
        # Show most recent extractions across all papers
        extractions = metadata_db.list_extraction_results(limit=50)
        st.subheader("Recent Extractions (Last 50)")
    
    if extractions:
        st.write(f"Found {len(extractions)} extraction(s)")
        
        for ext in extractions:
            with st.expander(
                f"{ext.get('paper_id', 'Unknown')} | "
                f"{ext.get('llm_model', 'Unknown')} | "
                f"{ext.get('extraction_status', 'Unknown').upper()} | "
                f"{ext.get('extraction_timestamp', 'Unknown')}"
            ):
                col1, col2, col3 = st.columns(3)
                
                with col1:
                    st.metric("Paper ID", ext.get('paper_id', 'N/A'))
                    st.metric("Provider", ext.get('llm_provider', 'N/A'))
                
                with col2:
                    st.metric("Model", ext.get('llm_model', 'N/A'))
                    st.metric("Status", ext.get('extraction_status', 'N/A'))
                
                with col3:
                    st.metric("Concepts", len(ext.get('concepts', [])))
                    st.metric("Methods", len(ext.get('methods', [])))
                    st.metric("Claims", len(ext.get('claims', [])))
                    if ext.get('extraction_duration_seconds'):
                        st.metric("Duration", f"{ext['extraction_duration_seconds']:.2f}s")

                if ext.get('raw_response'):
                    with st.expander("Raw LLM output"):
                        parsed_raw = _safe_json_parse(str(ext.get('raw_response')))
                        if parsed_raw is not None:
                            st.json(parsed_raw)
                        else:
                            st.code(str(ext.get('raw_response')))
                
                if ext.get('error_message'):
                    st.error(f"Error: {ext['error_message']}")
                
                if ext.get('concepts'):
                    st.subheader("Concepts")
                    for concept in ext['concepts']:
                        st.write(f"- {concept.get('label', 'Unknown')} (confidence: {concept.get('confidence', 0):.1%})")
                
                if ext.get('methods'):
                    st.subheader("Methods")
                    for method in ext['methods']:
                        st.write(f"- {method.get('label', 'Unknown')}")
                
                if ext.get('claims'):
                    st.subheader("Claims")
                    for claim in ext['claims']:
                        st.write(f"- {claim.get('statement', 'Unknown')}")
                
                if ext.get('cross_domain_hints'):
                    st.subheader("Cross-Domain Hints")
                    for hint in ext['cross_domain_hints']:
                        st.info(hint)
    else:
        st.info("No extraction history found. Start by extracting entities from papers.")


# Footer
st.divider()

col1, col2, col3 = st.columns(3)

with col1:
    st.caption(f"Provider: {selected_provider}")

with col2:
    st.caption("Phase 3 | Entity Extraction & LLM Integration")

with col3:
    st.caption("ScienceKG Knowledge Graph")

