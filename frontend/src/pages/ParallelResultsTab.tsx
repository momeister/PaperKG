import { useEffect, useState } from "react";
import {
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  Loader2,
  NotebookPen,
  Pencil,
  Play,
  Plus,
  Send,
  Sparkles,
  Trash2,
} from "lucide-react";

import { api, streamAgentDispatch } from "../api";
import type {
  AgentConfig,
  AgentDispatchEvent,
  AgentHandoffResponse,
  ParallelEntry,
  ParallelSession,
  ParallelVariant,
  VerificationSource,
} from "../types";
import { AnswerWithCitations, CitedInline, useParallelPool } from "./ParallelResearchPanel";

const STATUS_LABEL: Record<string, string> = {
  vorgeschlagen: "Vorgeschlagen",
  in_arbeit: "In Arbeit",
  ergebnis: "Ergebnis da",
  verworfen: "Verworfen",
};

type ResultBlock = { user: ParallelEntry; feedback: ParallelEntry | null };

/** Pair each submitted result with the KI-Einordnung that follows it (entries are ordered
 * by creation: a user result, then optionally its assistant feedback). */
function resultBlocks(entries: ParallelEntry[]): ResultBlock[] {
  const blocks: ResultBlock[] = [];
  for (let i = 0; i < entries.length; i += 1) {
    const entry = entries[i];
    if (entry.role !== "user") continue;
    const next = entries[i + 1];
    blocks.push({ user: entry, feedback: next && next.role === "assistant" ? next : null });
  }
  return blocks;
}

/** Notes-side "Ergebnisse" tab: the methods to try (variants + ready-to-copy prompts) plus
 * per-variant result entry, the grounded KI-Einordnung, and an opt-in "In Notiz übernehmen". */
