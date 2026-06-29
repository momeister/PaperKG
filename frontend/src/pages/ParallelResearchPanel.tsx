import { useEffect, useState } from "react";
import { BookOpen, ChevronDown, ChevronRight, GitMerge, Loader2, Sparkles } from "lucide-react";

import { api } from "../api";
import { colorVarsForPaperId } from "../citationColors";
import type { Answer, ParallelSession, VerificationSource } from "../types";
import {
  AnswerText,
  bestEvidenceIndex,
  citationIds,
  citationMetasFor,
  sameCitation,
  verificationSourcesFor,
} from "./AssistantPage";

/** Build the shared source pool for a Parallel session (overview + synthesis sources),
 * so variant citations resolve to openable sources with their evidence. */
export function useParallelPool(session: ParallelSession): VerificationSource[] {
  const [pool, setPool] = useState<VerificationSource[]>([]);
  useEffect(() => {
    let cancelled = false;
    const payloads = [session.overview_payload, session.synthesis_payload].filter(
      Boolean,
    ) as Answer[];
    void Promise.all(payloads.map((payload) => verificationSourcesFor(payload).catch(() => [])))
      .then((lists) => {
        if (cancelled) return;
        const merged = new Map<string, VerificationSource>();
        for (const list of lists) {
          for (const source of list) {
            if (!merged.has(source.paper_id)) merged.set(source.paper_id, source);
          }
        }
        setPool(Array.from(merged.values()));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [session.overview_payload, session.synthesis_payload]);
  return pool;
}

/** Renders a grounded Answer (overview / feedback / synthesis) with the shared citation UI:
 * citations are clickable and open the PDF pane via ``onOpenCitation``. */
export function AnswerWithCitations({
  answer,
  onOpenCitation,
}: {
  answer: Answer;
  onOpenCitation: (source: VerificationSource, evidenceIndex: number) => void;
}) {
  const [verification, setVerification] = useState<VerificationSource[]>([]);
  useEffect(() => {
    let cancelled = false;
    void verificationSourcesFor(answer)
      .then((sources) => {
        if (!cancelled) setVerification(sources);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [answer]);

  return (
    <div className="answer-text">
      <AnswerText
        answer={answer.answer}
        citationLinks={answer.citation_links ?? []}
        getCitationMeta={(citation, context, start) =>
          citationMetasFor(verification, citation, context, answer.citation_links ?? [], start)
        }
        onCitationClick={() => {}}
        onCitationMetaClick={(meta) => onOpenCitation(meta.source, meta.evidenceIndex)}
      />
      {verification.length > 0 ? (
        <div className="research-tree-sources">
          {verification.map((src) => (
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

/** Renders free text that may contain ``[arxiv:…]`` / ``[europepmc:…, openalex:…]`` tokens as
 * clickable chips. A bracket with several ids becomes several chips; each chip opens its source
 * **in the PDF/meta pane** (the in-pane opener ingests on demand and only then falls back to the
 * web/grey view — it never bounces straight to the external site). */
export function CitedInline({
  text,
  pool,
  onOpen,
}: {
  text: string;
  pool: VerificationSource[];
  onOpen: (source: VerificationSource, evidenceIndex: number) => void;
}) {
  if (!text) return null;
  const parts = text.split(/(\[[^\]]+\])/g);

  function openCite(id: string, source: VerificationSource | undefined) {
    if (source) {
      onOpen(source, bestEvidenceIndex(source, text));
      return;
    }
    // Not resolved in the session pool yet — still open it in the pane (which ingests the PDF on
    // demand, then falls back to the grey/web view) instead of opening the external site directly.
    onOpen({ paper_id: id, title: id, pdf_available: false, evidence: [] }, 0);
  }

  return (
    <>
      {parts.map((part, index) => {
        const match = /^\[(.+)\]$/.exec(part);
        if (!match) return <span key={index}>{part}</span>;
        const ids = citationIds(match[1]).filter(
          (id) => /^[a-z][a-z0-9_]*::?[^\s]/i.test(id) || /^10\.\d{4}/.test(id),
        );
        if (ids.length === 0) return <span key={index}>{part}</span>;
        return (
          <span key={index} className="parallel-cite-group">
            <span className="parallel-cite-bracket">[</span>
            {ids.map((id, idIndex) => {
              const source = pool.find((item) => sameCitation(item.paper_id, id));
              return (
                <span key={id + idIndex}>
                  {idIndex > 0 ? <span className="parallel-cite-bracket">, </span> : null}
                  <button
                    type="button"
                    className="citation-link citation-link--mapped parallel-cite"
                    style={colorVarsForPaperId(id, 0)}
                    title={source?.title || id}
                    onClick={() => openCite(id, source)}
                  >
                    {id}
                  </button>
                </span>
              );
            })}
            <span className="parallel-cite-bracket">]</span>
          </span>
        );
      })}
    </>
  );
}

/** Assistant-side panel: the *explanation* (grounded overview) and the final cross-variant
 * synthesis. The methods (variants) and result entry live in the Notes "Ergebnisse" tab. */
export function ParallelResearchPanel({
  session,
  loading,
  followupLoading,
  provider,
  model,
  paperIds,
  onChange,
  onOpenCitation,
}: {
  session: ParallelSession;
  loading?: boolean;
  followupLoading?: boolean;
  provider?: string | null;
  model?: string | null;
  paperIds?: string[];
  onChange: (session: ParallelSession) => void;
  onOpenCitation: (source: VerificationSource, evidenceIndex: number) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [overviewOpen, setOverviewOpen] = useState(true);

  async function synthesize() {
    setBusy(true);
    try {
      const res = await api.synthesizeParallelSession(session.id, { paper_ids: paperIds, provider, model });
      onChange(res.session);
    } finally {
      setBusy(false);
    }
  }

  const overviewAnswer = session.overview_payload;
  const synthesisAnswer = session.synthesis_payload;
  const followups = session.followups ?? [];

  return (
    <div className="parallel-panel">
      <header className="parallel-panel__head">
        <div>
          <span className="parallel-panel__eyebrow">
            <GitMerge size={13} /> Parallel Research
          </span>
          <strong className="parallel-panel__question">{session.question}</strong>
        </div>
        <div className="parallel-panel__head-actions">
          <button
            type="button"
            className="button button-compact button-primary"
            onClick={synthesize}
            disabled={busy || session.variants.length === 0}
            title="Eingesendete Ergebnisse vergleichen und beste Variante bestimmen"
          >
            {busy ? <Loader2 size={13} className="spin" /> : <GitMerge size={13} />}
            <span>Beste Variante analysieren</span>
          </button>
        </div>
      </header>

      {overviewAnswer ? (
        <section className="parallel-overview">
          <button
            type="button"
            className="parallel-overview__toggle"
            onClick={() => setOverviewOpen((open) => !open)}
            aria-expanded={overviewOpen}
          >
            {overviewOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            <BookOpen size={14} />
            <span>Worum geht es &amp; wie gehst du ran?</span>
          </button>
          {overviewOpen ? (
            <div className="parallel-overview__body">
              <AnswerWithCitations answer={overviewAnswer} onOpenCitation={onOpenCitation} />
            </div>
          ) : null}
        </section>
      ) : loading ? (
        <div className="parallel-loading">
          <Loader2 size={16} className="spin" />
          <span>Erklärung wird erstellt…</span>
        </div>
      ) : null}

      {followups.length > 0 || followupLoading ? (
        <section className="parallel-followups">
          {followups.map((followup) => (
            <div key={followup.id} className="parallel-followup">
              <div className="parallel-followup__question">{followup.question}</div>
              {followup.answer_payload ? (
                <AnswerWithCitations answer={followup.answer_payload} onOpenCitation={onOpenCitation} />
              ) : null}
            </div>
          ))}
          {followupLoading ? (
            <div className="parallel-loading">
              <Loader2 size={16} className="spin" />
              <span>Antwort wird erstellt…</span>
            </div>
          ) : null}
        </section>
      ) : null}

      {synthesisAnswer ? (
        <section className="parallel-synthesis">
          <div className="parallel-synthesis__label">
            <Sparkles size={13} /> Gesamtanalyse &amp; Empfehlung
          </div>
          <AnswerWithCitations answer={synthesisAnswer} onOpenCitation={onOpenCitation} />
        </section>
      ) : null}

      <p className="parallel-panel__hint">
        Die <strong>Methoden zum Ausprobieren</strong> und deine Ergebnisse findest du rechts im
        Notizen-Tab <strong>„Ergebnisse"</strong>. Trag dort ein, was dein KI-Tool produziert hat —
        danach hier <strong>„Beste Variante analysieren"</strong>.
      </p>
    </div>
  );
}
