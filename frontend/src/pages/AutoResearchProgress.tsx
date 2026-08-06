// AutoResearchProgress — reichhaltige, animierte Fortschrittsanzeige fuer die
// Auto-Recherche. Ersetzt die fruehere einzeilige Status-Zeile durch eine
// aufklappbare Stage-Liste mit Shimmer-Animation auf der aktiven Stage.
//
// Props sind bewusst flach (nur progress + stages + onCancel), damit die Komponente
// rein praesentativ bleibt — aller State lebt in WorkspacePage.
import { useState } from "react";
import type { CSSProperties } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Loader2,
  Square,
} from "lucide-react";
import type { AutoResearchProgress as AutoResearchProgressType, AutoResearchStage } from "./AssistantPage";

export interface AutoResearchProgressProps {
  progress: AutoResearchProgressType;
  /** Eskalationsleiter (scientific → trusted → unverified) zur Anzeige der
   *  Vertrauensstufe pro Stage. Spiegel von query/auto_answer.HARVEST_STAGES. */
  stages: { id: string; label: string; hint: string }[];
  onCancel: () => void;
}

/** Stage-ID, die in `progress.phases` als Key dient — die Stage-IDs aus dem
 *  Live-State koennen entweder direkte Eskalations-IDs ("scientific", "trusted",
 *  "unverified") oder themenspezifische Keys ("scientific:Thema X") sein. Wir
 *  leiten daraus die anzuzeigende Vertrauensstufe ab. */
function trustTierForStage(stage: AutoResearchStage, ladder: { id: string; label: string }[]): string | null {
  if (stage.scope === "planning" || stage.scope === "reanswering") {
    return null;
  }
  const baseId = stage.id.split(":")[0];
  const entry = ladder.find((s) => s.id === baseId);
  return entry ? entry.label : null;
}

