import { useEffect, useRef, useState } from "react";
import { FileText, Link as LinkIcon, Type } from "lucide-react";

import { api } from "../api";
import type { Task } from "../types";

/**
 * Dialog zur Aufnahme eines Task-Inputs (Task-Focused Mode).
 *
 * Drei Quellen (Plan §Session 4):
 *  - URL  → POST /tasks/extract (Backend lädt/seiten-Parst die Seite → TaskSpec)
 *  - PDF  → Datei-Upload, sendet an /tasks/extract als base64-Payload
 *  - Text → Freitext-Aufgabenstellung, geht direkt an /tasks/extract
 *
 * Optional kann der Nutzer "speichern" statt nur extrahieren — dann landet
 * die Task über POST /projects/{id}/tasks/ingest direkt in der tasks-Tabelle
 * des aktiven Projekts und wird im TaskFocusedPane angezeigt.
 *
 * Der Dialog ist rein praesentativ; die eigentliche Mutation passiert im
 * Aufrufer (TaskFocusedPane) via `onTaskCreated`.
 */
type Source = "url" | "pdf" | "text";

type Props = {
  projectId: string;
  open: boolean;
  onClose: () => void;
  /** Wird gerufen, wenn eine Task erfolgreich extrahiert+gespeichert wurde. */
  onTaskCreated: (task: Task) => void;
  /** Aktuell in der Topbar gewählter LLM-Provider/-Modell — wird an das Backend
   *  weitergereicht, damit die Extraktion denselben LLM nutzt wie der Assistant. */
  provider?: string | null;
  model?: string | null;
};

