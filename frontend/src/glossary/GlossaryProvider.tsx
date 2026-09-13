import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import { useAppState } from "../state";
import type { GlossaryEntry } from "../types";

const QUERY_KEY = ["glossary"];
const Context = createContext({ entries: [] as GlossaryEntry[], enabled: true, expanded: false, loading: false, error: "", setEnabled: (_: boolean) => {}, setExpanded: (_: boolean) => {}, open: (_text = "", _entry?: GlossaryEntry) => {} });
export const useGlossary = () => useContext(Context);
const read = (key: string, fallback: boolean) => { try { const value = localStorage.getItem(key); return value === null ? fallback : value === "true"; } catch { return fallback; } };
const write = (key: string, value: boolean) => { try { localStorage.setItem(key, String(value)); } catch { /* local storage may be unavailable */ } };
const errorText = (e: unknown) => e instanceof Error ? e.message : "Aktion fehlgeschlagen";

type Draft = { key: number; text: string; entry?: GlossaryEntry };
export function GlossaryProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const query = useQuery({ queryKey: QUERY_KEY, queryFn: api.listGlossary, staleTime: 30000 });
  const [enabled, enable] = useState(() => read("sciencekg.glossary.enabled", true));
  const [expanded, expand] = useState(() => read("sciencekg.glossary.expanded", false));
  const [draft, setDraft] = useState<Draft | null>(null);
  const counter = useRef(0);
  const open = useCallback((text = "", entry?: GlossaryEntry) => setDraft({ key: ++counter.current, text, entry }), []);
  const refresh = useCallback(async () => { await client.invalidateQueries({ queryKey: QUERY_KEY }); }, [client]);
  useEffect(() => {
    const sync = (event: StorageEvent) => {
      if (event.key === "sciencekg.glossary.enabled") enable(read(event.key, true));
      if (event.key === "sciencekg.glossary.expanded") expand(read(event.key, false));
      if (event.key === "sciencekg.glossary.updated") void refresh();
    };
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, [refresh]);
  const value = useMemo(() => ({ entries: query.data?.items ?? [], enabled, expanded, loading: query.isPending, error: query.error ? errorText(query.error) : "", open,
    setEnabled: (value: boolean) => { enable(value); write("sciencekg.glossary.enabled", value); },
    setExpanded: (value: boolean) => { expand(value); write("sciencekg.glossary.expanded", value); },
  }), [query.data, query.error, query.isPending, enabled, expanded, open]);
  return <Context.Provider value={value}>{children}{draft && <GlossaryDialog key={draft.key} draft={draft} close={() => setDraft(null)} open={open} refresh={refresh} />}</Context.Provider>;
}

export async function glossaryChanged(client: ReturnType<typeof useQueryClient>) {
  await client.invalidateQueries({ queryKey: QUERY_KEY });
  try { localStorage.setItem("sciencekg.glossary.updated", String(Date.now())); } catch { /* optional cross-window refresh */ }
}

export function GlossaryPanel({ managementOnly = false }: { managementOnly?: boolean }) {
  const { entries, enabled, expanded, setEnabled, setExpanded, open, loading, error } = useGlossary();
  const client = useQueryClient();
  const [search, setSearch] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");
  return <section className="glossary-panel" aria-label="Wörterbuch">
    <div className="button-row">{!managementOnly && <button type="button" className="button button-compact" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? "▾" : "▸"} Wörterbuch ({entries.length})</button>}
      <label><input type="checkbox" checked={enabled} onChange={event => setEnabled(event.target.checked)} /> Begriffshinweise anzeigen</label></div>
    {(managementOnly || expanded) && <div className="glossary-management">
      <div className="button-row"><input aria-label="Wörterbuch durchsuchen" placeholder="Begriff suchen" value={search} onChange={e => setSearch(e.target.value)} /><button className="button button-compact" onClick={() => open()}>Begriff anlegen</button></div>
      {loading && <p>Lädt…</p>}{(error || failure) && <p role="alert">{error || failure}</p>}
      {!loading && !error && entries.length === 0 && <p>Noch keine Begriffe. Lege einen Begriff an oder markiere Text in einer Notiz oder PDF.</p>}
      <ul>{entries.filter(e => `${e.term} ${e.explanation}`.toLocaleLowerCase().includes(search.toLocaleLowerCase())).map(entry => <li key={entry.id}>
        <div><strong>{entry.term}</strong><p>{entry.explanation}</p></div>
        <button className="button button-compact" onClick={() => open("", entry)}>Bearbeiten</button>
        {deleting === entry.id ? <><span>Begriff löschen?</span><button className="button button-compact" disabled={busy} onClick={async () => { setBusy(true); setFailure(""); try { await api.deleteGlossary(entry.id); await glossaryChanged(client); setDeleting(null); } catch (e) { setFailure(errorText(e)); } finally { setBusy(false); } }}>Löschen bestätigen</button><button className="button button-compact" onClick={() => setDeleting(null)}>Abbrechen</button></> : <button className="button button-compact" onClick={() => setDeleting(entry.id)}>Löschen</button>}
      </li>)}</ul>
    </div>}
  </section>;
}

