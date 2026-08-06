import { useState } from "react";
import {
  CheckCircle2,
  GraduationCap,
  Loader2,
  Plus,
  Send,
  X,
  XCircle,
} from "lucide-react";

import { api } from "../api";
import type { Answer, ParallelSession, ParallelStep, ParallelVariant } from "../types";

/**
 * "Wie umsetzen"-Steps für eine Variante (Task-Focused Mode + Parallel-Research).
 *
 * Plan §Session 4: Pro Variante zeigt der Assistant Vorschläge ("vorgeschlagen"),
 * die der Nutzer interaktiv bearbeitet:
 *  - "+Eigener Schritt": Nutzer legt eigenen Step an (origin=user)
 *  - "Das probiere ich": Step auf in_progress
 *  - "Ergebnis zeigen": Step auf done + Ergebnis als parallel_entry posten
 *    (optional Professor-Feedback)
 *  - "Weg nichts": Step auf rejected
 *  - "Frage an Professor": Follow-up scoped to variant+step
 *
 * Implementationsplan = Summe der akzeptierten Steps (done/in_progress).
 */
type Props = {
  variant: ParallelVariant;
  session: ParallelSession;
  onChange: (session: ParallelSession) => void;
  scope: { paperIds?: string[]; provider?: string | null; model?: string | null };
};

