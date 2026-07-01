import { useEffect, useRef, useState } from "react";
import { ExternalLink, Globe, RotateCw } from "lucide-react";

import { openExternal } from "../native";

// Live-Vorschau für den Coding-Agent-Modus: eine Adressleiste über einem iframe,
// das den lokalen Dev-Server rendert, den der Agent im Terminal startet. Die URL
// kommt entweder per Auto-Erkennung aus der Terminal-Ausgabe (siehe
// WorkstationPage) oder wird hier manuell eingegeben.

/** Normalize a preview URL: add http:// if missing, rewrite 0.0.0.0 → 127.0.0.1. */
export function normalizePreviewUrl(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return "";
  const withScheme = /^https?:\/\//i.test(trimmed) ? trimmed : `http://${trimmed}`;
  return withScheme.replace(/\/\/0\.0\.0\.0\b/, "//127.0.0.1");
}

export function PreviewPane({ url, onUrlChange }: { url: string; onUrlChange: (url: string) => void }) {
  const [input, setInput] = useState(url);
  const [reloadKey, setReloadKey] = useState(0);
  const lastUrlRef = useRef(url);

  // Keep the address bar in sync when the URL is set from the outside
  // (auto-detected from the terminal), without clobbering manual edits.
  useEffect(() => {
    if (url !== lastUrlRef.current) {
      lastUrlRef.current = url;
      setInput(url);
    }
  }, [url]);

  const normalized = normalizePreviewUrl(url);

  function submit() {
    const next = normalizePreviewUrl(input);
    if (next !== input) setInput(next);
    if (next && next !== url) onUrlChange(next);
    setReloadKey((key) => key + 1);
  }

  return (
    <div className="wk-pane wk-preview">
      <div className="wk-preview-bar">
        <button
          className="wk-iconbtn"
          type="button"
          title="Neu laden"
          disabled={!normalized}
          onClick={() => setReloadKey((key) => key + 1)}
        >
          <RotateCw size={14} />
        </button>
        <input
          className="wk-preview-url"
          placeholder="http://localhost:5173"
          spellCheck={false}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && submit()}
          onBlur={submit}
        />
        <button
          className="wk-iconbtn"
          type="button"
          title="Im Browser öffnen"
          disabled={!normalized}
          onClick={() => normalized && void openExternal(normalized)}
        >
          <ExternalLink size={14} />
        </button>
      </div>
      {normalized ? (
        <iframe key={reloadKey} className="wk-preview-frame" src={normalized} title="Vorschau" />
      ) : (
        <div className="wk-preview-empty">
          <Globe size={30} />
          <strong>Keine Vorschau-URL</strong>
          <span>
            Starte im Terminal einen Dev-Server (z. B. <code>npm run dev</code>) — die URL wird automatisch
            erkannt — oder gib sie oben manuell ein.
          </span>
        </div>
      )}
    </div>
  );
}
