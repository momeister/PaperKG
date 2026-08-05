/**
 * Code lesen **und ändern**, ohne die Seite zu verlassen — wahlweise nur die
 * Funktion oder die ganze Datei.
 *
 * Vorher stand hier ein `<pre>`: gut zum Nachschlagen, nutzlos in dem Moment,
 * in dem man versteht, was falsch ist. Dann war es die ganze Datei — richtig,
 * wenn man die Datei meint, aber falsch für den häufigen Fall: man kommt aus
 * einer Antwort, hat eine von zwölf beteiligten Funktionen vor sich und will
 * *diese* ändern. Neunhundert Zeilen drumherum sind dann kein Zusammenhang,
 * sondern Suchaufwand.
 *
 * Deshalb zwei dünne Nutzer über einem gemeinsamen `MonacoHost`:
 *
 *   * **Funktion** (Standard) — nur die Zeilen des Symbols. Geschrieben wird
 *     über `PATCH …/symbol/{id}/source`; der Zeilenbereich kommt dabei aus dem
 *     Graphen, nicht von hier, und der `content_hash` ist eine optimistische
 *     Sperre gegen stilles Überschreiben.
 *   * **Datei** — das bisherige Verhalten über `PUT /workspaces/{id}/file`,
 *     unverändert. Manche Änderungen sind nun einmal keine Funktionsänderung
 *     (Import dazu, zwei Stellen gleichzeitig).
 *
 * Nach jedem Schreiben ist der Index veraltet: die Zeilennummern im Graphen
 * zeigen woanders hin als der Text auf der Platte. Das wird gesagt, nicht
 * verschwiegen — und mit einem Knopf zum Neuindizieren verbunden.
 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ExternalLink, RefreshCw, RotateCcw, Save } from "lucide-react";

import { api } from "../../api";
import { languageForPath } from "../../monaco-setup";
import type { CodeNodeDetail } from "../../types";
import { MonacoHost } from "./MonacoHost";

export type CodeEditorMode = "symbol" | "file";

export type CodeEditorPanelProps = {
  projectId: string;
  node: CodeNodeDetail | null;
  /** Wird nach dem Speichern gerufen — der Index ist dann veraltet. */
  onSaved?: () => void;
  onReindex?: () => void;
  onOpenInWorkstation?: (path: string, line: number) => void;
  /** Womit begonnen wird. Der Sprung aus einer Trefferliste meint die Funktion. */
  initialMode?: CodeEditorMode;
};

/** Kopfzeile beider Ansichten — gleiche Gesten, damit der Wechsel nichts kostet. */
function EditorBar({
  path,
  range,
  dirty,
  saving,
  disabled,
  mode,
  onModeChange,
  onRevert,
  onSave,
  onOpenInWorkstation,
}: {
  path: string;
  range: string;
  dirty: boolean;
  saving: boolean;
  disabled: boolean;
  mode: CodeEditorMode;
  onModeChange: (mode: CodeEditorMode) => void;
  onRevert: () => void;
  onSave: () => void;
  onOpenInWorkstation?: () => void;
}) {
  return (
    <div className="cgp-editor-bar">
      <div className="segmented cgp-editor-scope">
        <button
          className={mode === "symbol" ? "active" : ""}
          onClick={() => onModeChange("symbol")}
          title="Nur die Zeilen dieser Funktion"
        >
          Funktion
        </button>
        <button
          className={mode === "file" ? "active" : ""}
          onClick={() => onModeChange("file")}
          title="Die ganze Datei"
        >
          Datei
        </button>
      </div>
      <span className="cgp-mono cgp-editor-path" title={path}>
        {path}
        <em>{range}</em>
      </span>
      <span className="cgp-spacer" />
      {dirty && <span className="cgp-dirty">ungespeichert</span>}
      <button
        className="wk-iconbtn"
        title="Änderungen verwerfen"
        onClick={onRevert}
        disabled={!dirty}
      >
        <RotateCcw size={14} />
      </button>
      {onOpenInWorkstation && (
        <button
          className="wk-iconbtn"
          title="In der Werkstatt öffnen (Terminal, git-Diff, Dateibaum)"
          onClick={onOpenInWorkstation}
        >
          <ExternalLink size={14} />
        </button>
      )}
      <button
        className="wk-btn wk-btn--primary"
        onClick={onSave}
        disabled={!dirty || saving || disabled}
        title="Speichern (Strg/Cmd+S)"
      >
        <Save size={14} /> {saving ? "speichert …" : "Speichern"}
      </button>
    </div>
  );
}

