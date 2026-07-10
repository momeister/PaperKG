import { useEffect, useState } from "react";
import {
  BookOpen,
  ChevronDown,
  ChevronRight,
  Download,
  GitMerge,
  GraduationCap,
  History,
  Loader2,
  Sparkles,
  Trash2,
} from "lucide-react";

import { api } from "../api";
import { colorVarsForPaperId } from "../citationColors";
import type { Answer, ParallelSession, ParallelSessionSummary, VerificationSource } from "../types";
import {
  AnswerText,
  bestEvidenceIndex,
  citationIds,
  citationMetasFor,
  sameCitation,
  verificationSourcesFor,
} from "./AssistantPage";
import { buildParallelSessionMarkdown, STAGE_STATUS_LABEL } from "./parallelHelpers";
import { ProfessorReviewCard } from "./ProfessorReviewCard";

/** Build the shared source pool for a Parallel session (overview + synthesis + stage
 * reviews), so variant citations resolve to openable sources with their evidence. */
export function useParallelPool(session: ParallelSession): VerificationSource[] {
  const [pool, setPool] = useState<VerificationSource[]>([]);
  useEffect(() => {
    let cancelled = false;
    const payloads = [
      session.overview_payload,
      session.synthesis_payload,
      ...(session.stages ?? []).map((stage) => stage.review_payload),
    ].filter(Boolean) as Answer[];
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
  }, [session]);
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

  /** Client-side export: the session payload already carries everything. */
  function exportMarkdown() {
    const markdown = buildParallelSessionMarkdown(session);
    const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `parallel-recherche-${session.id}.md`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  const overviewAnswer = session.overview_payload;
  const synthesisAnswer = session.synthesis_payload;
  const followups = session.followups ?? [];
  const stages = session.stages ?? [];

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
            className="button button-compact"
            onClick={exportMarkdown}
            title="Gesamte Recherche (Etappen, Varianten, Ergebnisse, Reviews) als Markdown-Datei exportieren"
          >
            <Download size={13} />
            <span>Export</span>
          </button>
          <button
            type="button"
            className="button button-compact button-primary"
            onClick={synthesize}
            disabled={busy || session.variants.length === 0}
            title="Professor-End-Review: das gesamte Vorhaben über alle Etappen auswerten"
          >
            {busy ? <Loader2 size={13} className="spin" /> : <GraduationCap size={13} />}
            <span>End-Review</span>
          </button>
        </div>
      </header>

      {stages.length > 0 ? (
        <div className="parallel-roadmap" title="Etappen-Roadmap des Vorhabens">
          {stages.map((stage, index) => (
            <span
              key={stage.id}
              className={`parallel-roadmap__stage parallel-roadmap__stage--${stage.status}`}
              title={stage.goal || stage.name}
            >
              <span className="parallel-roadmap__index">{index + 1}</span>
              <span className="parallel-roadmap__name">{stage.name}</span>
              <span className="parallel-roadmap__status">{STAGE_STATUS_LABEL[stage.status] ?? stage.status}</span>
            </span>
          ))}
        </div>
      ) : null}

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
            <Sparkles size={13} /> End-Review &amp; Empfehlung
          </div>
          <ProfessorReviewCard answer={synthesisAnswer} onOpenCitation={onOpenCitation} />
        </section>
      ) : null}

      <p className="parallel-panel__hint">
        Die <strong>Etappen mit den Methoden zum Ausprobieren</strong> und deine Ergebnisse findest
        du rechts im Notizen-Tab <strong>„Ergebnisse"</strong>. Trag dort ein, was dein KI-Tool
        produziert hat — danach hier <strong>„End-Review"</strong>.
      </p>
    </div>
  );
}

/** Browser over the server-persisted parallel sessions of a project: reopen or delete
 * earlier Forschungsvorhaben (they are stored in DuckDB, not just localStorage). */
export function ParallelSessionBrowser({
  projectId,
  activeSessionId,
  defaultOpen = false,
  onOpen,
}: {
  projectId: string;
  activeSessionId?: string;
  defaultOpen?: boolean;
  onOpen: (summary: ParallelSessionSummary) => void;
}) {
  const [sessions, setSessions] = useState<ParallelSessionSummary[]>([]);
  const [open, setOpen] = useState(defaultOpen);

  useEffect(() => {
    let cancelled = false;
    api
      .listParallelSessions(projectId)
      .then((res) => {
        if (!cancelled) setSessions(res.sessions);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [projectId, activeSessionId]);

  async function remove(sessionId: string) {
    await api.deleteParallelSession(sessionId).catch(() => {});
    setSessions((prev) => prev.filter((item) => item.id !== sessionId));
  }

  const items = sessions.filter((item) => item.id !== activeSessionId);
  if (!items.length) return null;

  return (
    <section className="parallel-session-browser">
      <button
        type="button"
        className="parallel-session-browser__toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <History size={14} />
        <span>Frühere Sessions ({items.length})</span>
      </button>
      {open ? (
        <div className="parallel-session-browser__list">
          {items.map((item) => (
            <div key={item.id} className="parallel-session-browser__row">
              <button
                type="button"
                className="parallel-session-browser__open"
                onClick={() => onOpen(item)}
                title="Session öffnen"
              >
                <strong>{item.question}</strong>
                <span className="muted">
                  {[
                    item.status === "synthesized" ? "End-Review vorhanden" : item.status,
                    `${item.variant_count} Varianten`,
                    item.stage_count ? `${item.stage_count} Etappen` : "",
                    item.updated_timestamp ? new Date(item.updated_timestamp).toLocaleString() : "",
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </button>
              <button
                type="button"
                className="icon-button nav-delete-btn"
                title="Session löschen"
                onClick={() => void remove(item.id)}
              >
                <Trash2 size={13} />
              </button>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}