export function TaskIngestDialog({ projectId, open, onClose, onTaskCreated, provider = null, model = null }: Props) {
  const [source, setSource] = useState<Source>("text");
  const [url, setUrl] = useState("");
  const [text, setText] = useState("");
  const [pdfFile, setPdfFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saveMode, setSaveMode] = useState<"extract" | "ingest">("ingest");

  const cardRef = useRef<HTMLDivElement>(null);

  // Reset beim Öffnen
  useEffect(() => {
    if (open) {
      setSource("text");
      setUrl("");
      setText("");
      setPdfFile(null);
      setError(null);
      setSaveMode("ingest");
      setBusy(false);
    }
  }, [open]);

  // Esc schließt (außer während Busy)
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, busy, onClose]);

  // Fokus ins erste Feld beim Öffnen
  useEffect(() => {
    if (open) {
      const t = setTimeout(() => cardRef.current?.focus(), 30);
      return () => clearTimeout(t);
    }
  }, [open]);

  if (!open) return null;

  const canSubmit =
    !busy &&
    ((source === "url" && url.trim().length > 0) ||
      (source === "text" && text.trim().length > 0) ||
      (source === "pdf" && pdfFile !== null));

  async function handleSubmit() {
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      // PDF-Quelle: zuerst als Paper hochladen, dann den gespeicherten Pfad an
      // extract/ingest weiterreichen. (Backend erwartet source_pdf_path als
      // Pfad im PDF-Base-Dir, nicht als roher Upload-Body.)
      let pdfPath: string | null = null;
      if (source === "pdf" && pdfFile) {
        const up = await api.uploadPdf(pdfFile, { title: pdfFile.name, project_id: projectId });
        pdfPath = up.pdf_path;
      }

      const commonPayload = {
        source_kind: source,
        source_url: source === "url" ? url.trim() : null,
        source_text: source === "text" ? text.trim() : null,
        source_pdf_path: pdfPath,
        provider,
        model,
      };

      let task: Task;
      if (saveMode === "ingest") {
        task = await api.tasks.ingest(projectId, commonPayload);
      } else {
        const spec = await api.tasks.extract(commonPayload);
        // Bei reiner Extraktion erzeugen wir ein "Pseudo-Task" ohne Persistenz
        task = {
          id: `preview-${Date.now()}`,
          project_id: projectId,
          title: spec.title || "Extrahierte Task",
          task_json: spec,
          source_kind: source,
          source_url: source === "url" ? url.trim() : null,
          source_pdf_path: pdfPath,
          created_timestamp: new Date().toISOString(),
          updated_timestamp: new Date().toISOString(),
        };
      }
      onTaskCreated(task);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="harvest-dialog-overlay" onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}>
      <div className="harvest-dialog-card task-ingest-dialog-card" ref={cardRef} tabIndex={-1}>
        <strong>Aufgabe aufnehmen</strong>
        <p className="muted">
          Lade eine Aufgabenstellung als URL, PDF oder Freitext. Der Assistant extrahiert daraus eine strukturierte
          TaskSpec (Ziel, Evaluation, Constraints, …).
        </p>

        <div className="segmented task-ingest-source-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={source === "text"}
            className={source === "text" ? "active" : ""}
            onClick={() => setSource("text")}
          >
            <Type size={14} />
            <span>Freitext</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={source === "url"}
            className={source === "url" ? "active" : ""}
            onClick={() => setSource("url")}
          >
            <LinkIcon size={14} />
            <span>URL</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={source === "pdf"}
            className={source === "pdf" ? "active" : ""}
            onClick={() => setSource("pdf")}
          >
            <FileText size={14} />
            <span>PDF</span>
          </button>
        </div>

        {source === "text" ? (
          <div className="task-ingest-field">
            <label htmlFor="task-ingest-text">Aufgabenstellung</label>
            <textarea
              id="task-ingest-text"
              rows={6}
              placeholder="z. B. „Baue einen Klassifikator für arrhythmiefreie EKG-Abschnitte aus dem MIT-BIH-Subset. Ziel: F1 ≥ 0.85 auf einer gehaltenen Patient-Disjunkt-Testmenge. Constraint: Modell muss auf CPU unter 200 ms pro 10-s-Abschnitt inferieren.“"
              value={text}
              onChange={(e) => setText(e.target.value)}
              disabled={busy}
            />
          </div>
        ) : null}

        {source === "url" ? (
          <div className="task-ingest-field">
            <label htmlFor="task-ingest-url">URL der Aufgabenstellung</label>
            <input
              id="task-ingest-url"
              type="url"
              placeholder="https://example.org/wettbewerb/2025-aufgabe.html"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              disabled={busy}
            />
            <span className="muted task-ingest-hint">
              Der Backend lädt die Seite herunter und parst den sichtbaren Text. PDFs hinter einer URL bitte direkt als PDF hochladen.
            </span>
          </div>
        ) : null}

        {source === "pdf" ? (
          <div className="task-ingest-field">
            <label htmlFor="task-ingest-pdf">PDF-Datei</label>
            <input
              id="task-ingest-pdf"
              type="file"
              accept="application/pdf"
              onChange={(e) => setPdfFile(e.target.files?.[0] ?? null)}
              disabled={busy}
            />
            {pdfFile ? <span className="muted">{pdfFile.name} ({Math.round(pdfFile.size / 1024)} KB)</span> : null}
          </div>
        ) : null}

        <div className="task-ingest-save-mode">
          <label>
            <input
              type="radio"
              name="task-ingest-save"
              value="ingest"
              checked={saveMode === "ingest"}
              onChange={() => setSaveMode("ingest")}
              disabled={busy}
            />
            <span>Speichern + Extrahieren</span>
            <span className="muted"> — Task landet im aktiven Projekt</span>
          </label>
          <label>
            <input
              type="radio"
              name="task-ingest-save"
              value="extract"
              checked={saveMode === "extract"}
              onChange={() => setSaveMode("extract")}
              disabled={busy}
            />
            <span>Nur extrahieren</span>
            <span className="muted"> — Vorschau ohne Speichern</span>
          </label>
        </div>

        {error ? <div className="warning-row">{error}</div> : null}

        <div className="harvest-dialog-actions">
          <button type="button" className="button button-primary" onClick={handleSubmit} disabled={!canSubmit}>
            {busy ? "Extrahiere…" : saveMode === "ingest" ? "Aufnehmen" : "Extrahieren"}
          </button>
          <button type="button" className="button" onClick={onClose} disabled={busy}>
            Abbrechen
          </button>
        </div>
      </div>
    </div>
  );
}