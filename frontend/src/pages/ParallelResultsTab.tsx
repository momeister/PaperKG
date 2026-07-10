import { useEffect, useState } from "react";
import {
  Bot,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Copy,
  GraduationCap,
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
import { isTauri, nativeInvoke } from "../native";
import type {
  AgentConfig,
  AgentDispatchEvent,
  AgentHandoffResponse,
  ParallelEntry,
  ParallelSession,
  ParallelStage,
  ParallelVariant,
  VerificationSource,
} from "../types";
import { formatAnswerForNote, verificationSourcesFor } from "./assistantHelpers";
import { CitedInline, useParallelPool } from "./ParallelResearchPanel";
import { groupVariantsByStage, STAGE_STATUS_LABEL, stageGroupLabel } from "./parallelHelpers";
import { ProfessorReviewCard } from "./ProfessorReviewCard";

const STATUS_LABEL: Record<string, string> = {
  vorgeschlagen: "Vorgeschlagen",
  in_arbeit: "In Arbeit",
  ergebnis: "Ergebnis da",
  verworfen: "Verworfen",
};

type ResultBlock = { user: ParallelEntry; feedback: ParallelEntry | null };

/** Pair each submitted result with the professor review that follows it (entries are
 * ordered by creation: a user result, then optionally its assistant feedback). */
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

/** Notes-side "Ergebnisse" tab: the Etappen roadmap with the methods to try (variants +
 * ready-to-copy prompts), per-variant result entry, the structured Professor-Review, and
 * an opt-in "In Notiz übernehmen" (with real citations). */
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
  onTakeIntoNote: (markdown: string, citations?: Record<string, unknown>[]) => void | Promise<unknown>;
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
  // Etappen: collapse state, per-stage busy marker, inline name/goal editing.
  const [openStageMap, setOpenStageMap] = useState<Record<string, boolean>>({});
  const [stageBusy, setStageBusy] = useState<string | null>(null);
  const [editingStage, setEditingStage] = useState<string | null>(null);
  const [stageDraft, setStageDraft] = useState<{ name?: string; goal?: string }>({});
  const [stageError, setStageError] = useState<string | null>(null);
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

  function toggleStageOpen(key: string) {
    setOpenStageMap((prev) => ({ ...prev, [key]: prev[key] === false ? true : false }));
  }

  const scopeBody = { paper_ids: scope.paperIds, provider: scope.provider, model: scope.model };
  const groups = groupVariantsByStage(session);

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

  async function addVariant(stageId?: string | null) {
    setBusy("add");
    try {
      await api.addParallelVariant(session.id, {
        name: "Neue Variante",
        stage_id: stageId ?? undefined,
      });
      await refresh();
    } finally {
      setBusy(null);
    }
  }

  async function generateMore(stageId?: string | null) {
    setBusy("generate");
    try {
      const res = await api.generateParallelVariants(session.id, {
        variant_count: 2,
        stage_id: stageId ?? undefined,
        ...scopeBody,
      });
      onChange(res.session);
    } finally {
      setBusy(null);
    }
  }

  // --- Etappen actions --------------------------------------------------- //

  async function addStage() {
    setStageBusy("add");
    setStageError(null);
    try {
      const res = await api.addParallelStage(session.id, { name: "Neue Etappe" });
      onChange(res.session);
      const created = (res.session.stages ?? [])[Math.max(0, (res.session.stages ?? []).length - 1)];
      if (created) {
        setEditingStage(created.id);
        setStageDraft({ name: created.name, goal: created.goal });
      }
    } catch (err) {
      setStageError(err instanceof Error ? err.message : "Etappe konnte nicht angelegt werden.");
    } finally {
      setStageBusy(null);
    }
  }

  async function proposeStages() {
    setStageBusy("propose");
    setStageError(null);
    try {
      const res = await api.addParallelStage(session.id, { propose: true, ...scopeBody });
      onChange(res.session);
    } catch (err) {
      setStageError(err instanceof Error ? err.message : "Keine Etappen-Vorschläge erhalten.");
    } finally {
      setStageBusy(null);
    }
  }

  async function reviewStage(stage: ParallelStage) {
    setStageBusy(stage.id);
    setStageError(null);
    try {
      const res = await api.reviewParallelStage(stage.id, scopeBody);
      onChange(res.session);
    } catch (err) {
      setStageError(err instanceof Error ? err.message : "Etappen-Review fehlgeschlagen.");
    } finally {
      setStageBusy(null);
    }
  }

  /** Complete an Etappe — the backend auto-activates the next open one. */
  async function completeStage(stage: ParallelStage) {
    setStageBusy(stage.id);
    try {
      const res = await api.updateParallelStage(stage.id, { status: "abgeschlossen" });
      onChange(res.session);
    } finally {
      setStageBusy(null);
    }
  }

  async function saveStageEdit(stage: ParallelStage) {
    const res = await api.updateParallelStage(stage.id, {
      name: (stageDraft.name ?? stage.name).trim() || stage.name,
      goal: stageDraft.goal ?? stage.goal,
    });
    onChange(res.session);
    setEditingStage(null);
    setStageDraft({});
  }

  async function removeStage(stage: ParallelStage) {
    setStageError(null);
    try {
      await api.deleteParallelStage(stage.id);
      await refresh();
    } catch (err) {
      setStageError(err instanceof Error ? err.message : "Etappe konnte nicht gelöscht werden.");
    }
  }

  // --- Variant actions ---------------------------------------------------- //

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

  /** Compile (or reuse the cached) task brief for a variant. */
  async function ensureHandoff(variant: ParallelVariant): Promise<AgentHandoffResponse> {
    const cached = handoff[variant.id];
    if (cached) return cached;
    const res = await api.parallelVariantHandoff(variant.id, scopeBody);
    setHandoff((p) => ({ ...p, [variant.id]: res }));
    return res;
  }

  /** Web fallback: toggle the inline hand-off panel (Kanal A/B), compiling the brief
   * on first open. Native builds skip this — see `sendToOverlay` below. */
  async function openHandoff(variant: ParallelVariant) {
    if (handoffOpen[variant.id]) {
      setHandoffOpen((p) => ({ ...p, [variant.id]: false }));
      return;
    }
    setHandoffOpen((p) => ({ ...p, [variant.id]: true }));
    if (handoff[variant.id] || handoffBusy) return;
    setHandoffBusy(variant.id);
    try {
      await ensureHandoff(variant);
    } finally {
      setHandoffBusy(null);
    }
  }

  /** Native: spawn the AI-Cursor overlay pre-loaded with this variant's brief — the
   * overlay itself requires an explicit "Starten" before anything runs. */
  async function sendToOverlay(variant: ParallelVariant) {
    if (handoffBusy) return;
    setHandoffBusy(variant.id);
    try {
      const res = await ensureHandoff(variant);
      await nativeInvoke("overlay_dispatch_task", {
        task: res.text,
        goal: res.brief.goal,
        mode: "self_managing",
        variantId: variant.id,
      });
    } finally {
      setHandoffBusy(null);
    }
  }

  async function copyBrief(variant: ParallelVariant) {
    const text = (await ensureHandoff(variant)).text;
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

  /** Into the note WITH real citations: the professor review is formatted via
   * formatAnswerForNote so its `[arxiv:...]` tokens become clickable citation chips
   * backed by note_citations rows (previously the citations were dropped). */
  async function takeIntoNote(variant: ParallelVariant, block: ResultBlock) {
    const quoted = block.user.content.split("\n").map((line) => `> ${line}`).join("\n");
    let markdown = `> **${variant.name} — Ergebnis**\n>\n${quoted}\n`;
    let citations: Record<string, unknown>[] = [];
    const payload = block.feedback?.answer_payload;
    if (payload?.answer?.trim()) {
      const label = payload.professor_review ? "Professor-Review" : "KI-Einordnung";
      try {
        const verification = await verificationSourcesFor(payload);
        const formatted = formatAnswerForNote(payload, verification);
        markdown += `\n**${label}:**\n\n${formatted.markdown.trim()}\n`;
        citations = formatted.citations;
      } catch {
        markdown += `\n**${label}:**\n\n${payload.answer.trim()}\n`;
      }
    } else if (block.feedback?.content?.trim()) {
      markdown += `\n**KI-Einordnung:** ${block.feedback.content.trim()}\n`;
    }
    await Promise.resolve(onTakeIntoNote(markdown, citations));
    setTakenId(block.user.id);
    window.setTimeout(() => setTakenId((id) => (id === block.user.id ? null : id)), 1500);
  }

  function renderVariant(variant: ParallelVariant, index: number) {
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
              {isTauri() ? (
                <div className="parallel-variant__handoff-toolbar">
                  <button
                    type="button"
                    className="button button-compact button-primary"
                    onClick={() => void sendToOverlay(variant)}
                    disabled={handoffBusy === variant.id}
                    title="Diese Variante als Aufgaben-Brief an den AI-Cursor übergeben (Selbst-Steuerung oder Assistent — nichts läuft, bis du im Overlay „Starten“ klickst)"
                  >
                    {handoffBusy === variant.id ? <Loader2 size={13} className="spin" /> : <Bot size={13} />}
                    <span>An AI-Cursor übergeben</span>
                  </button>
                  <button
                    type="button"
                    className="button button-compact"
                    onClick={() => void copyBrief(variant)}
                    disabled={handoffBusy === variant.id}
                  >
                    {briefCopiedId === variant.id ? <Check size={13} /> : <Copy size={13} />}
                    <span>{briefCopiedId === variant.id ? "Kopiert" : "Brief kopieren"}</span>
                  </button>
                </div>
              ) : (
                <>
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
                        <button type="button" className="button button-compact" onClick={() => void copyBrief(variant)}>
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
                </>
              )}
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
                <span className="parallel-entry__role">
                  {block.feedback.answer_payload?.professor_review ? (
                    <>
                      <GraduationCap size={12} /> Professor-Review
                    </>
                  ) : (
                    "KI-Einordnung"
                  )}
                </span>
                {block.feedback.answer_payload ? (
                  <ProfessorReviewCard answer={block.feedback.answer_payload} onOpenCitation={onOpenCitation} />
                ) : (
                  <div className="parallel-entry__content">{block.feedback.content}</div>
                )}
              </div>
            ) : null}
            <div className="parallel-result__block-actions">
              <button type="button" className="button button-compact" onClick={() => void takeIntoNote(variant, block)}>
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
  }

  return (
    <section className="panel parallel-results-tab">
      <div className="parallel-results-tab__intro">
        Dein Vorhaben ist in <strong>Etappen</strong> gegliedert; pro Etappe gibt es
        {" "}<strong>Methoden zum Ausprobieren</strong>. Trag pro Variante ein, was dein KI-Tool
        produziert hat — der <strong>Professor</strong> begutachtet es strukturiert, und mit
        {" "}<strong>„In Notiz übernehmen"</strong> wandert es samt Zitaten in deine Notiz.
      </div>

      <div className="parallel-results-tab__actions">
        <button type="button" className="button button-compact" onClick={() => void generateMore()} disabled={busy !== null}>
          {busy === "generate" ? <Loader2 size={13} className="spin" /> : <Sparkles size={13} />}
          <span>Mehr Varianten</span>
        </button>
        <button type="button" className="button button-compact" onClick={() => void addVariant()} disabled={busy !== null}>
          <Plus size={13} />
          <span>Eigene Variante</span>
        </button>
      </div>

      {stageError ? <div className="muted-row parallel-stage-error">{stageError}</div> : null}

      {groups.length === 0 ? (
        <div className="muted-row">Noch keine Varianten — „Mehr Varianten" generieren oder „Eigene Variante" hinzufügen.</div>
      ) : null}

      {groups.map((group, groupIndex) => {
        const stage = group.stage;
        const stageKey = stage?.id ?? `orphans-${groupIndex}`;
        const stageOpen = openStageMap[stageKey] !== false;
        const isStageEditing = stage !== null && editingStage === stage.id;
        const reviewAnswer = stage?.review_payload ?? null;
        return (
          <section key={stageKey} className={`parallel-stage parallel-stage--${stage?.status ?? "aktiv"}`}>
            <div className="parallel-stage__head">
              <button
                type="button"
                className="icon-button parallel-result__toggle"
                title={stageOpen ? "Einklappen" : "Ausklappen"}
                aria-expanded={stageOpen}
                onClick={() => toggleStageOpen(stageKey)}
              >
                {stageOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              </button>
              <span className="parallel-stage__index">Etappe {groupIndex + 1}</span>
              {isStageEditing && stage ? (
                <input
                  className="parallel-variant__name-input"
                  value={stageDraft.name ?? stage.name}
                  onChange={(e) => setStageDraft((d) => ({ ...d, name: e.target.value }))}
                />
              ) : (
                <strong className="parallel-stage__name">{stageGroupLabel(group, groupIndex)}</strong>
              )}
              {stage ? (
                <span className={`parallel-badge parallel-stage-status--${stage.status}`}>
                  {STAGE_STATUS_LABEL[stage.status] ?? stage.status}
                </span>
              ) : null}
              <span className="parallel-variant__spacer" />
              {stage ? (
                <>
                  {isStageEditing ? (
                    <button type="button" className="icon-button" title="Speichern" onClick={() => void saveStageEdit(stage)}>
                      <Check size={14} />
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="icon-button"
                      title="Etappe bearbeiten"
                      onClick={() => {
                        setEditingStage(stage.id);
                        setStageDraft({ name: stage.name, goal: stage.goal });
                      }}
                    >
                      <Pencil size={13} />
                    </button>
                  )}
                  <button
                    type="button"
                    className="icon-button"
                    title="Professor-Etappen-Review: alle Varianten + Ergebnisse dieser Etappe begutachten"
                    disabled={stageBusy === stage.id}
                    onClick={() => void reviewStage(stage)}
                  >
                    {stageBusy === stage.id ? <Loader2 size={13} className="spin" /> : <GraduationCap size={13} />}
                  </button>
                  {stage.status !== "abgeschlossen" ? (
                    <button
                      type="button"
                      className="icon-button"
                      title="Etappe abschließen (nächste offene wird aktiv)"
                      disabled={stageBusy === stage.id}
                      onClick={() => void completeStage(stage)}
                    >
                      <CheckCircle2 size={13} />
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="icon-button"
                    title="Variante in dieser Etappe hinzufügen"
                    disabled={busy !== null}
                    onClick={() => void addVariant(stage.id)}
                  >
                    <Plus size={13} />
                  </button>
                  <button
                    type="button"
                    className="icon-button nav-delete-btn"
                    title="Etappe löschen (Varianten wandern in die erste verbleibende Etappe)"
                    onClick={() => void removeStage(stage)}
                  >
                    <Trash2 size={13} />
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  className="icon-button"
                  title="Variante hinzufügen"
                  disabled={busy !== null}
                  onClick={() => void addVariant()}
                >
                  <Plus size={13} />
                </button>
              )}
            </div>

            {stageOpen ? (
              <>
                {isStageEditing && stage ? (
                  <label className="parallel-stage__goal-edit">
                    Ziel
                    <textarea
                      value={stageDraft.goal ?? stage.goal}
                      onChange={(e) => setStageDraft((d) => ({ ...d, goal: e.target.value }))}
                      rows={2}
                    />
                  </label>
                ) : stage?.goal?.trim() ? (
                  <p className="parallel-stage__goal">
                    <span className="parallel-variant__text-label">Ziel:</span> {stage.goal}
                  </p>
                ) : null}

                {reviewAnswer ? (
                  <div className="parallel-stage__review">
                    <span className="parallel-entry__role">
                      <GraduationCap size={12} /> Etappen-Review
                    </span>
                    <ProfessorReviewCard answer={reviewAnswer} onOpenCitation={onOpenCitation} />
                  </div>
                ) : null}

                {group.variants.length === 0 ? (
                  <div className="muted-row">Noch keine Varianten in dieser Etappe.</div>
                ) : (
                  group.variants.map((variant, index) => renderVariant(variant, index))
                )}
              </>
            ) : null}
          </section>
        );
      })}

      <div className="parallel-results-tab__actions parallel-results-tab__stage-actions">
        <button type="button" className="button button-compact" onClick={() => void addStage()} disabled={stageBusy !== null}>
          {stageBusy === "add" ? <Loader2 size={13} className="spin" /> : <Plus size={13} />}
          <span>Etappe hinzufügen</span>
        </button>
        <button type="button" className="button button-compact" onClick={() => void proposeStages()} disabled={stageBusy !== null}>
          {stageBusy === "propose" ? <Loader2 size={13} className="spin" /> : <Sparkles size={13} />}
          <span>Etappen vorschlagen</span>
        </button>
      </div>

      <label className="parallel-panel__feedback-toggle">
        <input type="checkbox" checked={requestFeedback} onChange={(e) => setRequestFeedback(e.target.checked)} />
        Professor begutachtet beim Einsenden sofort
      </label>
    </section>
  );
}