export function ParallelResultsTab({
  session,
  onChange,
  scope,
  onOpenCitation,
  onTakeIntoNote,
}: {
  session: ParallelSession;
  onChange: (session: ParallelSession) => void;
  scope: { paperIds?: string[]; provider?: string | null; model?: string | null };
  onOpenCitation: (source: VerificationSource, evidenceIndex: number) => void;
  onTakeIntoNote: (markdown: string) => void | Promise<unknown>;
}) {
  const pool = useParallelPool(session);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [requestFeedback, setRequestFeedback] = useState(true);
  const [pending, setPending] = useState<string | null>(null);
  const [takenId, setTakenId] = useState<string | null>(null);
  const [busy, setBusy] = useState<"add" | "generate" | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<Partial<ParallelVariant>>({});
  const [copiedId, setCopiedId] = useState<string | null>(null);
  // Each variant card is collapsible; default open (undefined ⇒ open).
  const [openMap, setOpenMap] = useState<Record<string, boolean>>({});
  // Desktop-agent hand-off (compile a variant into a task brief for UI-TARS et al.).
  const [agentConfig, setAgentConfig] = useState<AgentConfig | null>(null);
  const [handoff, setHandoff] = useState<Record<string, AgentHandoffResponse>>({});
  const [handoffBusy, setHandoffBusy] = useState<string | null>(null);
  const [handoffOpen, setHandoffOpen] = useState<Record<string, boolean>>({});
  const [briefCopiedId, setBriefCopiedId] = useState<string | null>(null);
  const [dispatchLog, setDispatchLog] = useState<Record<string, AgentDispatchEvent[]>>({});
  const [dispatching, setDispatching] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api.getAgentConfig().then((cfg) => { if (alive) setAgentConfig(cfg); }).catch(() => {});
    return () => { alive = false; };
  }, []);

  function toggleOpen(variantId: string) {
    setOpenMap((prev) => ({ ...prev, [variantId]: prev[variantId] === false ? true : false }));
  }

  const scopeBody = { paper_ids: scope.paperIds, provider: scope.provider, model: scope.model };

  async function refresh() {
    const res = await api.getParallelSession(session.id);
    onChange(res.session);
  }

  async function submit(variantId: string) {
    const content = (drafts[variantId] ?? "").trim();
    if (!content || pending) return;
    setPending(variantId);
    try {
      const res = await api.addParallelEntry(variantId, {
        content,
        request_feedback: requestFeedback,
        ...scopeBody,
      });
      onChange(res.session);
      setDrafts((prev) => ({ ...prev, [variantId]: "" }));
    } finally {
      setPending(null);
    }
  }

  async function addVariant() {
    setBusy("add");
    try {
      await api.addParallelVariant(session.id, { name: "Neue Variante" });
      await refresh();
    } finally {
      setBusy(null);
    }
  }

  async function generateMore() {
    setBusy("generate");
    try {
      const res = await api.generateParallelVariants(session.id, { variant_count: 2, ...scopeBody });
      onChange(res.session);
    } finally {
      setBusy(null);
    }
  }

  async function saveEdit(variant: ParallelVariant) {
    await api.updateParallelVariant(variant.id, {
      name: editDraft.name ?? variant.name,
      approach: editDraft.approach ?? variant.approach,
      rationale: editDraft.rationale ?? variant.rationale,
      suggested_prompt: editDraft.suggested_prompt ?? variant.suggested_prompt,
    });
    setEditing(null);
    setEditDraft({});
    await refresh();
  }

  async function setStatus(variantId: string, status: string) {
    await api.updateParallelVariant(variantId, { status });
    await refresh();
  }

  async function removeVariant(variantId: string) {
    await api.deleteParallelVariant(variantId);
    await refresh();
  }

  async function removeEntry(entry: ParallelEntry, feedback: ParallelEntry | null) {
    await api.deleteParallelEntry(entry.id);
    if (feedback) await api.deleteParallelEntry(feedback.id).catch(() => {});
    await refresh();
  }

  function copyPrompt(variant: ParallelVariant) {
    if (!variant.suggested_prompt) return;
    void navigator.clipboard?.writeText(variant.suggested_prompt).then(() => {
      setCopiedId(variant.id);
      window.setTimeout(() => setCopiedId((id) => (id === variant.id ? null : id)), 1500);
    });
  }

  /** Toggle the desktop-agent hand-off panel; compile the task brief on first open. */
  async function openHandoff(variant: ParallelVariant) {
    if (handoffOpen[variant.id]) {
      setHandoffOpen((p) => ({ ...p, [variant.id]: false }));
      return;
    }
    setHandoffOpen((p) => ({ ...p, [variant.id]: true }));
    if (handoff[variant.id] || handoffBusy) return;
    setHandoffBusy(variant.id);
    try {
      const res = await api.parallelVariantHandoff(variant.id, scopeBody);
      setHandoff((p) => ({ ...p, [variant.id]: res }));
    } finally {
      setHandoffBusy(null);
    }
  }

  function copyBrief(variant: ParallelVariant) {
    const text = handoff[variant.id]?.text;
    if (!text) return;
    void navigator.clipboard?.writeText(text).then(() => {
      setBriefCopiedId(variant.id);
      window.setTimeout(() => setBriefCopiedId((id) => (id === variant.id ? null : id)), 1500);
    });
  }

  /** Kanal B: POST the brief to the local bridge and stream the agent's run progress. */
  async function runOnAgent(variant: ParallelVariant) {
    const text = handoff[variant.id]?.text;
    if (!text || dispatching) return;
    setDispatching(variant.id);
    setDispatchLog((p) => ({ ...p, [variant.id]: [] }));
    try {
      await streamAgentDispatch({ task: text, variant_id: variant.id }, (event) => {
        setDispatchLog((p) => ({ ...p, [variant.id]: [...(p[variant.id] ?? []), event] }));
      });
      await refresh();
    } catch {
      setDispatchLog((p) => ({
        ...p,
        [variant.id]: [...(p[variant.id] ?? []), { status: "error", error: "Bridge nicht erreichbar" }],
      }));
    } finally {
      setDispatching(null);
    }
  }

  function takeIntoNote(variant: ParallelVariant, block: ResultBlock) {
    const quoted = block.user.content.split("\n").map((line) => `> ${line}`).join("\n");
    let markdown = `> **${variant.name} — Ergebnis**\n>\n${quoted}\n`;
    const feedbackText = block.feedback?.answer_payload?.answer || block.feedback?.content || "";
    if (feedbackText.trim()) {
      markdown += `\n**KI-Einordnung:** ${feedbackText.trim()}\n`;
    }
    void Promise.resolve(onTakeIntoNote(markdown)).then(() => {
      setTakenId(block.user.id);
      window.setTimeout(() => setTakenId((id) => (id === block.user.id ? null : id)), 1500);
    });
  }

  return (
    <section className="panel parallel-results-tab">
      <div className="parallel-results-tab__intro">
        Hier sind die <strong>Methoden zum Ausprobieren</strong>. Trag pro Variante ein, was dein
        KI-Tool produziert hat — die KI ordnet es gegroundet ein, und mit
        {" "}<strong>„In Notiz übernehmen"</strong> wandert es in deine Notiz.
      </div>

      <div className="parallel-results-tab__actions">
        <button type="button" className="button button-compact" onClick={generateMore} disabled={busy !== null}>
          {busy === "generate" ? <Loader2 size={13} className="spin" /> : <Sparkles size={13} />}
          <span>Mehr Varianten</span>
        </button>
        <button type="button" className="button button-compact" onClick={addVariant} disabled={busy !== null}>
          <Plus size={13} />
          <span>Eigene Variante</span>
        </button>
      </div>

      {session.variants.length === 0 ? (
        <div className="muted-row">Noch keine Varianten — „Mehr Varianten" generieren oder „Eigene Variante" hinzufügen.</div>
      ) : null}

      {session.variants.map((variant, index) => {
        const isEditing = editing === variant.id;
        const blocks = resultBlocks(variant.entries);
        const isOpen = openMap[variant.id] !== false;
        const expanded = isEditing || isOpen;
        return (
          <article key={variant.id} className={`parallel-result parallel-variant--${variant.status}`}>
            <div className="parallel-result__head">
              <button
                type="button"
                className="icon-button parallel-result__toggle"
                title={isOpen ? "Einklappen" : "Ausklappen"}
                aria-expanded={isOpen}
                onClick={() => toggleOpen(variant.id)}
              >
                {isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              </button>
              <span className="parallel-variant__index">V{index + 1}</span>
              {isEditing ? (
                <input
                  className="parallel-variant__name-input"
                  value={editDraft.name ?? variant.name}
                  onChange={(e) => setEditDraft((d) => ({ ...d, name: e.target.value }))}
                />
              ) : (
                <strong className="parallel-result__name">{variant.name}</strong>
              )}
              <span className={`parallel-badge parallel-badge--${variant.origin}`}>
                {variant.origin === "ai" ? "KI" : "Eigen"}
              </span>
              <select
                className="parallel-variant__status"
                value={variant.status}
                onChange={(e) => void setStatus(variant.id, e.target.value)}
                aria-label="Status"
              >
                {Object.entries(STATUS_LABEL).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
              <span className="parallel-variant__spacer" />
              {isEditing ? (
                <button type="button" className="icon-button" title="Speichern" onClick={() => void saveEdit(variant)}>
                  <Check size={14} />
                </button>
              ) : (
                <button
                  type="button"
                  className="icon-button"
                  title="Bearbeiten"
                  onClick={() => {
                    setEditing(variant.id);
                    setEditDraft({});
                  }}
                >
                  <Pencil size={13} />
                </button>
              )}
              <button type="button" className="icon-button nav-delete-btn" title="Variante löschen" onClick={() => void removeVariant(variant.id)}>
                <Trash2 size={13} />
              </button>
            </div>

            {!expanded ? null : isEditing ? (
              <div className="parallel-variant__edit">
                <label>
                  Ansatz
                  <textarea
                    value={editDraft.approach ?? variant.approach}
                    onChange={(e) => setEditDraft((d) => ({ ...d, approach: e.target.value }))}
                    rows={2}
                  />
                </label>
                <label>
                  Begründung
                  <textarea
                    value={editDraft.rationale ?? variant.rationale}
                    onChange={(e) => setEditDraft((d) => ({ ...d, rationale: e.target.value }))}
                    rows={2}
                  />
                </label>
                <label>
                  Prompt
                  <textarea
                    value={editDraft.suggested_prompt ?? variant.suggested_prompt}
                    onChange={(e) => setEditDraft((d) => ({ ...d, suggested_prompt: e.target.value }))}
                    rows={5}
                  />
                </label>
              </div>
            ) : (
              <>
                {variant.approach ? (
                  <p className="parallel-variant__text">
                    <span className="parallel-variant__text-label">Ansatz:</span>{" "}
                    <CitedInline text={variant.approach} pool={pool} onOpen={onOpenCitation} />
                  </p>
                ) : null}
                {variant.rationale ? (
                  <p className="parallel-variant__text">
                    <span className="parallel-variant__text-label">Warum:</span>{" "}
                    <CitedInline text={variant.rationale} pool={pool} onOpen={onOpenCitation} />
                  </p>
                ) : null}
                {variant.suggested_prompt ? (
                  <details className="parallel-variant__prompt">
                    <summary className="parallel-variant__prompt-head">
                      <ChevronRight size={13} className="parallel-variant__prompt-caret" />
                      <span>Prompt für dein Coding-Tool</span>
                    </summary>
                    <div className="parallel-variant__prompt-body">
                      <div className="parallel-variant__prompt-toolbar">
                        <button type="button" className="button button-compact" onClick={() => copyPrompt(variant)}>
                          {copiedId === variant.id ? <Check size={12} /> : <Copy size={12} />}
                          <span>{copiedId === variant.id ? "Kopiert" : "Kopieren"}</span>
                        </button>
                      </div>
                      <pre>{variant.suggested_prompt}</pre>
                    </div>
                  </details>
                ) : null}

                <div className="parallel-variant__handoff">
                  <button
                    type="button"
                    className="button button-compact"
                    onClick={() => void openHandoff(variant)}
                    disabled={handoffBusy === variant.id}
                    title="Diese Variante als Aufgaben-Brief an einen Desktop-Agenten (z. B. UI-TARS) übergeben"
                  >
                    {handoffBusy === variant.id ? <Loader2 size={13} className="spin" /> : <Bot size={13} />}
                    <span>An Desktop-Agent übergeben</span>
                  </button>
                  {handoffOpen[variant.id] && handoff[variant.id] ? (
                    <div className="parallel-variant__handoff-body">
                      <div className="parallel-variant__prompt-toolbar">
                        <button type="button" className="button button-compact" onClick={() => copyBrief(variant)}>
                          {briefCopiedId === variant.id ? <Check size={12} /> : <Copy size={12} />}
                          <span>{briefCopiedId === variant.id ? "Kopiert" : "Brief kopieren"}</span>
                        </button>
                        {agentConfig?.enabled ? (
                          <button
                            type="button"
                            className="button button-compact button-primary"
                            onClick={() => void runOnAgent(variant)}
                            disabled={dispatching === variant.id}
                          >
                            {dispatching === variant.id ? <Loader2 size={12} className="spin" /> : <Play size={12} />}
                            <span>Jetzt ausführen</span>
                          </button>
                        ) : null}
                      </div>
                      <pre className="parallel-variant__handoff-text">{handoff[variant.id].text}</pre>
                      <p className="parallel-variant__handoff-hint muted">
                        {agentConfig?.enabled
                          ? "„Jetzt ausführen“ schickt den Brief an den lokalen Desktop-Agenten (Kanal B) — er steuert deinen Rechner, beobachte den Lauf."
                          : "Kopiere den Brief und füge ihn in UI-TARS-Desktop ein (Kanal A). Für automatische Ausführung agent_bridge in config.yaml aktivieren (bridge/uitars)."}
                      </p>
                      {(dispatchLog[variant.id]?.length ?? 0) > 0 ? (
                        <div className="parallel-variant__handoff-log">
                          {dispatchLog[variant.id].map((ev, i) => (
                            <div key={i} className={`parallel-handoff-event parallel-handoff-event--${ev.status}`}>
                              <span className="parallel-handoff-event__status">{ev.status}</span>
                              {ev.error ? (
                                <span> {ev.error}</span>
                              ) : ev.value != null ? (
                                <span> {typeof ev.value === "string" ? ev.value : JSON.stringify(ev.value)}</span>
                              ) : null}
                            </div>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              </>
            )}

            {expanded ? blocks.map((block) => (
              <div key={block.user.id} className="parallel-result__block">
                <div className="parallel-result__entry parallel-result__entry--user">
                  <span className="parallel-entry__role">Dein Ergebnis</span>
                  <div className="parallel-entry__content">{block.user.content}</div>
                </div>
                {block.feedback ? (
                  <div className="parallel-result__entry parallel-result__entry--ai">
                    <span className="parallel-entry__role">KI-Einordnung</span>
                    {block.feedback.answer_payload ? (
                      <AnswerWithCitations answer={block.feedback.answer_payload} onOpenCitation={onOpenCitation} />
                    ) : (
                      <div className="parallel-entry__content">{block.feedback.content}</div>
                    )}
                  </div>
                ) : null}
                <div className="parallel-result__block-actions">
                  <button type="button" className="button button-compact" onClick={() => takeIntoNote(variant, block)}>
                    {takenId === block.user.id ? <Check size={12} /> : <NotebookPen size={12} />}
                    <span>{takenId === block.user.id ? "Übernommen" : "In Notiz übernehmen"}</span>
                  </button>
                  <button
                    type="button"
                    className="icon-button nav-delete-btn"
                    title="Ergebnis löschen"
                    onClick={() => void removeEntry(block.user, block.feedback)}
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              </div>
            )) : null}

            {expanded ? (
            <div className="parallel-result__submit">
              <textarea
                placeholder="Ergebnis dieser Variante eintragen (was hat dein KI-Tool produziert / wie lief es?)…"
                value={drafts[variant.id] ?? ""}
                onChange={(e) => setDrafts((prev) => ({ ...prev, [variant.id]: e.target.value }))}
                rows={2}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                    e.preventDefault();
                    void submit(variant.id);
                  }
                }}
              />
              <button
                type="button"
                className="button button-compact button-primary"
                onClick={() => void submit(variant.id)}
                disabled={pending === variant.id || !(drafts[variant.id] ?? "").trim()}
              >
                {pending === variant.id ? <Loader2 size={13} className="spin" /> : <Send size={13} />}
                <span>Einsenden</span>
              </button>
            </div>
            ) : null}
          </article>
        );
      })}

      <label className="parallel-panel__feedback-toggle">
        <input type="checkbox" checked={requestFeedback} onChange={(e) => setRequestFeedback(e.target.checked)} />
        KI gibt beim Einsenden sofort eine Einordnung
      </label>
    </section>
  );
}
