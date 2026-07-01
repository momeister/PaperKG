import { useEffect, useRef } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

import { isTauri, nativeInvoke, nativeListen } from "../native";

// Embedded PTY terminal (the "Agent" half of the Code-Werkstatt). Runs a real
// shell in the project folder via the Rust portable-pty backend, so AI coding
// CLIs (claude / Claude Code, opencode, codex), git and the shell run inside
// PaperKG. Native-only; in the web app the parent shows a hint instead.
//
// Props:
//  - `active`   : the tab is visible (TerminalTabs renders inactive tabs with
//                 display:none). When it becomes active we re-fit + focus, since
//                 a hidden xterm cannot measure itself.
//  - `onOutput` : raw terminal text, used by the parent to auto-detect a dev
//                 server URL for the preview pane.

export function WerkstattTerminal({
  cwd,
  active = true,
  onOutput,
}: {
  cwd: string;
  active?: boolean;
  onOutput?: (text: string) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const termIdRef = useRef<string | null>(null);
  // Keep the latest callback without re-running the spawn effect.
  const onOutputRef = useRef(onOutput);
  onOutputRef.current = onOutput;

  useEffect(() => {
    if (!isTauri() || !containerRef.current) return;

    const term = new Terminal({
      fontSize: 13,
      fontFamily: 'ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace',
      cursorBlink: true,
      theme: { background: "#1e1e1e" },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(containerRef.current);
    fit.fit();
    termRef.current = term;
    fitRef.current = fit;

    const decoder = new TextDecoder();
    let disposed = false;
    let termId: string | null = null;
    const unlisteners: Array<() => void> = [];

    (async () => {
      try {
        const id = await nativeInvoke<string>("terminal_spawn", {
          cwd,
          cols: term.cols,
          rows: term.rows,
        });
        if (disposed) {
          void nativeInvoke("terminal_kill", { id });
          return;
        }
        termId = id;
        termIdRef.current = id;

        unlisteners.push(
          await nativeListen<number[]>(`terminal://output/${id}`, (bytes) => {
            const chunk = new Uint8Array(bytes);
            term.write(chunk);
            onOutputRef.current?.(decoder.decode(chunk, { stream: true }));
          }),
        );
        unlisteners.push(
          await nativeListen<unknown>(`terminal://exit/${id}`, () => {
            term.write("\r\n\x1b[2m[Prozess beendet]\x1b[0m\r\n");
          }),
        );

        term.onData((data) => {
          void nativeInvoke("terminal_write", { id, data });
        });
      } catch (error) {
        term.write(`\r\n\x1b[31mTerminal-Start fehlgeschlagen: ${String(error)}\x1b[0m\r\n`);
      }
    })();

    // Keep the PTY size in sync with the rendered area.
    const resize = () => {
      try {
        fit.fit();
      } catch {
        return;
      }
      if (termId) {
        void nativeInvoke("terminal_resize", { id: termId, cols: term.cols, rows: term.rows });
      }
    };
    const observer = new ResizeObserver(resize);
    observer.observe(containerRef.current);

    return () => {
      disposed = true;
      observer.disconnect();
      unlisteners.forEach((un) => un());
      if (termId) void nativeInvoke("terminal_kill", { id: termId });
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
      termIdRef.current = null;
    };
  }, [cwd]);

  // When this tab becomes visible again, re-fit (a hidden xterm has zero size)
  // and focus it.
  useEffect(() => {
    if (!active) return;
    const handle = window.setTimeout(() => {
      try {
        fitRef.current?.fit();
      } catch {
        return;
      }
      const term = termRef.current;
      if (term && termIdRef.current) {
        void nativeInvoke("terminal_resize", { id: termIdRef.current, cols: term.cols, rows: term.rows });
        term.focus();
      }
    }, 0);
    return () => window.clearTimeout(handle);
  }, [active]);

  if (!isTauri()) {
    return (
      <div className="werkstatt-terminal-hint">
        Das eingebettete Terminal (für Claude Code, opencode, codex, git …) ist nur in der
        Desktop-App verfügbar.
      </div>
    );
  }

  return <div className="werkstatt-terminal" ref={containerRef} />;
}
