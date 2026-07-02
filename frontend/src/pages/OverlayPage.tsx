import { useEffect, useRef, useState } from "react";
import { ArrowRight, Check, Copy, Crop, EyeOff, Loader2, MapPin, Play, Sparkles, Square, X } from "lucide-react";

import {
  api,
  askCompanion,
  cancelAgent,
  getCompanionConfig,
  guideCompanion,
  startSelfDrive,
  stepSelfDrive,
  stopSelfDrive,
  streamAgentDispatch,
} from "../api";
import { isTauri, nativeInvoke, nativeListen } from "../native";
import type {
  AgentConfig,
  AgentDispatchEvent,
  AgentMode,
  CaptureResult,
  CompanionConfigInfo,
  CompanionStep,
  MonitorInfo,
  ObserveChatEntry,
  OverlayTaskPayload,
  SelfDriveAction,
  SelfDriveStepResult,
  SnipResultPayload,
} from "../types";

// AI-Cursor overlay (R1, reworked for the Desktop Companion R6). Rendered only in the
// transparent, always-on-top Tauri overlay window (toggled by a global hotkey / tray,
// or spawned pre-loaded via `overlay_dispatch_task` from the Notes Parallelmodus
// "An AI-Cursor übergeben" button). Two modes:
//   - Companion (default): screen-aware chat. Each question captures the primary monitor
//     natively (capture_screen — the card itself is excluded via WDA or hidden), sends it
//     to POST /companion/guide (vision through the project's own LLMRouter: local
//     LM Studio/Ollama VLMs or Claude via the `anthropic` provider) and shows the German
//     answer; returned steps glide the separate pointer ring to each spot
//     (pointer_show {space:"physical"}). "Bereich erklären" snips a frozen-frame region
//     (snip_start → snip://result) that is attached to the next question. The companion
//     only *sees* and *points* — it never drives mouse or keyboard.
//   - Selbst-Steuerung (legacy): the UI-TARS bridge drives the real mouse/keyboard
//     autonomously (streamAgentDispatch via bridge/uitars). Unchanged.
// Nothing runs until the user acts here — a question or "Starten" is the consent gesture.
// Screenshots only leave the machine when the user explicitly selects the `anthropic`
// provider in the picker; local providers are the default.

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

/** Human-readable one-liner for a planned Selbst-Steuerung action. */
function describeAction(action: SelfDriveAction): string {
  switch (action.type) {
    case "click":
      return `Klick auf (${Math.round(action.x ?? 0)}, ${Math.round(action.y ?? 0)})`;
    case "double_click":
      return `Doppelklick auf (${Math.round(action.x ?? 0)}, ${Math.round(action.y ?? 0)})`;
    case "move":
      return `Maus bewegen nach (${Math.round(action.x ?? 0)}, ${Math.round(action.y ?? 0)})`;
    case "type":
      return `Tippen: „${action.text ?? ""}“`;
    case "key":
      return `Taste: ${action.keys ?? ""}`;
    case "scroll":
      return `Scrollen (dx ${action.dx ?? 0}, dy ${action.dy ?? 0})`;
    case "wait":
      return "Warten / erneut beobachten";
    default:
      return action.type;
  }
}

