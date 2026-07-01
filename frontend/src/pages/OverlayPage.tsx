import { useEffect, useRef, useState } from "react";
import { Check, Copy, Eye, Loader2, Play, Send, Sparkles, Square, X } from "lucide-react";

import { api, askObserve, cancelAgent, streamAgentDispatch, streamObserve, stopObserve } from "../api";
import { isTauri, nativeInvoke, nativeListen } from "../native";
import type { AgentConfig, AgentDispatchEvent, AgentMode, ObserveChatEntry, OverlayTaskPayload } from "../types";

// AI-Cursor overlay (R1, reworked for Desktop-Agent v2). Rendered only in the
// transparent, always-on-top Tauri overlay window (toggled by a global hotkey / tray,
// or spawned pre-loaded via `overlay_dispatch_task` from the Notes Parallelmodus
// "An AI-Cursor übergeben" button). Two modes:
//   - Selbst-Steuerung: the bridge drives mouse/keyboard autonomously (streamAgentDispatch).
//   - Assistent: the bridge only describes the screen periodically and answers questions
//     (streamObserve / askObserve) — it never touches mouse/keyboard.
// Nothing runs until the user clicks "Starten" here, even if a task arrived pre-loaded — that
// click is the consent gesture, so neither mode is gated on `agent_bridge.enabled` (that flag
// only gates the web-mode fallback in ParallelResultsTab.tsx, which has no on-demand sidecar).
// Tauri manages the bridge/uitars sidecar itself (agent_bridge_ensure/_stop); if it can't start
// (e.g. Node.js missing), the resulting error offers a "Brief kopieren" fallback instead of a
// dead end. `agent_bridge.helper_enabled` remains a standalone toggle to disable just Assistent.

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

function ChatBubble({ entry }: { entry: ObserveChatEntry }) {
  return <div className={`overlay-chat-bubble overlay-chat-bubble--${entry.role}`}>{entry.text}</div>;
}

