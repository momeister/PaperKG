import { useEffect, useRef, useState } from "react";
import { Key, UploadCloud, X } from "lucide-react";

import { api } from "../api";
import type { KaggleStatus } from "../types";

/**
 * Kaggle-Login-Dialog (Task-Focused Mode).
 *
 * Popup-Flow (Plan §Session 4):
 *  1. Nutzer lädt seine ``kaggle.json`` hoch ODER gibt Username+Key manuell ein.
 *  2. Frontend sendet das an ``POST /settings/kaggle``.
 *  3. Backend schreibt die Creds in ``.env`` (``KAGGLE_USERNAME``/``KAGGLE_KEY``,
 *     gitignored) und lädt sie in den Prozess-Env. Kein Neustart nötig.
 *  4. Erfolg → ``onLoggedIn()`` -> Parent refresht Status + schließt Dialog.
 *
 * Ohne Kaggle-Key degradiert der Backend auf Public-Scrape für offene Datensätze
 * (Plan-Entscheid: graceful degradation). Kaggle-Wettbewerbe + geschützte
 * Datensätze brauchen weiterhin Auth.
 */
type Mode = "manual" | "json";

type Props = {
  open: boolean;
  onClose: () => void;
  onLoggedIn: (status: KaggleStatus) => void;
};

export function KaggleLoginDialog({ open, onClose, onLoggedIn }: Props) {
  const [mode, setMode] = useState<Mode>("manual");
  const [username, setUsername] = useState("");
  const [key, setKey] = useState("");
  const [jsonFile, setJsonFile] = useState<File | null>(null);
  const [jsonText, setJsonText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cardRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) {
      setMode("manual");
      setUsername("");
      setKey("");
      setJsonFile(null);
      setJsonText("");
      setError(null);
      setBusy(false);
    }
  }, [open]);

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

  useEffect(() => {
    if (open) {
      const t = setTimeout(() => cardRef.current?.focus(), 30);
      return () => clearTimeout(t);
    }
  }, [open]);

  if (!open) return null;

  const canSubmit =
    !busy &&
    (mode === "manual"
      ? username.trim().length > 0 && key.trim().length > 0
      : jsonText.trim().length > 0 || jsonFile !== null);

  async function handleSubmit() {
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      let payload: { username?: string; key?: string; kaggle_json?: string };
      if (mode === "manual") {
        payload = { username: username.trim(), key: key.trim() };
      } else if (jsonFile) {
        const text = await jsonFile.text();
        // kaggle.json Inhalt direkt als String weiterreichen
        payload = { kaggle_json: text };
      } else {
        payload = { kaggle_json: jsonText.trim() };
      }
      await api.loginKaggle(payload);
      const status = await api.getKaggleStatus();
      onLoggedIn(status);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function onJsonFile(file: File | null) {
    setJsonFile(file);
    if (file) {
      try {
        const text = await file.text();
        setJsonText(text);
      } catch {
        /* file.text() kann bei sehr großen Dateien scheitern — ignorieren */
      }
    } else {
      setJsonText("");
    }
  }

  return (
    <div className="harvest-dialog-overlay" onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}>
      <div className="harvest-dialog-card kaggle-login-dialog-card" ref={cardRef} tabIndex={-1}>
        <div className="kaggle-login-head">
          <strong>Kaggle verbinden</strong>
          <button type="button" className="button button-compact" onClick={onClose} disabled={busy} aria-label="Schließen">
            <X size={14} />
          </button>
        </div>
        <p className="muted">
          Kaggle-Datensätze und Wettbewerbe brauchen Authentifizierung. Lade deine <code>kaggle.json</code> hoch
          (zu finden unter <a href="https://www.kaggle.com/settings/account" target="_blank" rel="noreferrer">kaggle.com/settings/account → Create New Token</a>)
          oder gib Username + Key manuell ein.
        </p>
        <p className="muted kaggle-login-note">
          Die Credentials werden lokal in deiner <code>.env</code> gespeichert (gitignored) und nur für Kaggle-API-Calls verwendet.
        </p>

        <div className="segmented kaggle-login-mode-tabs">
          <button type="button" className={mode === "manual" ? "active" : ""} onClick={() => setMode("manual")}>
            <Key size={14} /> <span>Username + Key</span>
          </button>
          <button type="button" className={mode === "json" ? "active" : ""} onClick={() => setMode("json")}>
            <UploadCloud size={14} /> <span>kaggle.json hochladen</span>
          </button>
        </div>

        {mode === "manual" ? (
          <div className="kaggle-login-fields">
            <label>
              Username
              <input type="text" value={username} onChange={(e) => setUsername(e.target.value)} disabled={busy} placeholder="dein-kaggle-username" autoComplete="username" />
            </label>
            <label>
              Key
              <input type="password" value={key} onChange={(e) => setKey(e.target.value)} disabled={busy} placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" autoComplete="current-password" />
            </label>
          </div>
        ) : (
          <div className="kaggle-login-fields">
            <label>
              kaggle.json
              <input type="file" accept="application/json,.json" onChange={(e) => onJsonFile(e.target.files?.[0] ?? null)} disabled={busy} />
            </label>
            {jsonFile ? <span className="muted">{jsonFile.name} ({Math.round(jsonFile.size / 1024)} KB)</span> : null}
            <details>
              <summary className="muted">…oder JSON-Inhalt direkt einfügen</summary>
              <textarea rows={5} value={jsonText} onChange={(e) => setJsonText(e.target.value)} disabled={busy}
                placeholder={'{"username":"…","key":"…"}'} />
            </details>
          </div>
        )}

        {error ? <div className="warning-row">{error}</div> : null}

        <div className="harvest-dialog-actions">
          <button type="button" className="button button-primary" onClick={handleSubmit} disabled={!canSubmit}>
            {busy ? "Verbinde…" : "Verbinden"}
          </button>
          <button type="button" className="button" onClick={onClose} disabled={busy}>
            Abbrechen
          </button>
        </div>
      </div>
    </div>
  );
}