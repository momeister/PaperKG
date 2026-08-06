import { useEffect, useState } from "react";
import { ArrowRight, GraduationCap, X } from "lucide-react";

import type { CreativityLevel, TaskResearchDirection } from "../types";
import { CreativitySlider } from "../components/CreativitySlider";

/**
 * Dialog: eine Forschungsrichtung in den Parallel-Modus überführen.
 *
 * Die Frage für die Parallel-Session ist vorausgefüllt mit
 * ``${direction.label} — ${direction.rationale}`` und bleibt editierbar.
 * Der Nutzer kann so die Ausrichtung verfeinern, bevor die Session startet.
 */
type Props = {
  direction: TaskResearchDirection;
  taskTitle: string;
  creativityLevel: CreativityLevel;
  open: boolean;
  onClose: () => void;
  onConfirm: (question: string, creativityLevel: CreativityLevel) => void;
};

export function ParallelTransferDialog({
  direction,
  taskTitle,
  creativityLevel,
  open,
  onClose,
  onConfirm,
}: Props) {
  const [question, setQuestion] = useState("");
  const [creativity, setCreativity] = useState<CreativityLevel>(creativityLevel);

  useEffect(() => {
    if (open) {
      setQuestion(`${direction.label} — ${direction.rationale}`.trim());
      setCreativity(creativityLevel);
    }
  }, [open, direction, creativityLevel]);

  if (!open) return null;

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label="In Parallel-Modus überführen">
      <div className="modal-card parallel-transfer-dialog">
        <header className="modal-card-head">
          <div className="modal-card-title">
            <GraduationCap size={16} />
            <strong>In Parallel-Modus überführen</strong>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Schließen">
            <X size={16} />
          </button>
        </header>

        <div className="modal-card-body">
          <div className="parallel-transfer-direction">
            <span className="muted">Forschungsrichtung</span>
            <strong>{direction.label}</strong>
            {direction.rationale ? <p className="muted">{direction.rationale}</p> : null}
            {direction.keywords?.length ? (
              <div className="task-research-direction-keywords">
                {direction.keywords.map((kw, i) => (
                  <span key={i} className="task-research-direction-keyword">{kw}</span>
                ))}
              </div>
            ) : null}
          </div>

          <p className="muted">
            Die über die Tiefensuche ins Projekt eingepflegten Papers und Web-Quellen stehen der
            Parallel-Session automatisch als Beleg-Pool zur Verfügung. Die Frage steuert, wie der
            Assistant die Variante ausrichtet.
          </p>

          <label className="parallel-transfer-question-label">
            <strong>Frage für die Parallel-Session</strong>
            <textarea
              className="parallel-transfer-question-input"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              rows={3}
              maxLength={2000}
              placeholder={`${direction.label} — wie lässt sich das Problem umsetzen?`}
            />
            <span className="muted parallel-transfer-task-context">
              Task: {taskTitle}
            </span>
          </label>

          <div className="parallel-transfer-creativity">
            <CreativitySlider
              value={creativity}
              onChange={setCreativity}
              id="parallel-transfer-creativity"
              label="Kreativität für diese Parallel-Session"
              hint="1 = konservativ, 5 = cross-domain. Steuert, wie variantenreich die Vorschläge ausfallen."
              tooltip="Projektweite Kreativität — gilt für den Implementationsplan und diese Session."
            />
          </div>
        </div>

        <footer className="modal-card-foot">
          <button type="button" className="button button-ghost" onClick={onClose}>
            Abbrechen
          </button>
          <button
            type="button"
            className="button button-primary"
            onClick={() => question.trim() && onConfirm(question.trim(), creativity)}
            disabled={!question.trim()}
          >
            <ArrowRight size={14} />
            <span>Parallel starten</span>
          </button>
        </footer>
      </div>
    </div>
  );
}