function Notice({ text, onReindex }: { text: string; onReindex?: () => void }) {
  return (
    <div className="cgp-map-banner">
      <AlertTriangle size={14} />
      <span>{text}</span>
      {onReindex && (
        <button className="wk-btn" onClick={onReindex}>
          <RefreshCw size={13} /> Neu indizieren
        </button>
      )}
    </div>
  );
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** Nur die Funktion. Der Zeilenbereich ist Sache des Backends. */
function SymbolEditor({
  projectId,
  node,
  mode,
  onModeChange,
  onSaved,
  onReindex,
  onOpenInWorkstation,
}: {
  projectId: string;
  node: CodeNodeDetail;
  mode: CodeEditorMode;
  onModeChange: (mode: CodeEditorMode) => void;
  onSaved?: () => void;
  onReindex?: () => void;
  onOpenInWorkstation?: (path: string, line: number) => void;
}) {
  const queryClient = useQueryClient();
  const [value, setValue] = useState("");
  const [saved, setSaved] = useState("");
  const [note, setNote] = useState<string | null>(null);

  const source = useQuery({
    queryKey: ["codegraph", "symbolSource", projectId, node.id],
    queryFn: () => api.codegraph.symbolSource(projectId, node.id),
    gcTime: 60_000,
  });

  useEffect(() => {
    const text = source.data?.text;
    if (text == null) return;
    setValue(text);
    setSaved(text);
    setNote(null);
  }, [source.data?.text, node.id]);

  const write = useMutation({
    mutationFn: () =>
      api.codegraph.writeSymbolSource(projectId, node.id, {
        text: value,
        content_hash: source.data!.content_hash,
      }),
    onSuccess: () => {
      setSaved(value);
      setNote("Gespeichert. Der Index kennt noch den alten Stand.");
      // Beide Sichten neu holen: die Datei drumherum hat sich mitgeändert.
      void queryClient.invalidateQueries({
        queryKey: ["codegraph", "symbolSource", projectId, node.id],
      });
      void queryClient.invalidateQueries({ queryKey: ["werkstatt", "file", projectId] });
      onSaved?.();
    },
    // 409 ist der interessante Fall und kommt mit einem verständlichen Satz aus
    // dem Backend — er wird durchgereicht statt in „Fehler" übersetzt.
    onError: (error: unknown) => setNote(`Nicht gespeichert: ${errorText(error)}`),
  });

  // Vorschalt-Dialog: vor dem Schreiben zeigen, was die Änderung mitreißt.
  // Abschaltbar — ein Dialog vor jedem Speichern wird sonst zum Wegklick-Reflex.
  const [impactPreview, setImpactPreview] = useState<import("../../types").CodeImpact | null>(null);
  const [impactLoading, setImpactLoading] = useState(false);

  const gateSave = () => {
    if (write.isPending || source.data?.stale || !dirty) return;
    const skip = typeof window !== "undefined" && window.localStorage.getItem("sciencekg.code.impactSkip");
    if (skip === "1") {
      write.mutate();
      return;
    }
    setImpactLoading(true);
    api.codegraph
      .impact(projectId, node.id, { depth: 3 })
      .then((data) => setImpactPreview(data))
      .catch(() => write.mutate()) // ohne Auswirkungsanalyse nicht blockieren
      .finally(() => setImpactLoading(false));
  };

  const dirty = value !== saved;
  const language = useMemo(() => languageForPath(node.path), [node.path]);
  const lineCount = useMemo(() => value.split("\n").length, [value]);

  return (
    <div className="cgp-editor">
      <EditorBar
        path={node.path}
        range={`:${source.data?.start_line ?? node.span.start_line}–${
          source.data?.end_line ?? node.span.end_line
        }`}
        dirty={dirty}
        saving={write.isPending}
        disabled={Boolean(source.data?.stale) || !source.data}
        mode={mode}
        onModeChange={onModeChange}
        onRevert={() => {
          setValue(saved);
          setNote(null);
        }}
        onSave={() => gateSave()}
        onOpenInWorkstation={
          onOpenInWorkstation
            ? () => onOpenInWorkstation(node.path, source.data?.start_line ?? node.span.start_line)
            : undefined
        }
      />

      {source.data?.stale && (
        <Notice
          text="Die Datei hat sich seit dem Indizieren geändert — der Zeilenbereich ist nicht mehr verlässlich. Zum Ändern erst neu indizieren oder die ganze Datei öffnen."
          onReindex={onReindex}
        />
      )}
      {note && (
        <Notice
          text={note}
          onReindex={
            onReindex
              ? () => {
                  setNote(null);
                  onReindex();
                }
              : undefined
          }
        />
      )}

      <div className="cgp-editor-body">
        {source.isLoading ? (
          <p className="muted cg-empty">Funktion wird geladen …</p>
        ) : source.isError ? (
          <p className="cg-notice cg-notice--error">
            <AlertTriangle size={14} /> <span>{errorText(source.error)}</span>
          </p>
        ) : (
          <MonacoHost
            // Eigener Modellpfad: sonst teilte sich die Funktionsansicht den
            // Rückgängig-Verlauf mit der Dateiansicht, und ein Strg+Z darin
            // schriebe Dateizeilen in den Funktionstext.
            modelPath={`${projectId}/symbol/${node.id}/${node.path}`}
            language={language}
            value={value}
            onChange={setValue}
            onSave={() => {
              if (dirty && !write.isPending && !source.data?.stale) gateSave();
            }}
            readOnly={Boolean(source.data?.stale)}
            highlight={{ start: 1, end: lineCount }}
          />
        )}
      </div>

      {impactLoading && (
        <div className="cg-impact-dialog" role="status">
          Berechne, was diese Änderung mitreißt…
        </div>
      )}
      {impactPreview && (
        <div className="cg-impact-dialog">
          <div className="cg-impact-dialog-head">
            Diese Änderung betrifft {impactPreview.reached.length} Symbole in {impactPreview.files.length} Dateien.
            {impactPreview.tests.length > 0 && (
              <span> {impactPreview.tests.length} Tests decken die Stelle ab.</span>
            )}
            {impactPreview.truncated && <span className="cg-impact-truncated"> (Radius gekürzt)</span>}
          </div>
          <ul className="cg-impact-dialog-list">
            {impactPreview.direct_callers.slice(0, 6).map((c) => (
              <li key={c.node.id}>
                <code>{c.node.path}:{c.node.line}</code> {c.node.name}
              </li>
            ))}
          </ul>
          <label className="cg-impact-skip">
            <input
              type="checkbox"
              onChange={(e) => {
                if (typeof window !== "undefined") {
                  window.localStorage.setItem("sciencekg.code.impactSkip", e.target.checked ? "1" : "0");
                }
              }}
            />
            nicht mehr fragen
          </label>
          <div className="cg-impact-dialog-actions">
            <button className="wk-btn" onClick={() => setImpactPreview(null)}>
              Abbrechen
            </button>
            <button
              className="wk-btn wk-btn--primary"
              onClick={() => {
                setImpactPreview(null);
                write.mutate();
              }}
            >
              Trotzdem speichern
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/** Die ganze Datei — das bisherige Verhalten, unverändert. */
function FileEditor({
  projectId,
  node,
  mode,
  onModeChange,
  onSaved,
  onReindex,
  onOpenInWorkstation,
}: {
  projectId: string;
  node: CodeNodeDetail;
  mode: CodeEditorMode;
  onModeChange: (mode: CodeEditorMode) => void;
  onSaved?: () => void;
  onReindex?: () => void;
  onOpenInWorkstation?: (path: string, line: number) => void;
}) {
  const queryClient = useQueryClient();
  const [value, setValue] = useState("");
  const [saved, setSaved] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const path = node.path;

  const file = useQuery({
    queryKey: ["werkstatt", "file", projectId, path],
    queryFn: () => api.werkstatt.readFile(projectId, path),
    gcTime: 60_000,
  });

  useEffect(() => {
    const content = file.data?.content;
    if (content == null) return;
    setValue(content);
    setSaved(content);
  }, [file.data?.content, path]);

  // Binär oder zu gross: dann gibt es nichts zu bearbeiten, und ein leerer
  // Editor, der beim Speichern die Datei leert, wäre die schlechteste Antwort.
  const unopenable = file.data?.binary
    ? "Binärdatei — im Editor nicht darstellbar."
    : file.data?.too_large
      ? "Datei zu gross für den Editor."
      : null;

  const save = useMutation({
    mutationFn: () => api.werkstatt.writeFile(projectId, path, value),
    onSuccess: () => {
      setSaved(value);
      setNote("Gespeichert. Der Index kennt noch den alten Stand.");
      void queryClient.invalidateQueries({ queryKey: ["werkstatt", "file", projectId, path] });
      void queryClient.invalidateQueries({
        queryKey: ["codegraph", "symbolSource", projectId, node.id],
      });
      onSaved?.();
    },
    onError: (error: unknown) => setNote(`Nicht gespeichert: ${errorText(error)}`),
  });

  const dirty = value !== saved;
  const language = useMemo(() => languageForPath(path), [path]);

  return (
    <div className="cgp-editor">
      <EditorBar
        path={path}
        range={`:${node.span.start_line}${
          node.span.end_line > node.span.start_line ? `–${node.span.end_line}` : ""
        }`}
        dirty={dirty}
        saving={save.isPending}
        disabled={Boolean(unopenable)}
        mode={mode}
        onModeChange={onModeChange}
        onRevert={() => {
          setValue(saved);
          setNote(null);
        }}
        onSave={() => save.mutate()}
        onOpenInWorkstation={
          onOpenInWorkstation ? () => onOpenInWorkstation(path, node.span.start_line) : undefined
        }
      />

      {note && (
        <Notice
          text={note}
          onReindex={
            onReindex
              ? () => {
                  setNote(null);
                  onReindex();
                }
              : undefined
          }
        />
      )}

      <div className="cgp-editor-body">
        {file.isLoading ? (
          <p className="muted cg-empty">Datei wird geladen …</p>
        ) : file.isError ? (
          <p className="cg-notice cg-notice--error">
            <AlertTriangle size={14} /> <span>{errorText(file.error)}</span>
          </p>
        ) : unopenable ? (
          <p className="muted cg-empty">{unopenable}</p>
        ) : (
          <MonacoHost
            modelPath={`${projectId}/${path}`}
            language={language}
            value={value}
            onChange={setValue}
            onSave={() => {
              if (dirty && !save.isPending) save.mutate();
            }}
            highlight={{ start: node.span.start_line, end: node.span.end_line }}
          />
        )}
      </div>
    </div>
  );
}

export function CodeEditorPanel({
  projectId,
  node,
  onSaved,
  onReindex,
  onOpenInWorkstation,
  initialMode = "symbol",
}: CodeEditorPanelProps) {
  const [mode, setMode] = useState<CodeEditorMode>(initialMode);

  if (!node) {
    return (
      <p className="muted cg-empty">
        Wähle ein Symbol — hier steht dann sein Quelltext, und du kannst ihn ändern.
      </p>
    );
  }

  const shared = {
    projectId,
    node,
    mode,
    onModeChange: setMode,
    onSaved,
    onReindex,
    onOpenInWorkstation,
  };
  return mode === "symbol" ? <SymbolEditor {...shared} /> : <FileEditor {...shared} />;
}

export default CodeEditorPanel;
