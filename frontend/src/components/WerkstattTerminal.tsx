import { useEffect, useRef } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

import { isTauri, nativeInvoke, nativeListen } from "../native";

// Embedded PTY terminal (the "Agent" half of the Code-Werkstatt). Runs a real
// shell in the project folder via the Rust portable-pty backend, so AI coding
// CLIs (claude / Claude Code, opencode, codex), git and the shell run inside
// PaperKG. Native-only; in the web app the parent shows a hint instead.

export function WerkstattTerminal({ cwd }: { cwd: string }) {
  const containerRef = useRef<HTMLDivElement>(null);

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

        unlisteners.push(
          await nativeListen<number[]>(`terminal://output/${id}`, (bytes) => {
            term.write(new Uint8Array(bytes));
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
    };
  }, [cwd]);

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
