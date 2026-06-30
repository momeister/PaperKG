// Offline Monaco setup for the Code-Werkstatt.
//
// `@monaco-editor/react` loads Monaco from a CDN by default — unacceptable for a
// fully-local / native desktop app. We instead bundle `monaco-editor` and point
// the loader at it, and wire up Monaco's web workers through Vite's `?worker`
// imports so syntax services run locally without any network access.
//
// Import this module once (for its side effects) before rendering an editor.
import * as monaco from "monaco-editor";
import editorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import jsonWorker from "monaco-editor/esm/vs/language/json/json.worker?worker";
import cssWorker from "monaco-editor/esm/vs/language/css/css.worker?worker";
import htmlWorker from "monaco-editor/esm/vs/language/html/html.worker?worker";
import tsWorker from "monaco-editor/esm/vs/language/typescript/ts.worker?worker";
import { loader } from "@monaco-editor/react";

const monacoEnvironment: monaco.Environment = {
  getWorker(_workerId: string, label: string): Worker {
    if (label === "json") return new jsonWorker();
    if (label === "css" || label === "scss" || label === "less") return new cssWorker();
    if (label === "html" || label === "handlebars" || label === "razor") return new htmlWorker();
    if (label === "typescript" || label === "javascript") return new tsWorker();
    return new editorWorker();
  },
};
(self as typeof self & { MonacoEnvironment?: monaco.Environment }).MonacoEnvironment = monacoEnvironment;

// Use the bundled monaco instead of fetching it from a CDN.
loader.config({ monaco });

/** Map a file path to a Monaco language id (best-effort; defaults to plaintext). */
export function languageForPath(path: string): string {
  const name = path.toLowerCase();
  const ext = name.includes(".") ? name.slice(name.lastIndexOf(".") + 1) : "";
  const byExt: Record<string, string> = {
    ts: "typescript", tsx: "typescript", js: "javascript", jsx: "javascript",
    mjs: "javascript", cjs: "javascript", py: "python", rs: "rust", go: "go",
    java: "java", c: "c", h: "c", cpp: "cpp", cc: "cpp", hpp: "cpp", cs: "csharp",
    rb: "ruby", php: "php", sh: "shell", bash: "shell", zsh: "shell", ps1: "powershell",
    sql: "sql", json: "json", jsonc: "json", yaml: "yaml", yml: "yaml", toml: "ini",
    ini: "ini", cfg: "ini", md: "markdown", markdown: "markdown", html: "html",
    htm: "html", xml: "xml", css: "css", scss: "scss", less: "less", svg: "xml",
    dockerfile: "dockerfile", lua: "lua", r: "r", kt: "kotlin", swift: "swift",
    vue: "html", svelte: "html",
  };
  if (byExt[ext]) return byExt[ext];
  if (name.endsWith("dockerfile")) return "dockerfile";
  if (name.endsWith(".gitignore") || name.endsWith(".env")) return "ini";
  return "plaintext";
}
