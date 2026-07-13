// Status/steering panel for the native Selbst-Steuerung loop: armed banner,
// autopilot vs per-action confirmation, pause/resume, step ticker and the
// confirm/skip buttons in confirmation mode. The goal/ask input lives in the
// shell's shared chat input — this panel only steers a running loop.

import { ArrowRight, Loader2, Pause, Play, Square } from "lucide-react";

import { describeSelfDriveAction } from "./companionSession";
import type { useSelfDriveLoop } from "./useSelfDriveLoop";

type SelfDrivePanelProps = {
  loop: ReturnType<typeof useSelfDriveLoop>;
  autopilot: boolean;
  onAutopilotChange: (value: boolean) => void;
};

export function SelfDrivePanel({ loop, autopilot, onAutopilotChange }: SelfDrivePanelProps) {
  if (!loop.sessionId) {
    return (
      <p className="overlay-muted">
        Beschreibe unten dein Ziel — die KI steuert Maus und Tastatur selbst.
        Not-Aus jederzeit: Strg+Shift+Q. (companion.self_drive.enabled muss an sein.)
      </p>
    );
  }
  return (
    <div className="overlay-selfdrive-plan">
      <p className="overlay-model-hint overlay-selfdrive-armed">
        ● Aktiv{autopilot ? " — Autopilot" : " — Bestätigungsmodus"}. Not-Aus: Strg+Shift+Q
      </p>
      <div className="overlay-companion-actions">
        <button
          type="button"
          className={`button button-compact overlay-source-toggle ${autopilot ? "overlay-source-toggle--active" : ""}`}
          aria-pressed={autopilot}
          title="Aktionen ohne Einzel-Bestätigung ausführen"
          onClick={() => onAutopilotChange(!autopilot)}
        >
          Autopilot
        </button>
        {loop.paused ? (
          <button className="button button-compact" type="button" onClick={loop.resume}>
            <Play size={13} /> Weiter
          </button>
        ) : (
          <button className="button button-compact" type="button" onClick={loop.pause}>
            <Pause size={13} /> Pause
          </button>
        )}
        <button className="button button-compact overlay-stop" type="button" onClick={() => void loop.stop()}>
          <Square size={13} /> Stoppen
        </button>
      </div>
      {loop.stepInfo ? (
        <p className="overlay-muted">
          Schritt {loop.stepInfo.step}/{loop.stepInfo.max}
        </p>
      ) : null}
      {loop.thought ? <p className="overlay-selfdrive-thought">{loop.thought}</p> : null}
      {loop.busy ? (
        <p className="overlay-muted">
          <Loader2 size={13} className="overlay-spin" /> Beobachte, prüfe & plane…
        </p>
      ) : loop.pendingAction ? (
        <>
          {loop.pendingAction.sensitive ? (
            <p className="overlay-hint overlay-hint--error overlay-selfdrive-sensitive">
              ⚠ {loop.pendingAction.sensitive_reason ?? "Sensibles Ziel erkannt"} — bitte prüfen und
              nur bei Bedarf ausführen.
            </p>
          ) : null}
          <p className="overlay-selfdrive-action">
            Nächste Aktion: <strong>{describeSelfDriveAction(loop.pendingAction)}</strong>
          </p>
          <div className="overlay-companion-actions">
            <button className="button button-primary button-compact" type="button" onClick={loop.confirm}>
              <Play size={13} /> Ausführen
            </button>
            <button className="button button-compact" type="button" onClick={loop.skip}>
              <ArrowRight size={13} /> Überspringen
            </button>
          </div>
        </>
      ) : loop.askQuestion ? (
        <p className="overlay-selfdrive-action">Rückfrage — antworte unten im Eingabefeld.</p>
      ) : loop.paused ? (
        <p className="overlay-muted">Pausiert.</p>
      ) : null}
    </div>
  );
}
