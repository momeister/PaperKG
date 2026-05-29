from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from query.grounded_responder import GroundedResponder
from query.hybrid_retriever import HybridRetriever
from query.hypothesis_generator import HypothesisGenerator
from query.kg_retriever import KGRetriever
from query.llm_router import LLMRouter
from query.source_verifier import find_pdf_path, verify_answer_sources
from quality.benchmark import run_benchmark
from quality.kg_health import build_health_report


app = FastAPI(
    title="ScienceKG Phase 4 API",
    description="Grounded KG search, research answers, paper details, and hypotheses.",
    version="4.0.0",
)

llm_router = LLMRouter.from_config_file("config.yaml")


class Phase4Request(BaseModel):
    metadata_db_path: str = "data/metadata.duckdb"
    graph_db_path: str = "data/graphs/global_kg"


class SearchRequest(Phase4Request):
    query: str
    limit: int = Field(default=10, ge=1, le=50)
    include_extractions: bool = True
    include_embeddings: bool = True


class AnswerRequest(Phase4Request):
    question: str
    limit: int = Field(default=8, ge=1, le=25)
    provider: str | None = None
    model: str | None = None
    conversation_context: list[dict[str, Any]] = []


class HypothesisRequest(Phase4Request):
    topic: str | None = None
    paper_id: str | None = None
    limit: int = Field(default=10, ge=1, le=25)
    provider: str | None = None
    model: str | None = None
    use_llm_refinement: bool = False


class VerifyAnswerRequest(Phase4Request):
    answer: dict[str, Any]
    pdf_base_dir: str = "data/pdfs"
    parse_pdfs: bool = True
    max_sources: int = Field(default=10, ge=1, le=50)
    max_evidence_per_source: int = Field(default=5, ge=1, le=50)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "phase": "4",
        "default_provider": llm_router.default_provider,
        "available_providers": llm_router.available_providers(),
    }


@app.post("/query/search")
def query_search(request: SearchRequest) -> dict[str, Any]:
    retriever = _hybrid_retriever(request.metadata_db_path, request.graph_db_path)
    hits = retriever.search(
        request.query,
        limit=request.limit,
        include_extractions=request.include_extractions,
        include_embeddings=request.include_embeddings,
    )
    return {
        "query": request.query,
        "hits": [hit.to_dict() for hit in hits],
    }


@app.post("/query/answer")
def query_answer(request: AnswerRequest) -> dict[str, Any]:
    retriever = _hybrid_retriever(request.metadata_db_path, request.graph_db_path)
    responder = GroundedResponder(retriever=retriever, llm_router=llm_router)
    answer = responder.answer(
        request.question,
        limit=request.limit,
        provider=request.provider,
        model=request.model,
        conversation_context=request.conversation_context,
    )
    return answer.to_dict()


@app.post("/sources/verify-answer")
def sources_verify_answer(request: VerifyAnswerRequest) -> dict[str, Any]:
    report = verify_answer_sources(
        request.answer,
        pdf_base_dir=request.pdf_base_dir,
        parse_pdfs=request.parse_pdfs,
        max_sources=request.max_sources,
        max_evidence_per_source=request.max_evidence_per_source,
    )
    return report.to_dict()


@app.post("/query/hypotheses")
def query_hypotheses(request: HypothesisRequest) -> dict[str, Any]:
    if not request.topic and not request.paper_id:
        raise HTTPException(status_code=400, detail="Provide either topic or paper_id.")

    retriever = _hybrid_retriever(request.metadata_db_path, request.graph_db_path)
    generator = HypothesisGenerator(
        retriever=retriever,
        llm_router=llm_router if request.use_llm_refinement else None,
    )
    hypotheses = generator.generate(
        topic=request.topic,
        paper_id=request.paper_id,
        limit=request.limit,
        provider=request.provider,
        model=request.model,
    )
    return {
        "topic": request.topic,
        "paper_id": request.paper_id,
        "hypotheses": [hypothesis.to_dict() for hypothesis in hypotheses],
    }


@app.get("/papers/{paper_id}/neighborhood")
def paper_neighborhood(
    paper_id: str,
    metadata_db_path: str = "data/metadata.duckdb",
    graph_db_path: str = "data/graphs/global_kg",
    limit: int = 20,
) -> dict[str, Any]:
    retriever = _kg_retriever(metadata_db_path, graph_db_path)
    neighborhood = retriever.paper_neighborhood(paper_id, limit=limit)
    if neighborhood is None:
        raise HTTPException(status_code=404, detail=f"Paper not found: {paper_id}")
    return neighborhood


@app.get("/papers/{paper_id}")
def paper_detail(
    paper_id: str,
    metadata_db_path: str = "data/metadata.duckdb",
    graph_db_path: str = "data/graphs/global_kg",
) -> dict[str, Any]:
    retriever = _kg_retriever(metadata_db_path, graph_db_path)
    detail = retriever.paper_detail(paper_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Paper not found: {paper_id}")
    return detail


@app.get("/paper/pdf")
def paper_pdf(
    paper_id: str,
    title: str = "",
    pdf_base_dir: str = "data/pdfs",
    metadata_db_path: str = "data/metadata.duckdb",
    graph_db_path: str = "data/graphs/global_kg",
):
    if not title:
        detail = _kg_retriever(metadata_db_path, graph_db_path).paper_detail(paper_id)
        title = str((detail or {}).get("source", {}).get("title") or "")
    pdf_path = find_pdf_path(paper_id, title, pdf_base_dir)
    if pdf_path is None:
        raise HTTPException(status_code=404, detail=f"Local PDF not found for: {paper_id}")

    resolved_pdf = Path(pdf_path).resolve()
    resolved_base = Path(pdf_base_dir).resolve()
    if resolved_base not in [resolved_pdf, *resolved_pdf.parents]:
        raise HTTPException(status_code=400, detail="Resolved PDF path is outside the configured PDF directory.")
    return FileResponse(
        path=str(resolved_pdf),
        media_type="application/pdf",
        filename=resolved_pdf.name,
    )


@app.get("/system/health-report")
def system_health_report(
    metadata_db_path: str = "data/metadata.duckdb",
    graph_db_path: str = "data/graphs/global_kg",
    pdf_base_dir: str = "data/pdfs",
) -> dict[str, Any]:
    return build_health_report(
        metadata_db_path=metadata_db_path,
        graph_db_path=graph_db_path,
        pdf_base_dir=pdf_base_dir,
    )


@app.get("/quality/benchmark")
def quality_benchmark(
    gold_dir: str = "quality/gold",
    pred_dir: str | None = None,
    allow_embedded_predictions: bool = True,
) -> dict[str, Any]:
    return run_benchmark(
        gold_dir=Path(gold_dir),
        pred_dir=Path(pred_dir) if pred_dir else None,
        allow_embedded_predictions=allow_embedded_predictions,
    )


def _kg_retriever(metadata_db_path: str, graph_db_path: str) -> KGRetriever:
    return KGRetriever(metadata_db_path=metadata_db_path, graph_db_path=graph_db_path)


def _hybrid_retriever(metadata_db_path: str, graph_db_path: str) -> HybridRetriever:
    return HybridRetriever(_kg_retriever(metadata_db_path, graph_db_path))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
