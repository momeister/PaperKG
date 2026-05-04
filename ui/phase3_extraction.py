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
import httpx
import json
import time
from pathlib import Path

import streamlit as st

from extraction.entity_extractor import EntityExtractor
from extraction.entity_linker import ExtractionPipeline
from extraction.embedding_engine import EmbeddingEngine
from extraction.vocabulary import VocabularyManager
from harvester.arxiv_client import ArxivClient, ArxivClientConfig
from harvester.deduplication import deduplicate_papers
from harvester.openalex_client import OpenAlexClient, OpenAlexConfig
from harvester.semantic_scholar_client import SemanticScholarClient, SemanticScholarConfig
from parsing.marker_parser import MarkerParser
from query.llm_router import LLMRouter
from storage.file_manager import FileManager
from storage.metadata_db import MetadataDB

# Page config
st.set_page_config(
    page_title="ScienceKG Phase 3",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("🔬 ScienceKG Phase 3: Entity Extraction")
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
def init_embedding_engine():
    """Initialize embedding engine with caching."""
    return EmbeddingEngine()


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


@st.cache_resource
def init_metadata_db():
    """Initialize metadata database with caching."""
    return MetadataDB("data/metadata.duckdb")


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
            paper_id = paper.get("id") or f"{paper.get('source')}:{paper.get('source_id')}"
            version = paper.get("version") or 1
            if not url:
                skipped += 1
                continue
            if file_manager.exists(paper_id, version):
                skipped += 1
                continue
            try:
                response = await client.get(url, follow_redirects=True)
                response.raise_for_status()
                file_manager.save_pdf(paper_id, response.content, version)
                downloaded += 1
            except Exception:
                failed += 1

    return downloaded, skipped, failed


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


def _extract_pdf_document(
    pdf_path: str,
    paper_id: str,
    provider: str,
    model: str,
    temperature: float,
    top_p: float,
    context_size: int,
    max_tokens: int,
    link_concepts: bool,
) -> tuple[object, float, object]:
    parser = MarkerParser()
    parsed = parser.parse(pdf_path, paper_id)
    overrides = {
        "model": model,
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens,
        "context_size": context_size,
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
    return parsed, duration, result


# Initialize components
llm_router = init_llm_router()
pipeline = init_extraction_pipeline()
embedding_engine = init_embedding_engine()
vocabulary = init_vocabulary_manager()
file_manager = init_file_manager()


# Sidebar configuration
with st.sidebar:
    st.header("⚙️ Configuration")

    # Provider selection
    providers = llm_router.available_providers()
    selected_provider = st.selectbox(
        "LLM Provider",
        options=providers,
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
    
    if st.button("🔄 Refresh models manually"):
        st.rerun()

    st.divider()

    # LLM Settings
    st.subheader("Generation Settings")

    temperature = st.slider(
        "Temperature",
        min_value=0.0,
        max_value=1.0,
        value=0.2,
        step=0.05,
        help="Lower = more deterministic, Higher = more creative",
    )

    top_p = st.slider(
        "Top P (Nucleus Sampling)",
        min_value=0.0,
        max_value=1.0,
        value=0.95,
        step=0.05,
    )

    context_size = st.slider(
        "Context Size",
        min_value=1024,
        max_value=262144,
        value=llm_router.provider_settings(selected_provider).context_size,
        step=1024,
        help="How much input text the model can consider at once",
    )

    max_tokens = st.slider(
        "Max Tokens",
        min_value=256,
        max_value=65536,
        value=llm_router.provider_settings(selected_provider).max_tokens,
        step=256,
        help="Maximum length of the model answer",
    )

    st.divider()

    # Features
    st.subheader("Features")
    link_concepts = st.checkbox("Link to OpenAlex", value=True)
    embed_concepts = st.checkbox("Generate embeddings", value=False)

    st.divider()

    if st.button("🔄 Reload Components"):
        st.cache_resource.clear()
        st.rerun()

    st.caption("Graph visualization stays in Phase 2: streamlit run ui/graph_visualization.py")


# Main content
tabs = st.tabs(["📄 Extract", "📚 Vocabulary", "📊 Batch", "📥 Harvest", "📋 History"])

# Tab 1: Extract
with tabs[0]:
    st.header("Extract Entities from Paper")

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
                paper_id = st.text_input("Paper ID", value=uploaded_file.name)

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
                    parser = MarkerParser()
                    try:
                        parsed = parser.parse(str(saved_path), paper_id or "pdf_document")
                        paper_text = parsed.text
                        st.success(f"✓ Parsed {parsed.page_count} pages and saved to {saved_path}")

                    except Exception as e:
                        st.error(f"Failed to parse PDF: {e}")
                        paper_text = ""
            else:
                paper_text = ""
                paper_id = ""

        elif input_method == "PDF URL":
            pdf_url = st.text_input("PDF URL")
            paper_id = st.text_input("Paper ID", value="pdf_from_url")

            if st.button("⬇️ Download PDF", use_container_width=True):
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
                            st.success(f"✓ PDF downloaded to {saved_path}")

                            parser = MarkerParser()
                            parsed = parser.parse(str(saved_path), paper_id or "pdf_from_url")
                            paper_text = parsed.text
                            st.success(f"✓ Parsed {parsed.page_count} pages")

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
                    value=Path(selected_pdf_name).stem,
                    key="phase3_single_harvest_paper_id",
                )

                if st.button("📖 Load and Parse PDF", use_container_width=True):
                    with st.spinner(f"Parsing {selected_pdf_name}..."):
                        try:
                            parsed, _, _ = _extract_pdf_document(
                                selected_pdf_path,
                                single_paper_id,
                                selected_provider,
                                selected_model,
                                temperature,
                                top_p,
                                context_size,
                                max_tokens,
                                link_concepts,
                            )
                            paper_text = parsed.text
                            paper_id = single_paper_id
                            st.success(f"✓ Parsed {parsed.page_count} pages from {selected_pdf_name}")
                        except Exception as e:
                            st.error(f"Failed to parse PDF: {e}")
                            paper_text = ""

                st.divider()
                st.subheader("Batch Extract Harvested PDFs")
                batch_selection = st.multiselect(
                    "Select PDFs to extract sequentially",
                    options=list(pdf_options.keys()),
                    default=[selected_pdf_name],
                    key="phase3_batch_harvest_pdf_selection",
                )

                if st.button("🚀 Extract Selected PDFs", use_container_width=True):
                    if not batch_selection:
                        st.error("Please select at least one PDF")
                    else:
                        batch_results = []
                        batch_errors = []
                        metadata_db = init_metadata_db()

                        progress = st.progress(0)
                        status = st.empty()

                        for index, pdf_name in enumerate(batch_selection, start=1):
                            pdf_path = pdf_options[pdf_name]
                            batch_paper_id = Path(pdf_name).stem
                            status.info(f"Processing {index}/{len(batch_selection)}: {pdf_name}")
                            try:
                                parsed, duration, result = _extract_pdf_document(
                                    pdf_path,
                                    batch_paper_id,
                                    selected_provider,
                                    selected_model,
                                    temperature,
                                    top_p,
                                    context_size,
                                    max_tokens,
                                    link_concepts,
                                )
                                result_id = metadata_db.save_extraction_result(
                                    paper_id=batch_paper_id,
                                    llm_provider=selected_provider,
                                    llm_model=selected_model,
                                    concepts=result.concepts,
                                    methods=result.methods,
                                    claims=result.claims,
                                    cross_domain_hints=result.cross_domain_hints,
                                    raw_response=result.raw_response,
                                    duration_seconds=duration,
                                )
                                batch_results.append(
                                    {
                                        "paper_id": batch_paper_id,
                                        "pdf_name": pdf_name,
                                        "parsed_pages": parsed.page_count,
                                        "result": result,
                                        "result_id": result_id,
                                    }
                                )
                            except Exception as exc:
                                batch_errors.append(f"{pdf_name}: {exc}")
                            progress.progress(index / len(batch_selection))

                        status.empty()
                        progress.empty()
                        st.session_state.batch_extractions = batch_results

                        if batch_results:
                            st.success(f"✓ Batch extraction complete for {len(batch_results)} PDF(s)")
                        if batch_errors:
                            st.warning("Some PDFs failed:\n" + "\n".join(f"- {error}" for error in batch_errors))
            else:
                st.warning("No harvested PDFs found. Use Harvest tab to download PDFs first.")

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
    if st.button("🚀 Extract Entities", use_container_width=True):
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

                    st.success(f"✓ Extraction complete! (Result ID: {result_id}, Duration: {duration:.2f}s)")

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

        st.divider()

        if result.raw_response:
            st.warning(result.raw_response)

        # Concepts
        col1, col2, col3 = st.columns(3)

        with col1:
            st.subheader("🔬 Concepts")
            for concept in result.concepts:
                with st.expander(f"{concept.get('label')}"):
                    st.write(f"**Context:** {concept.get('context')}")
                    st.write(f"**Confidence:** {concept.get('confidence'):.2%}")

                    if "openalx_id" in concept and concept["openalx_id"]:
                        st.write(f"**OpenAlex ID:** {concept['openalx_id']}")

        with col2:
            st.subheader("🔧 Methods")
            for method in result.methods:
                with st.expander(f"{method.get('label')}"):
                    st.write(method.get("description", ""))

        with col3:
            st.subheader("💡 Claims")
            for claim in result.claims:
                with st.expander(claim.get("statement", "")[:50]):
                    st.write(f"**Type:** {claim.get('evidence_type')}")

        # Cross-domain hints
        if result.cross_domain_hints:
            st.subheader("🌐 Cross-Domain Hints")
            for hint in result.cross_domain_hints:
                st.info(f"• {hint}")

    if "batch_extractions" in st.session_state and st.session_state.batch_extractions:
        st.divider()
        st.subheader("Batch Extraction Results")

        for item in st.session_state.batch_extractions:
            result = item["result"]
            with st.expander(f"{item['paper_id']} | Result {item['result_id']} | {item['parsed_pages']} pages"):
                if result.raw_response:
                    st.warning(result.raw_response)
                st.write(f"**Paper ID:** {item['paper_id']}")
                st.write(f"**PDF:** {item['pdf_name']}")
                st.write(f"**Concepts:** {len(result.concepts)}")
                st.write(f"**Methods:** {len(result.methods)}")
                st.write(f"**Claims:** {len(result.claims)}")
                if result.cross_domain_hints:
                    st.write("**Cross-domain hints:**")
                    for hint in result.cross_domain_hints:
                        st.info(f"• {hint}")

    st.info(
        "Die Graphansicht gehört zu Phase 2 und läuft separat über `streamlit run ui/graph_visualization.py`. "
        "Phase 3 konzentriert sich auf PDF-Parsing, Modellwahl und Entity Extraction."
    )


# Tab 2: Vocabulary
with tabs[1]:
    st.header("Custom Vocabulary Management")

    st.write("Manage entity normalization and deduplication")

    col1, col2 = st.columns([2, 1])

    with col1:
        vocab_entries = vocabulary.to_dict()
        st.dataframe(vocab_entries)

    with col2:
        st.subheader("Add Entry")

        canonical = st.text_input("Canonical label")
        aliases = st.text_area("Aliases (comma-separated)")
        openalx_id = st.text_input("OpenAlex ID (optional)")
        domain = st.text_input("Domain (optional)")

        if st.button("✅ Add Entry"):
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

                st.success("✓ Entry added")
                st.rerun()


# Tab 3: Batch
with tabs[2]:
    st.header("Batch Processing")

    st.write("Process multiple papers in batch")

    col1, col2 = st.columns([2, 1])

    with col1:
        paper_ids = st.text_area(
            "Paper IDs (one per line)",
            placeholder="arxiv_001\narxiv_002\narxiv_003",
        )

    with col2:
        st.subheader("Options")
        parallel_jobs = st.slider("Parallel jobs", 1, 8, 4)

    # List jobs
    jobs = batch_processor.list_jobs() if "batch_processor" in dir() else []

    if jobs:
        st.subheader("Recent Jobs")

        for job in jobs[-5:]:
            status_icon = "✓" if job.status == "completed" else "⏳"
            st.write(
                f"{status_icon} {job.job_id} | {job.papers_processed}/{job.papers_total} papers"
            )


# Tab 4: Phase 1 Harvest
with tabs[3]:
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
        run_search = st.button("🔎 Search Papers", use_container_width=True)

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
                        metadata_db = MetadataDB("data/metadata.duckdb")
                        try:
                            metadata_db.batch_insert_papers(results)
                        finally:
                            metadata_db.close()

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
                    if st.button("⬇️ Download PDF", key=f"download_{paper_id}"):
                        with st.spinner("Downloading PDF..."):
                            try:
                                downloaded, skipped, failed = asyncio.run(_download_search_results([paper]))
                                st.success(f"Download summary: downloaded={downloaded}, skipped={skipped}, failed={failed}")
                            except Exception as exc:
                                st.error(f"Download failed: {exc}")
                else:
                    st.caption("No direct PDF URL available for this record.")


# Tab 5: History
with tabs[4]:
    st.header("Extraction History")
    st.write("View and manage past extraction results")
    
    col1, col2 = st.columns([2, 1])
    
    with col1:
        paper_id_filter = st.text_input("Filter by paper ID (optional)", placeholder="e.g., arxiv_2301.12345")
    
    with col2:
        if st.button("🔄 Refresh History"):
            st.rerun()
    
    metadata_db = init_metadata_db()
    
    if paper_id_filter.strip():
        # Show specific paper's extractions
        extractions = metadata_db.get_paper_extractions(paper_id_filter.strip(), limit=100)
        st.subheader(f"Extractions for {paper_id_filter}")
    else:
        # Show most recent extractions across all papers
        all_results = metadata_db.conn.execute("""
            SELECT * FROM extraction_results
            ORDER BY extraction_timestamp DESC
            LIMIT 50
        """).fetchall()
        
        cols = [desc[0] for desc in metadata_db.conn.description]
        extractions = []
        for row in all_results:
            data = dict(zip(cols, row))
            for field in ["concepts", "methods", "claims", "cross_domain_hints"]:
                if data.get(field):
                    try:
                        data[field] = json.loads(data[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            extractions.append(data)
        st.subheader("Recent Extractions (Last 50)")
    
    if extractions:
        st.write(f"Found {len(extractions)} extraction(s)")
        
        for ext in extractions:
            with st.expander(
                f"📄 {ext.get('paper_id', 'Unknown')} | "
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
                
                if ext.get('error_message'):
                    st.error(f"Error: {ext['error_message']}")
                
                if ext.get('concepts'):
                    st.subheader("Concepts")
                    for concept in ext['concepts']:
                        st.write(f"• {concept.get('label', 'Unknown')} (confidence: {concept.get('confidence', 0):.1%})")
                
                if ext.get('methods'):
                    st.subheader("Methods")
                    for method in ext['methods']:
                        st.write(f"• {method.get('label', 'Unknown')}")
                
                if ext.get('claims'):
                    st.subheader("Claims")
                    for claim in ext['claims']:
                        st.write(f"• {claim.get('statement', 'Unknown')}")
                
                if ext.get('cross_domain_hints'):
                    st.subheader("Cross-Domain Hints")
                    for hint in ext['cross_domain_hints']:
                        st.info(f"💡 {hint}")
    else:
        st.info("No extraction history found. Start by extracting entities from papers.")


# Footer
st.divider()

col1, col2, col3 = st.columns(3)

with col1:
    st.caption(f"🔬 Provider: {selected_provider}")

with col2:
    st.caption("📚 Phase 3 | Entity Extraction & LLM Integration")

with col3:
    st.caption("🚀 ScienceKG Knowledge Graph")
