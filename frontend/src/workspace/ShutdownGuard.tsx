import { useContext, useEffect, useRef, useState } from "react";
import { isTauri, nativeInvoke, nativeListen } from "../native";
import { noteDrafts } from "./noteDrafts";
import { WorkspaceWindowsContext } from "./PortablePane";

const work = new Map<() => void | Promise<void>, "save" | "stop">();
export function registerWorkspaceShutdown(flush: () => void | Promise<void>, phase: "save" | "stop" = "save") {
  work.set(flush, phase); return () => { work.delete(flush); };
}

export function ShutdownGuard() {
  const manager = useContext(WorkspaceWindowsContext);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const pending = useRef(false);
  const dialog = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!saving || !dialog.current) return;
    dialog.current.focus();
    // The same note may also be open on another route outside WorkspaceHost.
    // Freeze every branch behind the modal, while keeping its actions usable.
    const blocked = new Map<HTMLElement, boolean>();
    let branch: HTMLElement = dialog.current;
    while (branch.parentElement) {
      for (const sibling of branch.parentElement.children) if (sibling !== branch && sibling instanceof HTMLElement) {
        blocked.set(sibling, sibling.inert); sibling.inert = true;
      }
      branch = branch.parentElement;
    }
    return () => { for (const [node, inert] of blocked) node.inert = inert; };
  }, [saving]);
  const finish = async () => {
    if (pending.current) return;
    pending.current = true; manager?.setInputLocked(true); setSaving(true); setError("");
    let timer: ReturnType<typeof setTimeout> | undefined;
    let cancelled = false;
    try {
      await Promise.race([
        (async () => {
          const { flushAssistantSessions } = await import("../pages/assistantSession");
          for (const [flush, phase] of work) if (phase === "save") await flush();
          await noteDrafts.flushAll(); await flushAssistantSessions();
          if (cancelled) return;
          for (const [stop, phase] of work) if (phase === "stop") await stop();
          // Stream abort handlers can enqueue a final session revision.
          await new Promise(resolve => setTimeout(resolve, 0));
          await noteDrafts.flushAll(); await flushAssistantSessions();
        })(),
        new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error("Speichern dauert zu lange. Die App bleibt geöffnet.")), 15000); })
      ]);
      await nativeInvoke("workspace_finish_exit");
    } catch (error) { setError(error instanceof Error ? error.message : String(error)); }
    finally { cancelled = true; clearTimeout(timer); manager?.setInputLocked(false); pending.current = false; setSaving(false); }
  };
  useEffect(() => {
    noteDrafts.recover();
    if (!isTauri()) return;
    let stopped = false, unlisten: (() => void) | undefined;
    void nativeListen("workspace-exit-request", () => { void finish(); }).then(off => { if (stopped) off(); else unlisten = off; })
      .catch(error => setError(String(error)));
    return () => { stopped = true; unlisten?.(); };
  }, []);
  if (!error && !saving) return null;
  return <div className="harvest-dialog-overlay" role="dialog" aria-modal="true" aria-label="Arbeitsplatz sichern">
    <div className="harvest-dialog-card" ref={dialog} tabIndex={-1}>
      <strong>{saving ? "Entwürfe werden gesichert…" : "Beenden angehalten"}</strong>
      {error ? <><p role="alert">{error}</p><p>Die Entwürfe bleiben geöffnet.</p>
        <button type="button" className="button" onClick={() => { setError(""); }}>Weiterarbeiten</button>
        <button type="button" className="button button-primary" onClick={() => { void finish(); }}>Speichern erneut versuchen</button></> : null}
    </div>
  </div>;
}
