import { useGlossary } from "../glossary/GlossaryProvider";
import { useRef, useState } from "react";
import { api } from "../api";
import { useAppState } from "../state";
import type { PdfSelection } from "../types";

export function PdfSelectionActions({ selection, onInsert, onAnnotate, onClear }: {
  selection: PdfSelection;
  onInsert?: (selection: PdfSelection, text: string, language?: string) => Promise<void>;
  onAnnotate: (body: string) => Promise<void>;
  onClear: () => void;
}) {
  const glossary = useGlossary();
  const { provider, model } = useAppState();
  const [language, setLanguage] = useState("Deutsch");
  const [translated, setTranslated] = useState<{ text: string; language: string } | null>(null);
  const [body, setBody] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  async function run(action: () => Promise<void>) {
    const id = ++generation.current;
    setBusy(true); setError("");
    try { await action(); } catch (e) { if (id === generation.current) setError(e instanceof Error ? e.message : "Aktion fehlgeschlagen"); }
    finally { if (id === generation.current) setBusy(false); }
  }
  return <div className="pdf-selection-actions" role="region" aria-label="PDF-Auswahl">
    <div className="button-row"><strong>Markierte Textstelle</strong><button className="button button-compact" type="button" aria-label="PDF-Auswahl löschen" onClick={onClear}>×</button></div>
    <p>{selection.originalText}</p>
    <div className="button-row">
      <button className="button button-compact" type="button" onClick={() => glossary.open(selection.originalText)}>Zum Wörterbuch hinzufügen</button>
      <button className="button button-compact" type="button" disabled={busy} onClick={() => setBody("")}>PDF-Notiz hinzufügen</button>
      {onInsert && <button className="button button-compact" type="button" disabled={busy} onClick={() => void run(() => onInsert(selection, selection.originalText))}>Zitat in Notiz einfügen</button>}
      <select aria-label="Sprache der PDF-Auswahl" value={language} onChange={e => setLanguage(e.target.value)}>{["Deutsch", "English", "Français", "Español", "Italiano"].map(l => <option key={l}>{l}</option>)}</select>
      <button className="button button-compact" type="button" disabled={busy} onClick={() => void run(async () => {
        const result = await api.rewriteNote({ text: selection.originalText, instruction: `Übersetze nach ${language}. Gib nur die Übersetzung aus.`, provider, model });
        setTranslated({ text: result.text, language });
      })}>{busy ? "Bitte warten…" : "Übersetzen"}</button>
    </div>
    {body !== null && <div><textarea aria-label="PDF-Notiz" value={body} onChange={e => setBody(e.target.value)} /><button className="button button-compact" type="button" disabled={busy || !body.trim()} onClick={() => void run(async () => { await onAnnotate(body); setBody(null); })}>PDF-Notiz speichern</button></div>}
    {translated && <div><strong>Übersetzung ({translated.language})</strong><p>{translated.text}</p>{onInsert && <button className="button button-compact" type="button" disabled={busy} onClick={() => void run(() => onInsert(selection, translated.text, translated.language))}>Übersetzung als Zitat einfügen</button>}</div>}
    {error && <div className="inline-error" role="alert">{error}</div>}
  </div>;
}