export function OverlayPage() {
  const [mode, setMode] = useState<AgentMode>("self_managing");
  const [taskText, setTaskText] = useState("");
  const [goalLabel, setGoalLabel] = useState("");
  const [config, setConfig] = useState<AgentConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [briefCopied, setBriefCopied] = useState(false);

  // Selbst-Steuerung state.
  const [runActive, setRunActive] = useState(false);
  const [events, setEvents] = useState<AgentDispatchEvent[]>([]);
  const runIdRef = useRef<string | null>(null);

  // Assistent state.
  const [observeActive, setObserveActive] = useState(false);
  const [lastObservation, setLastObservation] = useState<string | null>(null);
  const [chat, setChat] = useState<ObserveChatEntry[]>([]);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const sessionIdRef = useRef<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const bridgeBaseRef = useRef<string | null>(null);
  const variantIdRef = useRef<string | null>(null);
  const logEndRef = useRef<HTMLDivElement>(null);

  const sessionActive = runActive || observeActive;

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

  // Pre-load a task compiled elsewhere (e.g. "An AI-Cursor übergeben" in the Notes
  // Parallelmodus) — the overlay only shows it; nothing runs until "Starten".
  useEffect(() => {
    if (!isTauri()) return;
    let unlisten: (() => void) | undefined;
    nativeListen<OverlayTaskPayload>("overlay://task", (payload) => {
      setTaskText(payload.task ?? "");
      setGoalLabel(payload.goal ?? "");
      setMode(payload.mode === "helper" ? "helper" : "self_managing");
      variantIdRef.current = payload.variantId ?? null;
      setCollapsed(false);
    }).then((fn) => { unlisten = fn; });
    return () => unlisten?.();
  }, []);

  // Escape hides the overlay — unless an Assistent session is watching the screen,
  // in which case it only collapses to a small "watching" pill (privacy: stopping
  // the observation should always be an explicit, visible action).
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      if (observeActive) {
        setCollapsed((c) => !c);
      } else {
        void hide();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [observeActive]);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ block: "end" });
  }, [events, chat]);

  async function hide() {
    if (isTauri()) await nativeInvoke("overlay_hide").catch(() => {});
  }

  function closeOrCollapse() {
    if (observeActive) {
      setCollapsed(true);
      return;
    }
    void hide();
  }

  async function handleStart() {
    const text = taskText.trim();
    if (!text || starting || sessionActive) return;
    setError(null);
    setStarting(true);
    let port: number;
    try {
      port = await nativeInvoke<number>("agent_bridge_ensure", {
        vlmBaseUrl: config?.vlm_base_url || undefined,
        vlmModel: config?.vlm_model || undefined,
        helperVlmModel: config?.helper_vlm_model || undefined,
        observeIntervalSeconds: config?.observe_interval_seconds || undefined,
        observeContextSize: config?.observe_context_size || undefined,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setStarting(false);
      return;
    }
    setStarting(false);

    const controller = new AbortController();
    abortRef.current = controller;

    if (mode === "self_managing") {
      bridgeBaseRef.current = `http://127.0.0.1:${port}`;
      setEvents([]);
      setRunActive(true);
      try {
        await streamAgentDispatch(
          {
            task: text,
            variant_id: variantIdRef.current ?? undefined,
            bridge_url: `http://127.0.0.1:${port}/run`,
          },
          (event) => {
            if (event.runId) runIdRef.current = event.runId;
            setEvents((prev) => [...prev, event]);
          },
          controller.signal,
        );
      } catch (err) {
        if (!controller.signal.aborted) {
          setEvents((prev) => [
            ...prev,
            { status: "error", error: err instanceof Error ? err.message : String(err) },
          ]);
        }
      } finally {
        setRunActive(false);
        abortRef.current = null;
        runIdRef.current = null;
      }
    } else {
      bridgeBaseRef.current = `http://127.0.0.1:${port}`;
      setChat([]);
      setLastObservation(null);
      setObserveActive(true);
      try {
        await streamObserve(
          { primer: text, bridge_base: bridgeBaseRef.current },
          (event) => {
            if (event.status === "started" && event.sessionId) sessionIdRef.current = event.sessionId;
            if (event.status === "observation") setLastObservation(event.value ?? null);
            if (event.status === "error") setError(event.error ?? null);
          },
          controller.signal,
        );
      } catch (err) {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : String(err));
        }
      } finally {
        setObserveActive(false);
        abortRef.current = null;
        sessionIdRef.current = null;
      }
    }
  }

  /** Stop at any time: abort the local stream immediately, ask the bridge to stop
   * gracefully, then hard-kill the sidecar as the guaranteed fallback. */
  async function handleStop() {
    abortRef.current?.abort();
    const bridgeBase = bridgeBaseRef.current ?? undefined;
    try {
      if (mode === "self_managing" && runIdRef.current) {
        await cancelAgent({ run_id: runIdRef.current, bridge_base: bridgeBase });
      } else if (mode === "helper" && sessionIdRef.current) {
        await stopObserve({ session_id: sessionIdRef.current, bridge_base: bridgeBase });
      }
    } catch {
      /* best-effort — the hard-kill below is the guaranteed fallback */
    }
    if (isTauri()) {
      await nativeInvoke("agent_bridge_stop").catch(() => {});
    }
  }

  /** Kanal-A fallback: if the bridge sidecar can't start (e.g. Node.js missing), let the
   * user copy the already-compiled brief instead of hitting a dead end. */
  function copyBrief() {
    const text = taskText.trim();
    if (!text) return;
    void navigator.clipboard?.writeText(text).then(() => {
      setBriefCopied(true);
      window.setTimeout(() => setBriefCopied(false), 1500);
    });
  }

  async function handleAsk() {
    const text = question.trim();
    if (!text || asking || !sessionIdRef.current) return;
    setAsking(true);
    setChat((prev) => [...prev, { role: "user", text }]);
    setQuestion("");
    try {
      const res = await askObserve({
        session_id: sessionIdRef.current,
        question: text,
        bridge_base: bridgeBaseRef.current ?? undefined,
      });
      setChat((prev) => [...prev, { role: "assistant", text: res.answer || res.error || "(keine Antwort)" }]);
    } catch (err) {
      setChat((prev) => [...prev, { role: "assistant", text: err instanceof Error ? err.message : String(err) }]);
    } finally {
      setAsking(false);
    }
  }

  if (collapsed) {
    return (
      <div className="overlay-root overlay-root--collapsed" onClick={() => setCollapsed(false)}>
        <span className="overlay-watching-dot" />
        <span>Assistent beobachtet deinen Bildschirm…</span>
      </div>
    );
  }

  const blockedReason =
    !config
      ? null
      : mode === "helper" && !config.helper_enabled
        ? "Assistent-Modus ist per Konfiguration deaktiviert (`agent_bridge.helper_enabled`)."
        : null;

  return (
    <div className="overlay-root">
      <header className="overlay-head" data-tauri-drag-region>
        <Sparkles size={15} />
        <strong>AI-Cursor</strong>
        <button className="overlay-close" type="button" onClick={closeOrCollapse} aria-label="Ausblenden">
          <X size={15} />
        </button>
      </header>

      <div className="overlay-mode-toggle" role="tablist" aria-label="Modus">
        <button
          type="button"
          className={`overlay-mode-toggle__item ${mode === "self_managing" ? "overlay-mode-toggle__item--active" : ""}`}
          disabled={sessionActive}
          onClick={() => setMode("self_managing")}
        >
          Selbst-Steuerung
        </button>
        <button
          type="button"
          className={`overlay-mode-toggle__item ${mode === "helper" ? "overlay-mode-toggle__item--active" : ""}`}
          disabled={sessionActive}
          onClick={() => setMode("helper")}
        >
          Assistent
        </button>
      </div>

      {blockedReason ? <p className="overlay-hint">{blockedReason}</p> : null}
      {error ? (
        <div className="overlay-hint overlay-hint--error">
          <p>{error}</p>
          {taskText.trim() ? (
            <button className="button button-compact" type="button" onClick={copyBrief}>
              {briefCopied ? <Check size={13} /> : <Copy size={13} />}
              {briefCopied ? "Kopiert" : "Brief kopieren"}
            </button>
          ) : null}
        </div>
      ) : null}

      {observeActive ? (
        <div className="overlay-watching">
          <Eye size={13} className="overlay-watching-dot" />
          <span>Ich sehe deinen Bildschirm{lastObservation ? ":" : "…"}</span>
          {lastObservation ? <p className="overlay-watching-text">{lastObservation}</p> : null}
        </div>
      ) : null}

      {!blockedReason ? (
        <div className="overlay-input">
          {goalLabel && !sessionActive ? <p className="overlay-goal">Ziel: {goalLabel}</p> : null}
          <textarea
            autoFocus
            rows={3}
            placeholder={
              mode === "self_managing"
                ? "Was soll der Desktop-Agent tun? (Strg+Enter)"
                : "Woran arbeitest du? (Kontext für den Assistenten, Strg+Enter)"
            }
            value={taskText}
            disabled={sessionActive}
            onChange={(event) => setTaskText(event.target.value)}
            onKeyDown={(event) => {
              if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
                event.preventDefault();
                void handleStart();
              }
            }}
          />
          {!sessionActive ? (
            <button
              className="button button-primary button-compact"
              type="button"
              disabled={!taskText.trim() || starting}
              onClick={() => void handleStart()}
            >
              {starting ? <Loader2 size={15} className="overlay-spin" /> : <Play size={15} />}
              {starting ? "Startet…" : "Starten"}
            </button>
          ) : (
            <button className="button button-compact overlay-stop" type="button" onClick={() => void handleStop()}>
              <Square size={15} />
              Stoppen
            </button>
          )}
        </div>
      ) : null}

      {mode === "self_managing" ? (
        events.length ? (
          <ul className="overlay-log">
            {events.map((event, index) => (
              <EventLine key={index} event={event} />
            ))}
            <div ref={logEndRef} />
          </ul>
        ) : (
          <p className="overlay-muted">Beschreibe eine Aufgabe und starte den Desktop-Agenten.</p>
        )
      ) : (
        <>
          {chat.length ? (
            <div className="overlay-chat">
              {chat.map((entry, index) => (
                <ChatBubble key={index} entry={entry} />
              ))}
              <div ref={logEndRef} />
            </div>
          ) : (
            <p className="overlay-muted">
              {observeActive ? "Stell jederzeit eine Frage zu deinem Bildschirm." : "Starte die Beobachtung, um Fragen zu stellen."}
            </p>
          )}
          {observeActive ? (
            <div className="overlay-chat-input">
              <input
                type="text"
                placeholder="Frage stellen…"
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    void handleAsk();
                  }
                }}
              />
              <button
                className="button button-compact button-primary"
                type="button"
                disabled={!question.trim() || asking}
                onClick={() => void handleAsk()}
              >
                {asking ? <Loader2 size={13} className="overlay-spin" /> : <Send size={13} />}
              </button>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
