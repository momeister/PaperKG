from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from query.hybrid_retriever import HybridRetriever
from query.kg_retriever import Evidence, Source
from query.llm_router import LLMRouter


@dataclass
class Hypothesis:
    statement: str
    rationale: str
    sources: list[Source] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "statement": self.statement,
            "rationale": self.rationale,
            "sources": [source.to_dict() for source in self.sources],
            "evidence": [item.to_dict() for item in self.evidence],
            "score": round(float(self.score), 4),
        }


class HypothesisGenerator:
    """
    Generates sourced cross-domain hypotheses from existing KG evidence.
    """

    def __init__(
        self,
        retriever: HybridRetriever | None = None,
        llm_router: LLMRouter | None = None,
    ) -> None:
        self.retriever = retriever or HybridRetriever()
        self.llm_router = llm_router

    def generate(
        self,
        topic: str | None = None,
        paper_id: str | None = None,
        limit: int = 10,
        provider: str | None = None,
        model: str | None = None,
    ) -> list[Hypothesis]:
        evidence_hits = self._hits(topic=topic, paper_id=paper_id, limit=limit)
        hypotheses = self._from_cross_domain_hints(evidence_hits)
        hypotheses.extend(self._from_shared_methods(evidence_hits))
        hypotheses.sort(key=lambda item: item.score, reverse=True)

        selected = hypotheses[: max(0, int(limit))]
        if self.llm_router is not None and selected:
            return self._refine(selected, provider=provider, model=model)
        return selected

    def _hits(
        self,
        topic: str | None,
        paper_id: str | None,
        limit: int,
    ):
        if paper_id:
            detail = self.retriever.paper_detail(paper_id)
            if not detail:
                return []
            latest = detail.get("latest_extraction") or {}
            source = Source(**detail["source"])
            evidence = []
            for field_name, kind in [
                ("cross_domain_hints", "cross_domain_hint"),
                ("methods", "method"),
                ("concepts", "concept"),
            ]:
                for item in latest.get(field_name) or []:
                    evidence.append(
                        Evidence(
                            paper_id=source.paper_id,
                            kind=kind,
                            field=field_name,
                            text=_item_text(item),
                            score=1.0,
                            metadata=item if isinstance(item, dict) else {},
                        )
                    )
            return [_SyntheticHit(source=source, evidence=evidence, score=float(len(evidence)))]

        if topic:
            return self.retriever.search(topic, limit=limit)
        return []

    def _from_cross_domain_hints(self, hits) -> list[Hypothesis]:
        hypotheses: list[Hypothesis] = []
        for hit in hits:
            for evidence in hit.evidence:
                if evidence.kind != "cross_domain_hint":
                    continue
                field = str(evidence.metadata.get("field") or evidence.metadata.get("domain") or "another field")
                why = str(evidence.metadata.get("why_applicable") or evidence.metadata.get("reason") or evidence.text)
                statement = (
                    f"Methods or findings from {hit.source.title or hit.source.paper_id} "
                    f"may be relevant to {field}."
                )
                hypotheses.append(
                    Hypothesis(
                        statement=statement,
                        rationale=why,
                        sources=[hit.source],
                        evidence=[evidence],
                        score=evidence.score + 1.0,
                    )
                )
        return hypotheses

    def _from_shared_methods(self, hits) -> list[Hypothesis]:
        by_label: dict[str, list[tuple[Any, Evidence]]] = defaultdict(list)
        for hit in hits:
            for evidence in hit.evidence:
                if evidence.kind not in {"method", "concept"}:
                    continue
                label = _label_from_evidence(evidence)
                if label:
                    by_label[label.lower()].append((hit, evidence))

        hypotheses: list[Hypothesis] = []
        for label, grouped in by_label.items():
            paper_ids = {hit.source.paper_id for hit, _ in grouped}
            if len(paper_ids) < 2:
                continue
            selected = grouped[:4]
            sources = [hit.source for hit, _ in selected]
            evidence = [item for _, item in selected]
            title_list = ", ".join(source.title or source.paper_id for source in sources[:3])
            hypotheses.append(
                Hypothesis(
                    statement=f"The shared {evidence[0].kind} '{label}' may connect these papers.",
                    rationale=f"The label appears in multiple local KG records: {title_list}.",
                    sources=sources,
                    evidence=evidence,
                    score=sum(item.score for item in evidence) + len(paper_ids),
                )
            )
        return hypotheses

    def _refine(
        self,
        hypotheses: list[Hypothesis],
        provider: str | None,
        model: str | None,
    ) -> list[Hypothesis]:
        refined: list[Hypothesis] = []
        for hypothesis in hypotheses:
            prompt = (
                "Rewrite this cross-domain hypothesis in one precise sentence. "
                "Use only the supplied statement and rationale.\n\n"
                f"Statement: {hypothesis.statement}\n"
                f"Rationale: {hypothesis.rationale}"
            )
            overrides = {"temperature": 0.2, "max_tokens": 300}
            if model:
                overrides["model"] = model
            try:
                assert self.llm_router is not None
                text = self.llm_router.chat(
                    [
                        {"role": "system", "content": "Return one grounded hypothesis sentence."},
                        {"role": "user", "content": prompt},
                    ],
                    provider=provider,
                    overrides=overrides,
                ).strip()
            except Exception:
                text = ""
            if text:
                hypothesis = Hypothesis(
                    statement=text,
                    rationale=hypothesis.rationale,
                    sources=hypothesis.sources,
                    evidence=hypothesis.evidence,
                    score=hypothesis.score,
                )
            refined.append(hypothesis)
        return refined


@dataclass
class _SyntheticHit:
    source: Source
    evidence: list[Evidence]
    score: float


def _label_from_evidence(evidence: Evidence) -> str:
    for key in ("label", "term", "field"):
        value = evidence.metadata.get(key)
        if value:
            return str(value)
    return evidence.text.split(":", 1)[0].strip()


def _item_text(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("label", "statement", "field", "why_applicable", "description", "context"):
            if item.get(key):
                return str(item[key])
        return " ".join(str(value) for value in item.values() if isinstance(value, (str, int, float)))
    return str(item or "")