function ChatBubble({ entry }: { entry: ObserveChatEntry }) {
  return (
    <div className={`overlay-chat-bubble overlay-chat-bubble--${entry.role}`}>
      {entry.text}
      {entry.sources?.length ? (
        <ul className="overlay-chat-sources">
          {entry.sources.map((source, index) => (
            <li key={index}>
              {source.type === "paper" ? (
                <span>📄 [{source.id}] {source.title}</span>
              ) : (
                <a href={source.url} target="_blank" rel="noreferrer">
                  🌐 {source.title || source.url}
                </a>
              )}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function OverlayPage() {
  const [mode, setMode] = useState<AgentMode>("helper");
  const [taskText, setTaskText] = useState("");
  const [goalLabel, setGoalLabel] = useState("");
  const [config, setConfig] = useState<AgentConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [briefCopied, setBriefCopied] = useState(false);

  // Selbst-Steuerung state (legacy UI-TARS path).
  const [runActive, setRunActive] = useState(false);
  const [events, setEvents] = useState<AgentDispatchEvent[]>([]);
  const runIdRef = useRef<string | null>(null);

  // Native Selbst-Steuerung (R7 skeleton): a per-action confirmation loop that drives
  // real mouse/keyboard through control.rs (enigo). Off by default; "native" toggles
  // between it and the legacy UI-TARS bridge inside the Selbst-Steuerung tab.
  const [selfDriveNative, setSelfDriveNative] = useState(
    () => localStorage.getItem("sciencekg.selfdrive.native") === "1",
  );
  const [sdSessionId, setSdSessionId] = useState<string | null>(null);
  const [sdArmed, setSdArmed] = useState(false);
  const [sdBusy, setSdBusy] = useState(false);
  const [sdThought, setSdThought] = useState("");
  const [sdAction, setSdAction] = useState<SelfDriveAction | null>(null);
  const [sdStep, setSdStep] = useState<{ step: number; max: number } | null>(null);

  // Companion state.
  const [companionCfg, setCompanionCfg] = useState<CompanionConfigInfo | null>(null);
  const [companionProvider, setCompanionProvider] = useState<string>(
    () => localStorage.getItem("sciencekg.companion.provider") ?? "",
  );
  const [companionModel, setCompanionModel] = useState<string>(
    () => localStorage.getItem("sciencekg.companion.model") ?? "",
  );
  const [discovering, setDiscovering] = useState(false);
  const [chat, setChat] = useState<ObserveChatEntry[]>([]);
  const [question, setQuestion] = useState("");
  const [pointing, setPointing] = useState(false);
  const [steps, setSteps] = useState<CompanionStep[]>([]);
  const [stepIndex, setStepIndex] = useState(0);
  const [snip, setSnip] = useState<SnipResultPayload | null>(null);
  // Multi-monitor: "" = Auto (monitor under the cursor), otherwise an xcap monitor id.
  const [monitors, setMonitors] = useState<MonitorInfo[]>([]);
  const [companionMonitor, setCompanionMonitor] = useState<string>(
    () => localStorage.getItem("sciencekg.companion.monitor") ?? "",
  );
  const [activeMonitorName, setActiveMonitorName] = useState<string>("");
  // The capture a pointing answer belongs to — its monitor origin/size place the ring
  // window on the right screen (steps stay monitor-relative physical pixels).
  const captureInfoRef = useRef<CaptureResult | null>(null);
  // Quellen-Modus: ground answers in local papers and/or a web search (off = privacy
  // default; the web toggle sends the question text to the configured search engine).
  const [usePapers, setUsePapers] = useState(
    () => localStorage.getItem("sciencekg.companion.papers") === "1",
  );
  const [useWeb, setUseWeb] = useState(() => localStorage.getItem("sciencekg.companion.web") === "1");

  const abortRef = useRef<AbortController | null>(null);
  const bridgeBaseRef = useRef<string | null>(null);
  const variantIdRef = useRef<string | null>(null);
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
    getCompanionConfig()
      .then((cfg) => {
        if (!alive) return;
        setCompanionCfg(cfg);
        setCompanionProvider((prev) => prev || cfg.provider);
        setCompanionModel((prev) => prev || cfg.model);
      })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    companionProvider
      ? localStorage.setItem("sciencekg.companion.provider", companionProvider)
      : localStorage.removeItem("sciencekg.companion.provider");
  }, [companionProvider]);

  // Live model discovery per provider (LM Studio /models, Ollama /api/tags, …) —
  // /companion/config only serves the cached list so the overlay opens instantly.
  // Best-effort: on failure the cached options stay usable.
  useEffect(() => {
    if (!companionProvider) return;
    let alive = true;
    setDiscovering(true);
    api
      .discoverModels(companionProvider)
      .then((res) => {
        if (!alive || !res.models.length) return;
        setCompanionCfg((prev) => {
          if (!prev) return prev;
          const known = prev.providers.some((item) => item.name === res.provider);
          const providers = known
            ? prev.providers.map((item) =>
                item.name === res.provider
                  ? { ...item, models: Array.from(new Set([...item.models, ...res.models])) }
                  : item,
              )
            : [...prev.providers, { name: res.provider, models: res.models }];
          return { ...prev, providers };
        });
      })
      .catch(() => {})
      .finally(() => {
        if (alive) setDiscovering(false);
      });
    return () => {
      alive = false;
    };
  }, [companionProvider]);

  useEffect(() => {
    companionModel
      ? localStorage.setItem("sciencekg.companion.model", companionModel)
      : localStorage.removeItem("sciencekg.companion.model");
  }, [companionModel]);

  useEffect(() => {
    companionMonitor
      ? localStorage.setItem("sciencekg.companion.monitor", companionMonitor)
      : localStorage.removeItem("sciencekg.companion.monitor");
  }, [companionMonitor]);

  useEffect(() => {
    usePapers
      ? localStorage.setItem("sciencekg.companion.papers", "1")
      : localStorage.removeItem("sciencekg.companion.papers");
  }, [usePapers]);

  useEffect(() => {
    useWeb
      ? localStorage.setItem("sciencekg.companion.web", "1")
      : localStorage.removeItem("sciencekg.companion.web");
  }, [useWeb]);

  // Monitor list for the picker — loaded per mount; xcap ids are only stable for the
  // current session, so a persisted pick that no longer exists falls back to Auto.
  useEffect(() => {
    if (!isTauri()) return;
    nativeInvoke<MonitorInfo[]>("list_monitors")
      .then((list) => {
        setMonitors(list);
        setCompanionMonitor((prev) =>
          prev && !list.some((item) => String(item.id) === prev) ? "" : prev,
        );
      })
      .catch(() => {});
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
    }).then((fn) => { unlisten = fn; });
    return () => unlisten?.();
  }, []);

  // A snipped region arrives from the snip window (tray or "Bereich erklären" button)
  // and is attached as a chip to the next companion question.
  useEffect(() => {
    if (!isTauri()) return;
    let unlisten: (() => void) | undefined;
    nativeListen<SnipResultPayload>("snip://result", (payload) => {
      setSnip(payload);
      setMode("helper");
    }).then((fn) => { unlisten = fn; });
    return () => unlisten?.();
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") void hide();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ block: "end" });
  }, [events, chat]);

  useEffect(() => {
    selfDriveNative
      ? localStorage.setItem("sciencekg.selfdrive.native", "1")
      : localStorage.removeItem("sciencekg.selfdrive.native");
  }, [selfDriveNative]);

  // Emergency stop (Ctrl+Shift+Q, emitted from Rust): disarm + drop the session so no
  // further action can run even if the user hit the global hotkey mid-loop.
  useEffect(() => {
    if (!isTauri()) return;
    let unlisten: (() => void) | undefined;
    nativeListen("selfdrive://emergency-stop", () => {
      setSdArmed(false);
      setSdAction(null);
      setSdBusy(false);
      setError("Not-Aus ausgelöst — Selbst-Steuerung gestoppt.");
      if (sdSessionId) void stopSelfDrive({ session_id: sdSessionId }).catch(() => {});
      setSdSessionId(null);
    }).then((fn) => {
      unlisten = fn;
    });
    return () => unlisten?.();
  }, [sdSessionId]);

  async function hide() {
    if (isTauri()) await nativeInvoke("overlay_hide").catch(() => {});
  }

  /** Ensure the bridge sidecar is running and return its loopback base URL (or null on
   * failure, error surfaced). Idempotent — `agent_bridge_ensure` reuses a live child — so
   * it's safe to call before every action instead of gating everything behind one "Starten". */
  async function ensureBridge(): Promise<string | null> {
    try {
      const port = await nativeInvoke<number>("agent_bridge_ensure", {
        vlmBaseUrl: config?.vlm_base_url || undefined,
        vlmModel: config?.vlm_model || undefined,
        helperVlmModel: config?.helper_vlm_model || undefined,
        observeIntervalSeconds: config?.observe_interval_seconds || undefined,
        observeContextSize: config?.observe_context_size || undefined,
      });
      bridgeBaseRef.current = `http://127.0.0.1:${port}`;
      return bridgeBaseRef.current;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return null;
    }
  }

  /** Selbst-Steuerung: hand the task to the bridge, which drives the real mouse/keyboard. */
  async function handleStart() {
    const text = taskText.trim();
    if (!text || starting || runActive) return;
    setError(null);
    setStarting(true);
    const base = await ensureBridge();
    setStarting(false);
    if (!base) return;

    const controller = new AbortController();
    abortRef.current = controller;
    setEvents([]);
    setRunActive(true);
    // No separate "AI-only" cursor exists — Selbst-Steuerung moves the user's real OS
    // cursor, so this full-screen border is the visible signal that a takeover is live.
    if (isTauri()) await nativeInvoke("control_border_show").catch(() => {});
    try {
      await streamAgentDispatch(
        { task: text, variant_id: variantIdRef.current ?? undefined, bridge_url: `${base}/run` },
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
      if (isTauri()) await nativeInvoke("control_border_hide").catch(() => {});
    }
  }

  /** Stop a Selbst-Steuerung run at any time: abort the local stream immediately, ask the
   * bridge to stop gracefully, then hard-kill the sidecar as the guaranteed fallback. */
  async function handleStop() {
    abortRef.current?.abort();
    try {
      if (runIdRef.current) {
        await cancelAgent({ run_id: runIdRef.current, bridge_base: bridgeBaseRef.current ?? undefined });
      }
    } catch {
      /* best-effort — the hard-kill below is the guaranteed fallback */
    }
    if (isTauri()) {
      await nativeInvoke("agent_bridge_stop").catch(() => {});
      await nativeInvoke("pointer_hide").catch(() => {});
    }
  }

  /** Apply one planning result: show the thought + action, or finish the session. */
  function sdApplyStep(res: SelfDriveStepResult) {
    if (res.error) {
      setError(res.error);
      setSdAction(null);
      return;
    }
    setSdThought(res.thought ?? "");
    setSdStep(res.step != null ? { step: res.step, max: res.max_steps ?? 0 } : null);
    if (res.done || !res.action || res.action.type === "done" || res.action.type === "fail") {
      setSdAction(null);
      setChat((prev) => [
        ...prev,
        {
          role: "assistant",
          text: `Selbst-Steuerung beendet (${res.action?.type ?? "done"}). ${res.thought ?? ""}`.trim(),
        },
      ]);
      void sdStop();
      return;
    }
    setSdAction(res.action);
  }

  /** Capture the target monitor and ask the backend for the next action. */
  async function sdPlan(sessionId: string) {
    setSdBusy(true);
    setError(null);
    try {
      const capture = await nativeInvoke<CaptureResult>("capture_screen", {
        monitor: companionMonitor ? Number(companionMonitor) : null,
      });
      captureInfoRef.current = capture;
      sdApplyStep(await stepSelfDrive({ session_id: sessionId, image_base64: capture.image_base64 }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSdBusy(false);
    }
  }

  /** Native Selbst-Steuerung: open a session, arm the shell (shows the control border),
   * plan the first action. Nothing runs until the user confirms each action. */
  async function sdStart() {
    const goal = taskText.trim();
    if (!goal || sdBusy || !isTauri()) return;
    setError(null);
    const res = await startSelfDrive({
      goal,
      monitor: companionMonitor ? Number(companionMonitor) : null,
      provider: companionProvider || undefined,
      model: companionModel || undefined,
    });
    if (res.error || !res.session_id) {
      setError(res.error ?? "Selbst-Steuerung konnte nicht gestartet werden.");
      return;
    }
    setSdSessionId(res.session_id);
    await nativeInvoke("self_drive_arm").catch(() => {});
    setSdArmed(true);
    await sdPlan(res.session_id);
  }

  /** Convert one planned action (original-screenshot px) to physical desktop pixels and
   * run it through control.rs, then plan the next step. */
  async function sdExecute() {
    if (!sdAction || !sdSessionId || sdBusy) return;
    const capture = captureInfoRef.current;
    const toPhysical = (value: number, axis: "x" | "y") =>
      (axis === "x" ? capture?.origin_x ?? 0 : capture?.origin_y ?? 0) + value;
    setSdBusy(true);
    try {
      const a = sdAction;
      if ((a.type === "click" || a.type === "double_click") && a.x != null && a.y != null) {
        await nativeInvoke("control_click", {
          x: toPhysical(a.x, "x"),
          y: toPhysical(a.y, "y"),
          button: "left",
          double: a.type === "double_click",
        });
      } else if (a.type === "move" && a.x != null && a.y != null) {
        await nativeInvoke("control_move", { x: toPhysical(a.x, "x"), y: toPhysical(a.y, "y") });
      } else if (a.type === "type") {
        await nativeInvoke("control_type", { text: a.text ?? "" });
      } else if (a.type === "key") {
        await nativeInvoke("control_key", { combo: a.keys ?? "" });
      } else if (a.type === "scroll") {
        await nativeInvoke("control_scroll", { dx: a.dx ?? 0, dy: a.dy ?? 0 });
      }
      // "wait" falls through — just observe again.
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSdBusy(false);
      return;
    }
    setSdBusy(false);
    await sdPlan(sdSessionId);
  }

  /** Skip the current action without executing it (re-observe and re-plan). */
  async function sdSkip() {
    if (!sdSessionId || sdBusy) return;
    await sdPlan(sdSessionId);
  }

  /** Stop native Selbst-Steuerung: disarm the shell (hides the border), drop the session. */
  async function sdStop() {
    const sessionId = sdSessionId;
    setSdArmed(false);
    setSdAction(null);
    setSdSessionId(null);
    if (isTauri()) await nativeInvoke("self_drive_disarm").catch(() => {});
    if (sessionId) await stopSelfDrive({ session_id: sessionId }).catch(() => {});
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

  /** Glide the pointer ring to one guidance step (coordinates arrive in monitor-
   * relative physical pixels from /companion/guide — hence space:"physical"; the
   * capture's monitor origin/size move the ring window onto the captured screen). */
  async function showStep(list: CompanionStep[], index: number) {
    const step = list[index];
    if (!step || !isTauri()) return;
    const counter = list.length > 1 ? `${index + 1}/${list.length}` : "";
    const label = [counter, step.label].filter(Boolean).join(" · ");
    const capture = captureInfoRef.current;
    await nativeInvoke("pointer_show", {
      x: step.x,
      y: step.y,
      label: label || null,
      space: "physical",
      originX: capture?.origin_x ?? null,
      originY: capture?.origin_y ?? null,
      monitorWidth: capture?.width ?? null,
      monitorHeight: capture?.height ?? null,
    }).catch(() => {});
  }

  function nextStep() {
    const next = stepIndex + 1;
    if (next >= steps.length) return;
    setStepIndex(next);
    void showStep(steps, next);
  }

  async function hidePointer() {
    setSteps([]);
    setStepIndex(0);
    if (isTauri()) await nativeInvoke("pointer_hide").catch(() => {});
  }

  /** Start a "Bereich erklären" region selection. Rust hides this card, shows the
   * frozen-frame snip window and pushes the crop back via `snip://result`. */
  async function startSnip() {
    if (!isTauri()) {
      setError("„Bereich erklären“ braucht die native Desktop-App (Bildschirmzugriff).");
      return;
    }
    setError(null);
    await nativeInvoke("snip_start", {
      monitor: companionMonitor ? Number(companionMonitor) : null,
    }).catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }

  /** Primary Companion action: screenshot → /companion/guide (answer + optional pointing
   * steps) — or /companion/ask when a snipped region is attached. One vision round trip;
   * the model decides whether pointing helps. */
  async function handleCompanionAsk() {
    const text = question.trim();
    if (!text || pointing) return;
    setPointing(true);
    setError(null);
    // History excludes the question being asked — the backend appends it itself.
    const history = chat.slice(-16).map((entry) => ({ role: entry.role, content: entry.text }));
    setChat((prev) => [...prev, { role: "user", text }]);
    setQuestion("");
    setSteps([]);
    setStepIndex(0);
    if (isTauri()) await nativeInvoke("pointer_hide").catch(() => {});
    const provider = companionProvider || undefined;
    const model = companionModel || undefined;
    try {
      if (snip) {
        // Region question: the frozen crop is the image; no pointing (its coordinates
        // wouldn't be meaningful on the live screen).
        const image = snip.image_base64;
        setSnip(null);
        const res = await askCompanion({
          question: text,
          image_base64: image,
          history,
          region: true,
          provider,
          model,
          use_papers: usePapers,
          use_web: useWeb,
        });
        setChat((prev) => [
          ...prev,
          {
            role: "assistant",
            text: res.error ? `Fehler: ${res.error}` : res.answer || "Dazu kann ich nichts sagen.",
            sources: res.sources,
          },
        ]);
        return;
      }
      if (!isTauri()) {
        setChat((prev) => [
          ...prev,
          { role: "assistant", text: "Der Desktop-Companion braucht die native Desktop-App — im Browser kann ich deinen Bildschirm nicht sehen." },
        ]);
        return;
      }
      const capture = await nativeInvoke<CaptureResult>("capture_screen", {
        monitor: companionMonitor ? Number(companionMonitor) : null,
      });
      captureInfoRef.current = capture;
      setActiveMonitorName(capture.monitor_name || "");
      const res = await guideCompanion({
        question: text,
        image_base64: capture.image_base64,
        history,
        provider,
        model,
        use_papers: usePapers,
        use_web: useWeb,
      });
      if (res.error) {
        setChat((prev) => [...prev, { role: "assistant", text: `Fehler: ${res.error}` }]);
        return;
      }
      setChat((prev) => [
        ...prev,
        { role: "assistant", text: res.answer || "Dazu kann ich nichts sagen.", sources: res.sources },
      ]);
      if (res.steps?.length) {
        setSteps(res.steps);
        setStepIndex(0);
        await showStep(res.steps, 0);
      }
    } catch (err) {
      setChat((prev) => [...prev, { role: "assistant", text: err instanceof Error ? err.message : String(err) }]);
    } finally {
      setPointing(false);
    }
  }

  const providerOptions = companionCfg?.providers ?? [];
  const activeProviderModels = providerOptions.find((item) => item.name === companionProvider)?.models ?? [];
  const modelOptions = Array.from(new Set([...activeProviderModels, ...(companionModel ? [companionModel] : [])])).filter(Boolean);
  const monitorLabel = (item: MonitorInfo) =>
    `${item.name || `Monitor ${item.id}`}${item.is_primary ? " (primär)" : ""}`;
  const selectedMonitor = monitors.find((item) => String(item.id) === companionMonitor);
  const monitorHint = selectedMonitor
    ? `Bildschirm: ${monitorLabel(selectedMonitor)}`
    : activeMonitorName
      ? `Zuletzt gesendet: ${activeMonitorName} (Auto – Cursor)`
      : "";

  return (
    <div className="overlay-root">
      <header className="overlay-head" data-tauri-drag-region>
        <Sparkles size={15} />
        <strong>AI-Cursor</strong>
        <button className="overlay-close" type="button" onClick={() => void hide()} aria-label="Ausblenden">
          <X size={15} />
        </button>
      </header>

      <div className="overlay-mode-toggle" role="tablist" aria-label="Modus">
        <button
          type="button"
          className={`overlay-mode-toggle__item ${mode === "helper" ? "overlay-mode-toggle__item--active" : ""}`}
          disabled={runActive}
          onClick={() => setMode("helper")}
        >
          Companion
        </button>
        <button
          type="button"
          className={`overlay-mode-toggle__item ${mode === "self_managing" ? "overlay-mode-toggle__item--active" : ""}`}
          disabled={runActive || pointing}
          onClick={() => setMode("self_managing")}
        >
          Selbst-Steuerung
        </button>
      </div>

      {mode === "self_managing" && config ? (
        <p className="overlay-model-hint">
          Modell: <strong>{config.vlm_model}</strong> (muss UI-TARS-kompatibel sein)
        </p>
      ) : null}

      {mode === "helper" ? (
        <div className="overlay-companion-pickers">
          <select
            aria-label="Provider"
            value={companionProvider}
            disabled={pointing}
            onChange={(event) => {
              setCompanionProvider(event.target.value);
              setCompanionModel("");
            }}
          >
            {providerOptions.map((item) => (
              <option key={item.name} value={item.name}>
                {item.name}
              </option>
            ))}
            {companionProvider && !providerOptions.some((item) => item.name === companionProvider) ? (
              <option value={companionProvider}>{companionProvider}</option>
            ) : null}
          </select>
          <select
            aria-label="Modell"
            value={companionModel}
            disabled={pointing}
            onChange={(event) => setCompanionModel(event.target.value)}
          >
            <option value="">Standardmodell</option>
            {modelOptions.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
          {monitors.length > 1 ? (
            <select
              aria-label="Bildschirm"
              value={companionMonitor}
              disabled={pointing}
              onChange={(event) => setCompanionMonitor(event.target.value)}
            >
              <option value="">Auto (Cursor)</option>
              {monitors.map((item) => (
                <option key={item.id} value={String(item.id)}>
                  {monitorLabel(item)}
                </option>
              ))}
            </select>
          ) : null}
          {discovering ? <Loader2 size={13} className="overlay-spin overlay-picker-spin" /> : null}
        </div>
      ) : null}
      {mode === "helper" && monitors.length > 1 && monitorHint ? (
        <p className="overlay-model-hint">{monitorHint}</p>
      ) : null}
      {mode === "helper" && companionProvider === "anthropic" ? (
        <p className="overlay-model-hint">Screenshots werden zur Beantwortung an Anthropic gesendet.</p>
      ) : null}

      {error ? (
        <div className="overlay-hint overlay-hint--error">
          <p>{error}</p>
          {mode === "self_managing" && taskText.trim() ? (
            <button className="button button-compact" type="button" onClick={copyBrief}>
              {briefCopied ? <Check size={13} /> : <Copy size={13} />}
              {briefCopied ? "Kopiert" : "Brief kopieren"}
            </button>
          ) : null}
        </div>
      ) : null}

      {mode === "self_managing" ? (
        <>
          <div className="overlay-mode-toggle overlay-mode-toggle--sub" role="tablist" aria-label="Selbst-Steuerung-Variante">
            <button
              type="button"
              className={`overlay-mode-toggle__item ${selfDriveNative ? "overlay-mode-toggle__item--active" : ""}`}
              disabled={runActive || !!sdSessionId}
              onClick={() => setSelfDriveNative(true)}
            >
              Nativ (experimentell)
            </button>
            <button
              type="button"
              className={`overlay-mode-toggle__item ${!selfDriveNative ? "overlay-mode-toggle__item--active" : ""}`}
              disabled={runActive || !!sdSessionId}
              onClick={() => setSelfDriveNative(false)}
            >
              UI-TARS-Bridge (Legacy)
            </button>
          </div>

          {selfDriveNative ? (
            <>
              <div className="overlay-input">
                {sdArmed ? (
                  <p className="overlay-model-hint overlay-selfdrive-armed">
                    ● Aktiv — jede Aktion wird einzeln bestätigt. Not-Aus: Strg+Shift+Q
                  </p>
                ) : (
                  <p className="overlay-model-hint">
                    Die KI plant Aktionen; du bestätigst jede einzeln. Sie bewegt deine echte Maus/Tastatur.
                  </p>
                )}
                <textarea
                  autoFocus
                  rows={3}
                  placeholder="Was soll die KI auf deinem Rechner tun? (Strg+Enter)"
                  value={taskText}
                  disabled={!!sdSessionId}
                  onChange={(event) => setTaskText(event.target.value)}
                  onKeyDown={(event) => {
                    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
                      event.preventDefault();
                      void sdStart();
                    }
                  }}
                />
                {!sdSessionId ? (
                  <button
                    className="button button-primary button-compact"
                    type="button"
                    disabled={!taskText.trim() || sdBusy || !isTauri()}
                    onClick={() => void sdStart()}
                  >
                    {sdBusy ? <Loader2 size={15} className="overlay-spin" /> : <Play size={15} />}
                    {sdBusy ? "Startet…" : "Starten"}
                  </button>
                ) : (
                  <button className="button button-compact overlay-stop" type="button" onClick={() => void sdStop()}>
                    <Square size={15} />
                    Stoppen
                  </button>
                )}
              </div>

              {sdSessionId ? (
                <div className="overlay-selfdrive-plan">
                  {sdStep ? (
                    <p className="overlay-muted">
                      Schritt {sdStep.step}/{sdStep.max}
                    </p>
                  ) : null}
                  {sdThought ? <p className="overlay-selfdrive-thought">{sdThought}</p> : null}
                  {sdBusy ? (
                    <p className="overlay-muted">
                      <Loader2 size={13} className="overlay-spin" /> Beobachte & plane…
                    </p>
                  ) : sdAction ? (
                    <>
                      <p className="overlay-selfdrive-action">
                        Nächste Aktion: <strong>{describeAction(sdAction)}</strong>
                      </p>
                      <div className="overlay-companion-actions">
                        <button className="button button-primary button-compact" type="button" onClick={() => void sdExecute()}>
                          <Play size={13} />
                          Ausführen
                        </button>
                        <button className="button button-compact" type="button" onClick={() => void sdSkip()}>
                          <ArrowRight size={13} />
                          Überspringen
                        </button>
                      </div>
                    </>
                  ) : (
                    <p className="overlay-muted">Keine Aktion geplant.</p>
                  )}
                </div>
              ) : (
                <p className="overlay-muted">
                  Beschreibe eine Aufgabe. Muss in config.yaml unter companion.self_drive aktiviert sein.
                </p>
              )}
            </>
          ) : (
            <>
              <div className="overlay-input">
                {goalLabel && !runActive ? <p className="overlay-goal">Ziel: {goalLabel}</p> : null}
                <textarea
                  autoFocus
                  rows={3}
                  placeholder="Was soll der Desktop-Agent tun? (Strg+Enter)"
                  value={taskText}
                  disabled={runActive}
                  onChange={(event) => setTaskText(event.target.value)}
                  onKeyDown={(event) => {
                    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
                      event.preventDefault();
                      void handleStart();
                    }
                  }}
                />
                {!runActive ? (
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
              {events.length ? (
                <ul className="overlay-log">
                  {events.map((event, index) => (
                    <EventLine key={index} event={event} />
                  ))}
                  <div ref={logEndRef} />
                </ul>
              ) : (
                <p className="overlay-muted">Beschreibe eine Aufgabe und starte den Desktop-Agenten.</p>
              )}
            </>
          )}
        </>
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
              Frag mich etwas zu deinem Bildschirm — ich antworte und zeige dir mit dem Zeiger, wo du klicken kannst.
            </p>
          )}
          {snip ? (
            <div className="overlay-snip-chip">
              <Crop size={13} />
              <span>
                Ausschnitt angehängt ({snip.width}×{snip.height}px) — die nächste Frage bezieht sich darauf
              </span>
              <button type="button" aria-label="Ausschnitt entfernen" onClick={() => setSnip(null)}>
                <X size={13} />
              </button>
            </div>
          ) : null}
          <div className="overlay-chat-input">
            <input
              type="text"
              autoFocus
              placeholder={snip ? "Frage zum Ausschnitt…" : "z. B. „Wie komme ich von hier zu den Einstellungen?“"}
              value={question}
              disabled={pointing}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  void handleCompanionAsk();
                }
              }}
            />
            <button
              className="button button-primary button-compact"
              type="button"
              disabled={!question.trim() || pointing}
              onClick={() => void handleCompanionAsk()}
            >
              {pointing ? <Loader2 size={15} className="overlay-spin" /> : <MapPin size={15} />}
              {pointing ? "Schaue…" : "Fragen"}
            </button>
          </div>
          <div className="overlay-companion-actions">
            <button
              className={`button button-compact overlay-source-toggle ${usePapers ? "overlay-source-toggle--active" : ""}`}
              type="button"
              disabled={pointing}
              aria-pressed={usePapers}
              title="Antworten mit deiner lokalen Paper-Bibliothek belegen ([arxiv:...]-Zitate)"
              onClick={() => setUsePapers((prev) => !prev)}
            >
              📄 Paper
            </button>
            <button
              className={`button button-compact overlay-source-toggle ${useWeb ? "overlay-source-toggle--active" : ""}`}
              type="button"
              disabled={pointing}
              aria-pressed={useWeb}
              title="Websuche einbeziehen — die Frage (nicht der Screenshot) geht an die konfigurierte Suchmaschine"
              onClick={() => setUseWeb((prev) => !prev)}
            >
              🌐 Web
            </button>
            <button className="button button-compact" type="button" disabled={pointing} onClick={() => void startSnip()}>
              <Crop size={13} />
              Bereich erklären
            </button>
            {steps.length > 1 && stepIndex < steps.length - 1 ? (
              <button className="button button-compact" type="button" onClick={nextStep}>
                <ArrowRight size={13} />
                Weiter ({stepIndex + 2}/{steps.length})
              </button>
            ) : null}
            {steps.length ? (
              <button className="button button-compact" type="button" onClick={() => void hidePointer()}>
                <EyeOff size={13} />
                Zeiger ausblenden
              </button>
            ) : null}
          </div>
        </>
      )}
    </div>
  );
}
