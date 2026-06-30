import { useEffect, useRef, useState } from "react";
import { Loader2, Send, Sparkles, X } from "lucide-react";

import { api, streamAgentDispatch } from "../api";
import { isTauri, nativeInvoke } from "../native";
import type { AgentConfig, AgentDispatchEvent } from "../types";

// AI-Cursor overlay (R1). Rendered only in the transparent, always-on-top Tauri
// overlay window (toggled by a global hotkey / tray). It reuses the existing
// UI-TARS hand-off: getAgentConfig + streamAgentDispatch against the same backend
// sidecar — PaperKG stays "the brain", no new VLM plumbing here.

/** One streamed dispatch event, rendered compactly. */
function EventLine({ event }: { event: AgentDispatchEvent }) {
  const text =
    event.error ??
    (typeof event.value === "string" ? event.value : event.value != null ? JSON.stringify(event.value) : "");
  return (
    <li className={`overlay-log-line overlay-log-line--${event.status}`}>
      <span className="overlay-log-tag">{event.from ?? event.status}</span>
      {text ? <span className="overlay-log-text">{text}</span> : null}
    </li>
  );
}

export function OverlayPage() {
  const [task, setTask] = useState("");
  const [events, setEvents] = useState<AgentDispatchEvent[]>([]);
  const [dispatching, setDispatching] = useState(false);
  const [config, setConfig] = useState<AgentConfig | null>(null);
  const logEndRef = useRef<HTMLDivElement>(null);

  // Transparent window: drop the app's opaque background (html + body) for this
  // window only, so the desktop shows through behind the floating card.
  useEffect(() => {
    document.documentElement.classList.add("overlay-mode");
    document.body.classList.add("overlay-mode");
    return () => {
      document.documentElement.classList.remove("overlay-mode");
      document.body.classList.remove("overlay-mode");
    };
  }, []);

  useEffect(() => {
    let alive = true;
    api.getAgentConfig().then((cfg) => { if (alive) setConfig(cfg); }).catch(() => {});
    return () => { alive = false; };
  }, []);

  // Escape hides the overlay (native window stays alive in the background).
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") void hide();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ block: "end" });
  }, [events]);

  async function hide() {
    if (isTauri()) await nativeInvoke("overlay_hide").catch(() => {});
  }

  async function dispatch() {
    const text = task.trim();
    if (!text || dispatching) return;
    setDispatching(true);
    setEvents([]);
    try {
      await streamAgentDispatch({ task: text }, (event) => {
        setEvents((prev) => [...prev, event]);
      });
    } catch (error) {
      setEvents((prev) => [
        ...prev,
        { status: "error", error: error instanceof Error ? error.message : String(error) },
      ]);
    } finally {
      setDispatching(false);
    }
  }

  return (
    <div className="overlay-root">
      <header className="overlay-head" data-tauri-drag-region>
        <Sparkles size={15} />
        <strong>AI-Cursor</strong>
        <button className="overlay-close" type="button" onClick={() => void hide()} aria-label="Ausblenden">
          <X size={15} />
        </button>
      </header>

      {config && !config.enabled ? (
        <p className="overlay-hint">
          Desktop-Agent-Bridge ist aus. Aktiviere den <code>agent_bridge:</code>-Block in der
          <code>config.yaml</code>, um Aufgaben an UI-TARS zu übergeben.
        </p>
      ) : null}

      <div className="overlay-input">
        <textarea
          autoFocus
          rows={3}
          placeholder="Was soll der Desktop-Agent tun? (Strg+Enter)"
          value={task}
          onChange={(event) => setTask(event.target.value)}
          onKeyDown={(event) => {
            if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
              event.preventDefault();
              void dispatch();
            }
          }}
        />
        <button
          className="button button-primary button-compact"
          type="button"
          disabled={!task.trim() || dispatching}
          onClick={() => void dispatch()}
        >
          {dispatching ? <Loader2 size={15} className="overlay-spin" /> : <Send size={15} />}
          {dispatching ? "Läuft…" : "Senden"}
        </button>
      </div>

      {events.length ? (
        <ul className="overlay-log">
          {events.map((event, index) => (
            <EventLine key={index} event={event} />
          ))}
          <div ref={logEndRef} />
        </ul>
      ) : (
        <p className="overlay-muted">
          {config?.enabled
            ? `Bridge bereit (${config.vlm_model || config.type}).`
            : "Beschreibe eine Aufgabe und sende sie an den Desktop-Agenten."}
        </p>
      )}
    </div>
  );
}