function GlossaryDialog({ draft, close, open, refresh }: { draft: Draft; close: () => void; open: (text: string, entry?: GlossaryEntry) => void; refresh: () => Promise<void> }) {
  const { provider, model } = useAppState();
  const [term, setTerm] = useState(draft.entry?.term ?? draft.text);
  const [explanation, setExplanation] = useState(draft.entry?.explanation ?? "");
  const [suggestion, setSuggestion] = useState("");
  const [error, setError] = useState("");
  const [duplicate, setDuplicate] = useState<GlossaryEntry | null>(null);
  const [suggesting, setSuggesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const generation = useRef(0);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
    return () => { generation.current++; previous?.focus({ preventScroll: true }); };
  }, []);
  async function suggest() {
    const id = ++generation.current;
    setSuggesting(true); setError(""); setSuggestion("");
    try { const result = await api.suggestGlossary({ term, selected_text: draft.text, provider, model }); if (generation.current === id) setSuggestion(result.suggestion); }
    catch (e) { if (generation.current === id) setError(errorText(e)); }
    finally { if (generation.current === id) setSuggesting(false); }
  }
  async function save() {
    const id = ++generation.current;
    setSuggesting(false); setSaving(true); setError(""); setDuplicate(null);
    try {
      if (draft.entry) await api.updateGlossary(draft.entry.id, { term, explanation }); else await api.createGlossary({ term, explanation });
      await refresh();
      try { localStorage.setItem("sciencekg.glossary.updated", String(Date.now())); } catch { /* optional */ }
      if (generation.current === id) close();
    } catch (e) {
      if (generation.current !== id) return;
      setError(errorText(e));
      if (e instanceof ApiError && e.status === 409 && typeof e.detail === "object" && e.detail && "entry" in e.detail) setDuplicate(e.detail.entry as GlossaryEntry);
    } finally { if (generation.current === id) setSaving(false); }
  }
  return <dialog ref={dialog} className="glossary-dialog" aria-labelledby="glossary-dialog-title" onCancel={e => { e.preventDefault(); if (!saving) close(); }}>
    <form onSubmit={e => { e.preventDefault(); void save(); }}>
      <h2 id="glossary-dialog-title">{draft.entry ? "Begriff bearbeiten" : "Zum Wörterbuch hinzufügen"}</h2>
      <label>Begriff<input aria-label="Begriff" disabled={saving} autoFocus required maxLength={500} value={term} onChange={e => { setTerm(e.target.value); generation.current++; setSuggesting(false); setSuggestion(""); setDuplicate(null); }} /></label>
      <label>Erklärung<textarea aria-label="Erklärung" disabled={saving} required maxLength={16000} rows={5} value={explanation} onChange={e => setExplanation(e.target.value)} /></label>
      <button className="button" type="button" disabled={!term.trim() || term.length > 500 || draft.text.length > 16000 || suggesting || saving} onClick={() => void suggest()}>{suggesting ? "Vorschlag wird angefordert…" : "KI-Vorschlag anfordern"}</button>
      {suggestion && <div className="glossary-suggestion"><strong>KI-Vorschlag</strong><p>{suggestion}</p><button className="button" type="button" onClick={() => setExplanation(suggestion)}>Vorschlag übernehmen</button></div>}
      {error && <p role="alert">{error}</p>}{duplicate && <button className="button" type="button" onClick={() => open("", duplicate)}>Vorhandenen Eintrag bearbeiten</button>}
      <div className="button-row"><button className="button button-primary" type="submit" disabled={!term.trim() || !explanation.trim() || saving}>Speichern</button><button className="button" type="button" disabled={saving} onClick={close}>Abbrechen</button></div>
    </form>
  </dialog>;
}