export function VariantSteps({ variant, session, onChange, scope }: Props) {
  const steps = variant.user_steps ?? [];
  const [newStepText, setNewStepText] = useState("");
  const [busyStepId, setBusyStepId] = useState<string | null>(null);
  const [resultDraft, setResultDraft] = useState<Record<string, string>>({});
  const [requestFeedback, setRequestFeedback] = useState<Record<string, boolean>>({});
  const [askOpen, setAskOpen] = useState<string | null>(null);
  const [askDraft, setAskDraft] = useState<Record<string, string>>({});
  const [rejectionDraft, setRejectionDraft] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  async function applyVariantUpdate(
    promise: Promise<{ variant: ParallelVariant; answer?: Answer | null }>,
  ) {
    setBusyStepId("…");
    setError(null);
    try {
      const res = await promise;
      // Variante in der Session ersetzen + Parent informieren.
      const updated: ParallelSession = {
        ...session,
        variants: session.variants.map((v) =>
          v.id === res.variant.id ? res.variant : v,
        ),
      };
      onChange(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyStepId(null);
    }
  }

  async function addStep() {
    const text = newStepText.trim();
    if (!text) return;
    setError(null);
    setBusyStepId("add");
    try {
      const res = await api.addParallelStep(variant.id, { text, origin: "user" });
      const updated: ParallelSession = {
        ...session,
        variants: session.variants.map((v) => (v.id === res.variant.id ? res.variant : v)),
      };
      onChange(updated);
      setNewStepText("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyStepId(null);
    }
  }

  async function setStatus(step: ParallelStep, status: "in_progress" | "rejected") {
    await applyVariantUpdate(
      api.updateParallelStep(variant.id, step.id, { status }),
    );
  }

  async function submitResult(step: ParallelStep) {
    const result = (resultDraft[step.id] ?? "").trim();
    if (!result) return;
    setBusyStepId(step.id);
    setError(null);
    try {
      const res = await api.submitParallelStepResult(variant.id, step.id, {
        result,
        request_feedback: requestFeedback[step.id] ?? true,
        paper_ids: scope.paperIds,
        provider: scope.provider ?? null,
        model: scope.model ?? null,
      });
      // Backend already appended the result entry (+ optional professor review)
      // and returns the updated variant. Trust it — replace in session.
      const updated: ParallelSession = {
        ...session,
        variants: session.variants.map((v) => (v.id === res.variant.id ? res.variant : v)),
      };
      onChange(updated);
      setResultDraft((p) => ({ ...p, [step.id]: "" }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyStepId(null);
    }
  }

  async function askProfessor(step: ParallelStep | null) {
    const question = askDraft[step?.id ?? "global"]?.trim();
    if (!question) return;
    setBusyStepId(step?.id ?? "ask");
    setError(null);
    try {
      const res = await api.askParallelProfessor(variant.id, {
        question,
        step_id: step?.id ?? null,
        paper_ids: scope.paperIds,
        provider: scope.provider ?? null,
        model: scope.model ?? null,
      });
      const updated: ParallelSession = {
        ...session,
        variants: session.variants.map((v) => (v.id === res.variant.id ? res.variant : v)),
      };
      onChange(updated);
      setAskDraft((p) => ({ ...p, [step?.id ?? "global"]: "" }));
      setAskOpen(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyStepId(null);
    }
  }

  async function rejectVariant() {
    const reason = (rejectionDraft[variant.id] ?? "").trim() || "Nicht passend";
    setBusyStepId("reject-variant");
    setError(null);
    try {
      const res = await api.rejectParallelVariant(variant.id, { reason });
      const updated: ParallelSession = {
        ...session,
        variants: session.variants.map((v) => (v.id === res.variant.id ? res.variant : v)),
      };
      onChange(updated);
      setRejectionDraft((p) => ({ ...p, [variant.id]: "" }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyStepId(null);
    }
  }

  async function deleteStep(step: ParallelStep) {
    setBusyStepId(step.id);
    setError(null);
    try {
      await api.deleteParallelStep(variant.id, step.id);
      const updated: ParallelSession = {
        ...session,
        variants: session.variants.map((v) => {
          if (v.id !== variant.id) return v;
          return {
            ...v,
            user_steps: (v.user_steps ?? []).filter((s) => s.id !== step.id),
          };
        }),
      };
      onChange(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyStepId(null);
    }
  }

  const acceptedCount = steps.filter((s) => s.status === "done" || s.status === "in_progress").length;

  return (
    <div className="parallel-steps-block">
      <div className="parallel-steps-block__head">
        <strong>Wie umsetzen?</strong>
        <span className="muted">
          {steps.length} Schritt{steps.length === 1 ? "" : "e"} · {acceptedCount} akzeptiert
        </span>
      </div>

      {error ? <div className="warning-row">{error}</div> : null}

      {steps.length === 0 ? (
        <p className="muted parallel-steps-empty">
          Noch keine Umsetzungs-Schritte. Der Assistant kann welche vorschlagen (über „Mehr Varianten"), oder
          du legst eigene an.
        </p>
      ) : (
        <div className="parallel-step-list">
          {steps.map((step) => (
            <div key={step.id} className={`parallel-step parallel-step--${step.status}`}>
              <div className="parallel-step__head">
                <span className="parallel-step__text">
                  {step.origin === "ai" ? <span className="parallel-badge parallel-badge--ai">KI</span> : null}
                  {step.text}
                </span>
                <button
                  type="button"
                  className="icon-button nav-delete-btn"
                  title="Schritt löschen"
                  onClick={() => void deleteStep(step)}
                  disabled={busyStepId !== null}
                >
                  <X size={12} />
                </button>
              </div>
              {step.rationale ? <p className="parallel-step__rationale">{step.rationale}</p> : null}
              {step.citation ? <p className="parallel-step__rationale">Quelle: {step.citation}</p> : null}

              {step.status === "vorgeschlagen" ? (
                <div className="parallel-step__actions">
                  <button
                    type="button"
                    className="button button-compact button-primary"
                    onClick={() => void setStatus(step, "in_progress")}
                    disabled={busyStepId !== null}
                  >
                    <span>Das probiere ich</span>
                  </button>
                  <button
                    type="button"
                    className="button button-compact"
                    onClick={() => void setStatus(step, "rejected")}
                    disabled={busyStepId !== null}
                  >
                    <span>Weg nichts für mich</span>
                  </button>
                  <button
                    type="button"
                    className="button button-compact"
                    onClick={() => setAskOpen(askOpen === step.id ? null : step.id)}
                    disabled={busyStepId !== null}
                  >
                    <GraduationCap size={12} />
                    <span>Frage an Professor</span>
                  </button>
                </div>
              ) : null}

              {step.status === "in_progress" ? (
                <div className="parallel-step__result-form">
                  <textarea
                    placeholder="Was hat dein KI-Tool produziert? (Ergebnis eintragen → Professor begutachtet)"
                    rows={2}
                    value={resultDraft[step.id] ?? ""}
                    onChange={(e) => setResultDraft((p) => ({ ...p, [step.id]: e.target.value }))}
                    disabled={busyStepId !== null}
                  />
                  <label className="parallel-step__feedback-toggle">
                    <input
                      type="checkbox"
                      checked={requestFeedback[step.id] ?? true}
                      onChange={(e) => setRequestFeedback((p) => ({ ...p, [step.id]: e.target.checked }))}
                    />
                    Professor begutachten
                  </label>
                  <div className="parallel-step__actions">
                    <button
                      type="button"
                      className="button button-compact button-primary"
                      onClick={() => void submitResult(step)}
                      disabled={busyStepId !== null || !(resultDraft[step.id] ?? "").trim()}
                    >
                      {busyStepId === step.id ? <Loader2 size={12} className="spin" /> : <CheckCircle2 size={12} />}
                      <span>Ergebnis zeigen</span>
                    </button>
                    <button
                      type="button"
                      className="button button-compact"
                      onClick={() => void setAskOpen(askOpen === step.id ? null : step.id)}
                      disabled={busyStepId !== null}
                    >
                      <GraduationCap size={12} />
                      <span>Frage</span>
                    </button>
                  </div>
                </div>
              ) : null}

              {step.status === "done" && step.result ? (
                <div className="parallel-step__result">
                  <strong>Ergebnis:</strong> {step.result}
                </div>
              ) : null}

              {step.status === "rejected" ? (
                <div className="parallel-step__rejection">Weg nichts für mich.</div>
              ) : null}

              {askOpen === step.id ? (
                <div className="parallel-ask-professor">
                  <textarea
                    placeholder="Frage an den Professor zu diesem Schritt…"
                    rows={2}
                    value={askDraft[step.id] ?? ""}
                    onChange={(e) => setAskDraft((p) => ({ ...p, [step.id]: e.target.value }))}
                    disabled={busyStepId !== null}
                  />
                  <div className="parallel-step__actions">
                    <button
                      type="button"
                      className="button button-compact button-primary"
                      onClick={() => void askProfessor(step)}
                      disabled={busyStepId !== null || !(askDraft[step.id] ?? "").trim()}
                    >
                      <Send size={12} />
                      <span>Senden</span>
                    </button>
                    <button
                      type="button"
                      className="button button-compact"
                      onClick={() => setAskOpen(null)}
                      disabled={busyStepId !== null}
                    >
                      <span>Abbrechen</span>
                    </button>
                  </div>
                </div>
              ) : null}
            </div>
          ))}
        </div>
      )}

      <div className="parallel-step-add">
        <input
          type="text"
          placeholder="+ Eigener Schritt"
          value={newStepText}
          onChange={(e) => setNewStepText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && newStepText.trim()) {
              e.preventDefault();
              void addStep();
            }
          }}
          disabled={busyStepId !== null}
        />
        <button
          type="button"
          className="button button-compact"
          onClick={() => void addStep()}
          disabled={busyStepId !== null || !newStepText.trim()}
        >
          <Plus size={13} />
          <span>Hinzufügen</span>
        </button>
      </div>

      {variant.status !== "verworfen" ? (
        <div className="parallel-step__actions">
          <button
            type="button"
            className="button button-compact"
            onClick={() => void rejectVariant()}
            disabled={busyStepId !== null}
            title="Diese Variante komplett ablehnen"
          >
            <XCircle size={13} />
            <span>Weg nichts für mich (Variante)</span>
          </button>
        </div>
      ) : variant.rejection_reason ? (
        <p className="parallel-step__rejection">Variante abgelehnt: {variant.rejection_reason}</p>
      ) : null}
    </div>
  );
}