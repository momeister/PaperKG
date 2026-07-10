import { useEffect, useState } from "react";

import { colorVarsForPaperId } from "../citationColors";
import type { Answer, ProfessorReview, VerificationSource } from "../types";
import { verificationSourcesFor } from "./assistantHelpers";
import { AnswerWithCitations, CitedInline } from "./ParallelResearchPanel";

const VERDICT_LABEL: Record<string, string> = {
  weiterverfolgen: "Weiterverfolgen",
  anpassen: "Anpassen",
  verwerfen: "Verwerfen",
};

const SECTION_TITLES: Array<{ key: keyof ProfessorReview; title: string }> = [
  { key: "staerken", title: "Stärken" },
  { key: "probleme", title: "Fehler & Probleme" },
  { key: "ideen", title: "Ideen" },
  { key: "naechste_schritte", title: "Nächste Schritte" },
];

/** Renders a professor review (Answer with ``professor_review``) as structured sections
 * with clickable `[arxiv:...]` chips. Legacy free-text answers (no ``professor_review``)
 * fall back to the plain grounded-answer rendering — old sessions keep working. */
export function ProfessorReviewCard({
  answer,
  onOpenCitation,
}: {
  answer: Answer;
  onOpenCitation: (source: VerificationSource, evidenceIndex: number) => void;
}) {
  const review = answer.professor_review;
  const [pool, setPool] = useState<VerificationSource[]>([]);
  useEffect(() => {
    if (!review) return;
    let cancelled = false;
    void verificationSourcesFor(answer)
      .then((sources) => {
        if (!cancelled) setPool(sources);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [answer, review]);

  if (!review) {
    return <AnswerWithCitations answer={answer} onOpenCitation={onOpenCitation} />;
  }

  const cite = (text: string) => <CitedInline text={text} pool={pool} onOpen={onOpenCitation} />;

  const list = (items: string[] | undefined, title: string) =>
    items && items.length ? (
      <div className="professor-review__section" key={title}>
        <span className="professor-review__title">{title}</span>
        <ul>
          {items.map((item, index) => (
            <li key={index}>{cite(item)}</li>
          ))}
        </ul>
      </div>
    ) : null;

  const head = review.kind === "final" ? review.gesamtverstaendnis : review.verstaendnis;

  return (
    <div className="professor-review">
      {head?.trim() ? (
        <div className="professor-review__section">
          <span className="professor-review__title">
            {review.kind === "final" ? "Gesamtverständnis" : "Verständnis"}
          </span>
          <p>{cite(head)}</p>
        </div>
      ) : null}

      {review.kind === "final" && review.etappen_zusammenfassung?.length ? (
        <div className="professor-review__section">
          <span className="professor-review__title">Etappen-Fazit</span>
          <ul>
            {review.etappen_zusammenfassung.map((item, index) => (
              <li key={item.stage_id || index}>
                <strong>{item.name || "Etappe"}:</strong> {cite(item.fazit)}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {SECTION_TITLES.map(({ key, title }) => list(review[key] as string[] | undefined, title))}

      {review.kind === "stage" && review.varianten_bewertung?.length ? (
        <div className="professor-review__section">
          <span className="professor-review__title">Varianten-Bewertung</span>
          <ul className="professor-review__verdicts">
            {review.varianten_bewertung.map((verdict, index) => (
              <li key={verdict.variant_id || index}>
                <span className={`professor-verdict professor-verdict--${verdict.urteil}`}>
                  {VERDICT_LABEL[verdict.urteil] ?? verdict.urteil}
                </span>
                <strong>{verdict.name}</strong>
                {verdict.begruendung ? <> — {cite(verdict.begruendung)}</> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {review.kind === "final" ? (
        <>
          {list(review.offene_punkte, "Offene Punkte")}
          {review.finale_antwort?.trim() ? (
            <div className="professor-review__section professor-review__final">
              <span className="professor-review__title">Finale Antwort</span>
              <p>{cite(review.finale_antwort)}</p>
            </div>
          ) : null}
        </>
      ) : null}

      {pool.length > 0 ? (
        <div className="research-tree-sources">
          {pool.map((src) => (
            <button
              key={src.paper_id}
              type="button"
              className="citation-link citation-link--mapped"
              style={colorVarsForPaperId(src.paper_id, 0)}
              onClick={() => onOpenCitation(src, 0)}
              title={src.title || src.paper_id}
            >
              {src.title ? src.title.slice(0, 40) + (src.title.length > 40 ? "…" : "") : src.paper_id}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
