import { useCallback, useEffect, useState } from "react";
import { Notebook, NotebookPen, Play, RefreshCw, Square } from "lucide-react";

import { EmptyState } from "../components/EmptyState";
import { NotesSidePanel } from "../components/NotesSidePanel";
import { isTauri, nativeInvoke } from "../native";

// Optional JupyterLab sidecar (roadmap R3). The Rust shell starts `jupyter lab`
// on a free localhost port and hands back a token URL we embed in an iframe.
// Native-only: a plain browser has no managed sidecar, so we show a hint. Jupyter
// is optional and not bundled into the installer — if it is missing the start
// fails and we surface a `pip install jupyterlab` hint.

type JupyterStatus = "idle" | "starting" | "running" | "error";

export function JupyterPage() {
  const [status, setStatus] = useState<JupyterStatus>("idle");
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notesOpen, setNotesOpen] = useState(() => localStorage.getItem("sciencekg.jupyter.notes") === "1");

  useEffect(() => {
    localStorage.setItem("sciencekg.jupyter.notes", notesOpen ? "1" : "0");
  }, [notesOpen]);

  const start = useCallback(async () => {
    setStatus("starting");
    setError(null);
    try {
      const next = await nativeInvoke<string>("jupyter_start");
      setUrl(next);
      setStatus("running");
    } catch (err) {
      setError(String(err));
      setStatus("error");
    }
  }, []);

  const stop = useCallback(async () => {
    try {
      await nativeInvoke("jupyter_stop");
    } catch {
      // Best effort — the server is killed on app exit regardless.
    }
    setUrl(null);
    setStatus("idle");
  }, []);

  const restart = useCallback(async () => {
    try {
      await nativeInvoke("jupyter_stop");
    } catch {
      // ignore — start() spawns a fresh server anyway
    }
    setUrl(null);
    await start();
  }, [start]);

  // Auto-start once on mount in the native shell. The server keeps running when
  // the user switches tabs (no stop on unmount), so kernels survive navigation;
  // the Rust exit hook reaps it on app close.
  useEffect(() => {
    if (isTauri()) {
      void start();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!isTauri()) {
    return (
      <section className="page jupyter-page">
        <div className="page-title">
          <div>
            <span>Notebooks</span>
            <h1>Jupyter</h1>
          </div>
        </div>
        <EmptyState title="Nur in der Desktop-App">
          JupyterLab läuft als lokaler Sidecar und ist nur in der nativen Desktop-App verfügbar.
        </EmptyState>
      </section>
    );
  }

  return (
    <section className="page jupyter-page">
      <div className="page-title">
        <div>
          <span>Notebooks</span>
          <h1>Jupyter</h1>
        </div>
        <div className="jupyter-toolbar">
          <button
            className={`button button-compact ${notesOpen ? "button-primary" : ""}`}
            type="button"
            title="Projekt-Notizen ein-/ausklappen"
            onClick={() => setNotesOpen((v) => !v)}
          >
            <NotebookPen size={15} /> Notizen
          </button>
          {status === "running" ? (
            <>
              <button className="button button-compact" type="button" onClick={restart}>
                <RefreshCw size={15} /> Neu starten
              </button>
              <button className="button button-compact" type="button" onClick={stop}>
                <Square size={15} /> Stoppen
              </button>
            </>
          ) : (
            <button
              className="button button-compact button-primary"
              type="button"
              onClick={start}
              disabled={status === "starting"}
            >
              <Play size={15} /> {status === "starting" ? "Startet…" : "JupyterLab starten"}
            </button>
          )}
        </div>
      </div>

      <div className="jupyter-body">
        <div className="jupyter-content">
          {status === "running" && url ? (
            <iframe className="jupyter-frame" src={url} title="JupyterLab" />
          ) : status === "starting" ? (
            <div className="jupyter-message">
              <Notebook size={18} /> JupyterLab startet…
            </div>
          ) : status === "error" ? (
            <EmptyState title="JupyterLab konnte nicht gestartet werden">
              {error ? <pre className="jupyter-error">{error}</pre> : null}
              Installiere es im Backend-venv: <code>pip install jupyterlab</code>, dann „JupyterLab starten".
            </EmptyState>
          ) : (
            <EmptyState title="JupyterLab ist gestoppt">
              Starte den lokalen Notebook-Server, um Notebooks direkt in PaperKG zu bearbeiten.
            </EmptyState>
          )}
        </div>
        {notesOpen ? <NotesSidePanel onClose={() => setNotesOpen(false)} /> : null}
      </div>
    </section>
  );
}
