/**
 * Der Editor selbst — einmal, für beide Betriebsarten.
 *
 * Monaco zieht fünf Worker-Chunks nach. Es darf deshalb genau **einen** Ort
 * geben, der ihn importiert: `CodeEditorPanel` wird von `CodeGraphPage` per
 * `lazy` geladen und nimmt diese Datei statisch mit. Ein zweiter Import — etwa
 * im Inspektor oder in der Chat-Trefferliste — zöge Monaco in den Chunk der
 * Seite und damit näher an den Entry-Chunk, den fünf Webviews parsen. Wer von
 * dort aus bearbeiten will, schaltet die Mittelspalte um, statt einen zweiten
 * Editor einzuhängen.
 *
 * Die drei Dinge, die hier und nur hier stehen: das Thema, Strg/Cmd+S über ein
 * Ref (nicht über die Closure) und die Markierung des betrachteten Bereichs.
 */
import { useEffect, useRef } from "react";
import Editor from "@monaco-editor/react";

import { THEME_META, useAppState } from "../../state";

export type MonacoHostProps = {
  /**
   * Monacos Modellpfad. Er bestimmt, welcher Rückgängig-Verlauf zu welchem Text
   * gehört — zwei Ansichten desselben Textes brauchen deshalb *verschiedene*
   * Pfade, sonst erbt die Funktionsansicht den Verlauf der Dateiansicht.
   */
  modelPath: string;
  language: string;
  value: string;
  onChange: (next: string) => void;
  onSave: () => void;
  readOnly?: boolean;
  /**
   * Zeilen, die hervorgehoben und angesprungen werden — 1-basiert im
   * *angezeigten* Text. In der Funktionsansicht ist das die ganze Länge, in der
   * Dateiansicht der Bereich des Symbols.
   */
  highlight?: { start: number; end: number } | null;
};

export function MonacoHost({
  modelPath,
  language,
  value,
  onChange,
  onSave,
  readOnly,
  highlight,
}: MonacoHostProps) {
  const { theme } = useAppState();
  const editorRef = useRef<unknown>(null);

  // Über ein Ref, nicht über die Closure: `addCommand` läuft einmal beim
  // Einhängen und hielte sonst für immer den ersten Textstand fest — Strg+S
  // schriebe die Datei von vor zwanzig Tastendrücken.
  const saveRef = useRef(onSave);
  saveRef.current = onSave;

  // Springen, wenn sich der Bereich ändert, ohne dass der Editor neu einhängt.
  useEffect(() => {
    const editor = editorRef.current as {
      revealLineInCenter?: (line: number) => void;
      setPosition?: (position: { lineNumber: number; column: number }) => void;
    } | null;
    if (!editor || !highlight) return;
    window.requestAnimationFrame(() => {
      editor.revealLineInCenter?.(highlight.start);
      editor.setPosition?.({ lineNumber: highlight.start, column: 1 });
    });
  }, [highlight?.start, highlight?.end]);

  return (
    <Editor
      height="100%"
      theme={THEME_META[theme].scheme === "dark" ? "vs-dark" : "light"}
      path={modelPath}
      language={language}
      value={value}
      onChange={(next) => onChange(next ?? "")}
      onMount={(editor, monaco) => {
        editorRef.current = editor;
        if (highlight) {
          // In einer 900-Zeilen-Datei ist sonst nicht zu sehen, wo das aufhört,
          // worüber man gerade liest — und beim Scrollen findet man es nicht
          // wieder.
          editor.createDecorationsCollection([
            {
              range: new monaco.Range(highlight.start, 1, highlight.end, 1),
              options: {
                isWholeLine: true,
                className: "cgp-span-line",
                linesDecorationsClassName: "cgp-span-gutter",
              },
            },
          ]);
          editor.revealLineInCenter(highlight.start);
          editor.setPosition({ lineNumber: highlight.start, column: 1 });
        }
        editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => saveRef.current());
      }}
      options={{
        fontSize: 13,
        minimap: { enabled: true, size: "fit" },
        scrollBeyondLastLine: false,
        automaticLayout: true,
        tabSize: 2,
        readOnly: Boolean(readOnly),
      }}
    />
  );
}

export default MonacoHost;