export function AutoResearchProgress({ progress, stages, onCancel }: AutoResearchProgressProps) {
  const [expanded, setExpanded] = useState(false);
  const activeIndex = progress.phases.findIndex((s) => s.status === "active");
  const currentStage = activeIndex >= 0 ? progress.phases[activeIndex] : progress.phases[progress.phases.length - 1];
  const totalPhases = progress.phases.length;
  const phaseNumber = Math.min(totalPhases, activeIndex >= 0 ? activeIndex + 1 : totalPhases);
  const hasDetails = progress.phases.some((s) => s.papers.length > 0 || s.grey.length > 0 || s.error);

  return (
    <div className="web-offer-card auto-research-card auto-research-progress">
      {/* Kopf: aktuelle Stage + Loader + Gesamtfortschritt + Abbrechen */}
      <div className="auto-research-header">
        <div className="auto-research-header-main">
          <Loader2 size={15} className="spin" />
          <div className="auto-research-header-text">
            <strong>Auto-Recherche läuft …</strong>
            <span className="auto-research-current-phase">{progress.currentPhase}</span>
            <span className="muted auto-research-phase-counter">Stage {phaseNumber}/{totalPhases}</span>
          </div>
        </div>
        <div className="auto-research-header-actions">
          {hasDetails ? (
            <button
              className="icon-button auto-research-expand"
              type="button"
              aria-label={expanded ? "Details ausblenden" : "Details einblenden"}
              title={expanded ? "Details ausblenden" : "Details einblenden"}
              onClick={() => setExpanded((v) => !v)}
            >
              {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            </button>
          ) : null}
          <button
            className="icon-button"
            type="button"
            aria-label="Auto-Recherche abbrechen"
            title="Auto-Recherche abbrechen"
            onClick={onCancel}
          >
            <Square size={14} />
          </button>
        </div>
      </div>

      {/* Aufklappbare Stage-Liste */}
      {expanded ? (
        <ol className="auto-research-stages">
          {progress.phases.map((stage, index) => {
            const tier = trustTierForStage(stage, stages);
            const isLast = index === progress.phases.length - 1;
            const stageExpanded = stage.status === "error" || stage.papers.length > 0 || stage.grey.length > 0;
            const showStageDetails = stageExpanded;
            return (
              <li
                key={stage.id}
                className="auto-research-stage-item"
                data-status={stage.status}
                style={stage.status === "active" ? shimmerStyle : undefined}
              >
                <div className="auto-research-stage-row">
                  <span className="auto-research-stage-icon">
                    {stage.status === "active" ? (
                      <Loader2 size={14} className="spin" />
                    ) : stage.status === "done" ? (
                      <CheckCircle2 size={14} />
                    ) : (
                      <AlertTriangle size={14} />
                    )}
                  </span>
                  <span className="auto-research-stage-label">{stage.label}</span>
                  {tier ? <span className="auto-research-stage-tier muted">{tier}</span> : null}
                </div>
                {stage.error ? (
                  <div className="auto-research-stage-error">{stage.error}</div>
                ) : null}
                {showStageDetails ? (
                  <ul className="auto-research-stage-sources">
                    {stage.papers.map((paper) => (
                      <li key={paper.id} className="auto-research-source-item auto-research-source-paper">
                        <span className="auto-research-source-title" title={paper.title}>{paper.title}</span>
                      </li>
                    ))}
                    {stage.grey.map((grey) => (
                      <li key={grey.id} className="auto-research-source-item auto-research-source-grey">
                        <span className="auto-research-source-title" title={grey.title}>{grey.title}</span>
                        {grey.trust_tier ? <span className="muted auto-research-source-tier">{grey.trust_tier}</span> : null}
                      </li>
                    ))}
                  </ul>
                ) : null}
                {!isLast ? <span className="auto-research-stage-connector" /> : null}
              </li>
            );
          })}
        </ol>
      ) : null}
    </div>
  );
}

// Shimmer-Animation als Inline-Style, damit kein globales CSS noetig ist.
// Der Gradient wandert langsam ueber die aktive Stage-Zeile und signalisiert
// "arbeitet noch" — aehnlich dem 'thinking'-Glow anderer KI-Assistenten.
const shimmerStyle: CSSProperties = {
  position: "relative",
  overflow: "hidden",
};

// Keyframes werden als globales <style>-Block einmalig injiziert, da React
// Inline-Styles keine @keyframes unterstuetzen.
let shimmerStyleInjected = false;
function injectShimmerStyle() {
  if (shimmerStyleInjected || typeof document === "undefined") {
    return;
  }
  shimmerStyleInjected = true;
  const style = document.createElement("style");
  style.textContent = `
@keyframes autoResearchShimmer {
  0% { transform: translateX(-100%); }
  100% { transform: translateX(100%); }
}
.auto-research-stage-item[data-status="active"]::after {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: linear-gradient(
    90deg,
    transparent 0%,
    rgba(99, 102, 241, 0.08) 40%,
    rgba(99, 102, 241, 0.18) 50%,
    rgba(99, 102, 241, 0.08) 60%,
    transparent 100%
  );
  animation: autoResearchShimmer 1.8s ease-in-out infinite;
}
.auto-research-stage-item[data-status="error"] {
  background: rgba(220, 38, 38, 0.08);
  border-left: 2px solid rgb(220, 38, 38);
}
.auto-research-progress {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.auto-research-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}
.auto-research-header-main {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  min-width: 0;
}
.auto-research-header-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.auto-research-header-text strong {
  display: block;
}
.auto-research-current-phase {
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.auto-research-phase-counter {
  font-size: 11px;
}
.auto-research-header-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}
.auto-research-stages {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}
.auto-research-stage-item {
  position: relative;
  padding: 6px 8px;
  border-radius: 4px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.auto-research-stage-row {
  display: flex;
  align-items: center;
  gap: 6px;
}
.auto-research-stage-icon {
  display: inline-flex;
  flex-shrink: 0;
}
.auto-research-stage-label {
  font-size: 12px;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.auto-research-stage-tier {
  font-size: 11px;
  flex-shrink: 0;
}
.auto-research-stage-error {
  font-size: 11px;
  color: rgb(220, 38, 38);
  padding-left: 20px;
}
.auto-research-stage-sources {
  list-style: none;
  margin: 0;
  padding: 0 0 0 20px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.auto-research-source-item {
  font-size: 11px;
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.auto-research-source-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}
.auto-research-source-tier {
  flex-shrink: 0;
}
.auto-research-stage-connector {
  position: absolute;
  left: 14px;
  bottom: -4px;
  width: 1px;
  height: 4px;
  background: rgba(128, 128, 128, 0.3);
}
`;
  document.head.appendChild(style);
}

// Modul-Init: Style einmalig injizieren.
injectShimmerStyle();