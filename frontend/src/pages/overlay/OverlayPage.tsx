import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  Check,
  Copy,
  Crop,
  EyeOff,
  List,
  Loader2,
  MapPin,
  Play,
  Plus,
  Send,
  Settings2,
  Sparkles,
  Square,
  X,
} from "lucide-react";

import {
  api,
  askCompanion,
  cancelAgent,
  createCompanionSession,
  getCompanionConfig,
  guideCompanion,
  streamAgentDispatch,
} from "../../api";
import { isTauri, nativeInvoke, nativeListen, signalWindowReady } from "../../native";
import type {
  AgentConfig,
  AgentDispatchEvent,
  AgentMode,
  CaptureResult,
  CompanionConfigInfo,
  CompanionSessionDetail,
  CompanionStep,
  MonitorInfo,
  OverlayTaskPayload,
  SnipResultPayload,
} from "../../types";
import { ChatStream } from "./ChatStream";
import {
  mapServerMessages,
  sessionTitleFromText,
  type OverlayChatEntry,
} from "./companionSession";
import { OverlayNotesPanel } from "./OverlayNotesPanel";
import { AGENT_SIZE, loadOverlayNotesSize } from "./overlayNotesSize";
import { SelfDrivePanel } from "./SelfDrivePanel";
import { SessionList } from "./SessionList";
import { useGuideFlow } from "./useGuideFlow";
import { useSelfDriveLoop } from "./useSelfDriveLoop";

// AI-Cursor overlay (R1 → R6 Companion → R7 rework). Rendered only in the transparent,
// always-on-top Tauri overlay window. One minimalist chat surface with two modes:
//   - Companion: screen-aware chat (ask/guide). The "Schritt-für-Schritt" chip starts a
//     guided sequence — the ring shows one step, the native click watcher auto-advances
//     after the user's real click, the backend verifies each click's effect. The
//     companion only *sees* and *points* — it never drives input.
//   - Selbst-Steuerung: the native loop (autopilot or per-action confirmation) drives
//     real mouse/keyboard via control.rs; Ctrl+Shift+Q is the global emergency stop.
//     The legacy UI-TARS bridge remains as a sub-toggle.
// Sessions persist in DuckDB (companion_sessions) — "Neue Session", session list,
// reopen after restart. Screenshots only leave the machine when the user explicitly
// selects the `anthropic` provider.

/** One streamed dispatch event of the legacy UI-TARS path, rendered compactly. */
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
  const [mode, setMode] = useState<AgentMode>("helper");
  // Separate from `mode`: `mode` still feeds OverlayTaskPayload for the legacy
  // hand-off dispatch, `view` just switches what this window currently shows.
  const [view, setView] = useState<"agent" | "notes">("agent");
  const [config, setConfig] = useState<AgentConfig | null>(null);
  const [companionCfg, setCompanionCfg] = useState<CompanionConfigInfo | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Minimalist shell: pickers live in the gear popover, sessions in the slide-in list.
  const [gearOpen, setGearOpen] = useState(false);
  const [listOpen, setListOpen] = useState(false);

  // Durable session (DuckDB). The ref mirrors the state so loop hooks starting in the
  // same tick as the lazy session creation still see the fresh id.
  const [dbSessionId, setDbSessionId] = useState<string | null>(null);
  const dbSessionIdRef = useRef<string | null>(null);
  const [sessionTitle, setSessionTitle] = useState("");

  const [chat, setChat] = useState<OverlayChatEntry[]>([]);
  const [question, setQuestion] = useState("");
  const [guideMode, setGuideMode] = useState(false);
  const [pointing, setPointing] = useState(false);
  const [steps, setSteps] = useState<CompanionStep[]>([]);
  const [stepIndex, setStepIndex] = useState(0);
  const [snip, setSnip] = useState<SnipResultPayload | null>(null);

  // Pickers (persisted prefs — chat content is server-persisted now).
  const [companionProvider, setCompanionProvider] = useState<string>(
    () => localStorage.getItem("sciencekg.companion.provider") ?? "",
  );
  const [companionModel, setCompanionModel] = useState<string>(
    () => localStorage.getItem("sciencekg.companion.model") ?? "",
  );
  const [discovering, setDiscovering] = useState(false);
  const [monitors, setMonitors] = useState<MonitorInfo[]>([]);
  const [companionMonitor, setCompanionMonitor] = useState<string>(
    () => localStorage.getItem("sciencekg.companion.monitor") ?? "",
  );
  const [usePapers, setUsePapers] = useState(
    () => localStorage.getItem("sciencekg.companion.papers") === "1",
  );
  const [useWeb, setUseWeb] = useState(() => localStorage.getItem("sciencekg.companion.web") === "1");
  const [autopilot, setAutopilot] = useState(true);

  // Legacy UI-TARS bridge state (Selbst-Steuerung sub-toggle, unchanged behaviour).
  const [selfDriveNative, setSelfDriveNative] = useState(
    () => localStorage.getItem("sciencekg.selfdrive.native") !== "0",
  );
  const [taskText, setTaskText] = useState("");
  const [goalLabel, setGoalLabel] = useState("");
  const [starting, setStarting] = useState(false);
  const [briefCopied, setBriefCopied] = useState(false);
  const [runActive, setRunActive] = useState(false);
  const [events, setEvents] = useState<AgentDispatchEvent[]>([]);
  const runIdRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const bridgeBaseRef = useRef<string | null>(null);
  const variantIdRef = useRef<string | null>(null);

  const captureInfoRef = useRef<CaptureResult | null>(null);
  const logEndRef = useRef<HTMLDivElement>(null);

  const addEntry = useCallback((entry: OverlayChatEntry) => {
    setChat((prev) => [...prev, entry]);
  }, []);
  const reportError = useCallback((message: string) => setError(message), []);
  const getDbSessionId = useCallback(() => dbSessionIdRef.current, []);

  const guide = useGuideFlow({
    monitor: companionMonitor,
    provider: companionProvider || undefined,
    model: companionModel || undefined,
    usePapers,
    useWeb,
    getDbSessionId,
    onEntry: addEntry,
    onError: reportError,
  });

  const selfDrive = useSelfDriveLoop({
    monitor: companionMonitor,
    provider: companionProvider || undefined,
    model: companionModel || undefined,
    autopilot,
    settleMs: companionCfg?.self_drive?.settle_ms ?? 800,
    mouseAbortPx: companionCfg?.self_drive?.mouse_abort_px ?? 150,
    actionTimeoutMs: companionCfg?.self_drive?.action_timeout_ms ?? 5000,
    stepTimeoutMs: companionCfg?.self_drive?.step_timeout_ms ?? 120000,
    getDbSessionId,
    onEntry: addEntry,
    onError: reportError,
  });

  // Transparent window: drop the app's opaque background for this window only.
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
        setAutopilot(cfg.self_drive?.autopilot ?? true);
      })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  // Persisted picker prefs.
  useEffect(() => {
    companionProvider
      ? localStorage.setItem("sciencekg.companion.provider", companionProvider)
      : localStorage.removeItem("sciencekg.companion.provider");
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
  useEffect(() => {
    selfDriveNative
      ? localStorage.setItem("sciencekg.selfdrive.native", "1")
      : localStorage.setItem("sciencekg.selfdrive.native", "0");
  }, [selfDriveNative]);

  // Live model discovery per provider — best-effort on top of the cached options.
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

  // Monitor list — xcap ids are session-only, so a stale persisted pick falls back to Auto.
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

  // Events this lazily created window may receive queued from Rust: a pre-loaded
  // task ("An AI-Cursor übergeben") and snipped regions. Both listeners register in
  // one effect, then the shell is told the window is ready so it drains the queue —
  // signalling any earlier would drop the very event the window was created for.
  useEffect(() => {
    if (!isTauri()) return;
    let disposed = false;
    const unlisteners: (() => void)[] = [];
    void (async () => {
      const offTask = await nativeListen<OverlayTaskPayload>("overlay://task", (payload) => {
        setTaskText(payload.task ?? "");
        setGoalLabel(payload.goal ?? "");
        setMode(payload.mode === "helper" ? "helper" : "self_managing");
        variantIdRef.current = payload.variantId ?? null;
      });
      const offSnip = await nativeListen<SnipResultPayload>("snip://result", (payload) => {
        setSnip(payload);
        setMode("helper");
      });
      if (disposed) {
        offTask();
        offSnip();
        return;
      }
      unlisteners.push(offTask, offSnip);
      await signalWindowReady();
    })();
    return () => {
      disposed = true;
      unlisteners.forEach((fn) => fn());
    };
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      // Notiz-Editor-Popover (KI-Auswahlfrage) ruft bei Escape nur preventDefault(),
      // damit es dieses Fenster nicht mitschließt statt nur sich selbst.
      if (event.key === "Escape" && !event.defaultPrevented) void hide();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Notizen brauchen deutlich mehr Platz als der Companion-Chat — Fenster wächst/
  // schrumpft beim Tab-Wechsel. Die Notizen-Größe merkt sich der zuletzt manuell per
  // Ziehgriff gewählte Wert (localStorage, siehe overlayNotesSize.ts), sonst ein
  // sinnvoller Default; im Web-Modus (kein Tauri) bleibt die Größe fix.
  useEffect(() => {
    if (!isTauri()) return;
    const size = view === "notes" ? loadOverlayNotesSize() : AGENT_SIZE;
    void nativeInvoke("overlay_resize", size);
  }, [view]);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ block: "end" });
  }, [events, chat]);

  // Emergency stop (Ctrl+Shift+Q / Maus-Ruck / Aktions-Timeout, emitted from Rust):
  // abort both loops — enigo is already disarmed and the click watcher stopped there.
  useEffect(() => {
    if (!isTauri()) return;
    let unlisten: (() => void) | undefined;
    nativeListen<{ reason?: string } | null>("selfdrive://emergency-stop", (payload) => {
      const reason = payload?.reason;
      setError(
        reason === "mouse"
          ? "Not-Aus durch Mausbewegung — alles gestoppt."
          : reason === "timeout"
            ? "Not-Aus durch Aktions-Timeout — alles gestoppt."
            : "Not-Aus ausgelöst — alles gestoppt.",
      );
      void selfDrive.abort(reason);
      guide.stop();
    }).then((fn) => {
      unlisten = fn;
    });
    return () => unlisten?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selfDrive.abort, guide.stop]);

  async function hide() {
    if (isTauri()) await nativeInvoke("overlay_hide").catch(() => {});
  }

  /** Lazily create the durable DuckDB session on the first message of a chat. */
  async function ensureDbSession(kind: "companion" | "selfdrive", firstText: string): Promise<string | null> {
    if (dbSessionIdRef.current) return dbSessionIdRef.current;
    try {
      const created = await createCompanionSession({
        kind,
        title: sessionTitleFromText(firstText),
        goal: kind === "selfdrive" ? firstText : "",
        provider: companionProvider || undefined,
        model: companionModel || undefined,
        monitor: companionMonitor ? Number(companionMonitor) : null,
      });
      dbSessionIdRef.current = created.id;
      setDbSessionId(created.id);
      setSessionTitle(created.title || sessionTitleFromText(firstText));
      return created.id;
    } catch {
      return null; // persistence is best-effort — the chat still works
    }
  }

  /** "Neue Session": stop everything, clear the stream, next message opens a fresh row. */
  async function newSession() {
    guide.stop();
    await selfDrive.stop();
    await hidePointer();
    dbSessionIdRef.current = null;
    setDbSessionId(null);
    setSessionTitle("");
    setChat([]);
    setSnip(null);
    setError(null);
    setListOpen(false);
  }

  /** Reopen a persisted session from the list. */
  async function openSession(detail: CompanionSessionDetail) {
    guide.stop();
    await selfDrive.stop();
    await hidePointer();
    dbSessionIdRef.current = detail.id;
    setDbSessionId(detail.id);
    setSessionTitle((detail.title || detail.goal || "").trim());
    setChat(mapServerMessages(detail.messages ?? []));
    setMode(detail.kind === "selfdrive" ? "self_managing" : "helper");
    if (detail.kind === "selfdrive") setSelfDriveNative(true);
    setError(null);
    setListOpen(false);
  }

  /** Glide the pointer ring to one one-shot guidance step (legacy multi-step answers). */
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

  /** Start a "Bereich erklären" region selection (frozen-frame snip). */
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

  /** One-shot Companion question: screenshot → /companion/guide (answer + optional
   * pointing) — or /companion/ask for snipped regions. */
  async function companionAsk(text: string) {
    setPointing(true);
    setError(null);
    const history = chat
      .filter((entry) => entry.role !== "system")
      .slice(-16)
      .map((entry) => ({ role: entry.role, content: entry.text }));
    addEntry({ role: "user", text });
    setSteps([]);
    setStepIndex(0);
    if (isTauri()) await nativeInvoke("pointer_hide").catch(() => {});
    const provider = companionProvider || undefined;
    const model = companionModel || undefined;
    const sessionId = await ensureDbSession("companion", text);
    try {
      if (snip) {
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
          session_id: sessionId,
        });
        addEntry({
          role: "assistant",
          text: res.error ? `Fehler: ${res.error}` : res.answer || "Dazu kann ich nichts sagen.",
          sources: res.sources,
        });
        return;
      }
      if (!isTauri()) {
        addEntry({
          role: "assistant",
          text: "Der Desktop-Companion braucht die native Desktop-App — im Browser kann ich deinen Bildschirm nicht sehen.",
        });
        return;
      }
      const capture = await nativeInvoke<CaptureResult>("capture_screen", {
        monitor: companionMonitor ? Number(companionMonitor) : null,
      });
      captureInfoRef.current = capture;
      const res = await guideCompanion({
        question: text,
        image_base64: capture.image_base64,
        history,
        provider,
        model,
        use_papers: usePapers,
        use_web: useWeb,
        session_id: sessionId,
      });
      if (res.error) {
        addEntry({ role: "assistant", text: `Fehler: ${res.error}` });
        return;
      }
      addEntry({
        role: "assistant",
        text: res.answer || "Dazu kann ich nichts sagen.",
        sources: res.sources,
      });
      if (res.steps?.length) {
        setSteps(res.steps);
        setStepIndex(0);
        await showStep(res.steps, 0);
      }
    } catch (err) {
      addEntry({ role: "assistant", text: err instanceof Error ? err.message : String(err) });
    } finally {
      setPointing(false);
    }
  }

  /** The one shared input routes by mode + state: companion question, guided goal,
   * self-drive goal, or the answer to a running loop's Rückfrage. */
  async function handleSubmit() {
    const text = question.trim();
    if (!text || inputBusy) return;
    setQuestion("");
    if (mode === "helper") {
      if (guideMode) {
        await ensureDbSession("companion", text);
        addEntry({ role: "user", text });
        await guide.start(text);
        return;
      }
      await companionAsk(text);
      return;
    }
    if (!selfDriveNative) return; // legacy path has its own textarea + Starten
    if (selfDrive.askQuestion) {
      await selfDrive.answer(text);
      return;
    }
    if (!selfDrive.sessionId) {
      await ensureDbSession("selfdrive", text);
      addEntry({ role: "user", text });
      await selfDrive.start(text);
    }
  }

  /** Legacy UI-TARS bridge (unchanged): ensure sidecar, stream the run. */
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

  async function handleLegacyStart() {
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

  async function handleLegacyStop() {
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

  function copyBrief() {
    const text = taskText.trim();
    if (!text) return;
    void navigator.clipboard?.writeText(text).then(() => {
      setBriefCopied(true);
      window.setTimeout(() => setBriefCopied(false), 1500);
    });
  }

  const providerOptions = companionCfg?.providers ?? [];
  const activeProviderModels =
    providerOptions.find((item) => item.name === companionProvider)?.models ?? [];
  const modelOptions = Array.from(
    new Set([...activeProviderModels, ...(companionModel ? [companionModel] : [])]),
  ).filter(Boolean);
  const monitorLabel = (item: MonitorInfo) =>
    `${item.name || `Monitor ${item.id}`}${item.is_primary ? " (primär)" : ""}`;

  const inputBusy = pointing || guide.busy || (selfDrive.busy && !selfDrive.askQuestion);
  const selfDriveWaitsForAnswer = mode === "self_managing" && selfDriveNative && !!selfDrive.askQuestion;
  const inputPlaceholder =
    mode === "helper"
      ? guideMode
        ? "Ziel, zu dem ich dich Schritt für Schritt führe…"
        : snip
          ? "Frage zum Ausschnitt…"
          : "Frag mich etwas zu deinem Bildschirm…"
      : selfDriveWaitsForAnswer
        ? "Antwort auf die Rückfrage…"
        : "Ziel für die Selbst-Steuerung… (Not-Aus: Strg+Shift+Q)";
  const showSharedInput = mode === "helper" || (mode === "self_managing" && selfDriveNative);

  return (
    <div className="overlay-root">
      <header className="overlay-head" data-tauri-drag-region>
        <Sparkles size={15} />
        <strong>{sessionTitle || "AI-Cursor"}</strong>
        <span className="overlay-head-actions">
          <button
            type="button"
            className="overlay-close"
            title="Sessions"
            aria-label="Sessions"
            onClick={() => setListOpen((prev) => !prev)}
          >
            <List size={14} />
          </button>
          <button
            type="button"
            className="overlay-close"
            title="Neue Session"
            aria-label="Neue Session"
            onClick={() => void newSession()}
          >
            <Plus size={14} />
          </button>
          <button
            type="button"
            className="overlay-close"
            title="Einstellungen"
            aria-label="Einstellungen"
            onClick={() => setGearOpen((prev) => !prev)}
          >
            <Settings2 size={14} />
          </button>
          <button className="overlay-close" type="button" onClick={() => void hide()} aria-label="Ausblenden">
            <X size={15} />
          </button>
        </span>
      </header>

      {listOpen ? (
        <SessionList
          activeId={dbSessionId}
          onSelect={(detail) => void openSession(detail)}
          onClose={() => setListOpen(false)}
          onError={reportError}
        />
      ) : null}

      {gearOpen ? (
        <div className="overlay-gear">
          <select
            aria-label="Provider"
            value={companionProvider}
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
          <div className="overlay-companion-actions">
            <button
              className={`button button-compact overlay-source-toggle ${usePapers ? "overlay-source-toggle--active" : ""}`}
              type="button"
              aria-pressed={usePapers}
              title="Antworten mit deiner lokalen Paper-Bibliothek belegen ([arxiv:...]-Zitate)"
              onClick={() => setUsePapers((prev) => !prev)}
            >
              📄 Paper
            </button>
            <button
              className={`button button-compact overlay-source-toggle ${useWeb ? "overlay-source-toggle--active" : ""}`}
              type="button"
              aria-pressed={useWeb}
              title="Websuche einbeziehen — die Frage (nicht der Screenshot) geht an die Suchmaschine"
              onClick={() => setUseWeb((prev) => !prev)}
            >
              🌐 Web
            </button>
            {discovering ? <Loader2 size={13} className="overlay-spin overlay-picker-spin" /> : null}
          </div>
          {companionProvider === "anthropic" ? (
            <p className="overlay-model-hint">Screenshots werden zur Beantwortung an Anthropic gesendet.</p>
          ) : null}
        </div>
      ) : null}

      <div className="overlay-mode-toggle" role="tablist" aria-label="Ansicht">
        <button
          type="button"
          className={`overlay-mode-toggle__item ${view === "agent" ? "overlay-mode-toggle__item--active" : ""}`}
          onClick={() => setView("agent")}
        >
          Agent
        </button>
        <button
          type="button"
          className={`overlay-mode-toggle__item ${view === "notes" ? "overlay-mode-toggle__item--active" : ""}`}
          disabled={runActive || pointing || guide.active || !!selfDrive.sessionId}
          onClick={() => setView("notes")}
        >
          Notizen
        </button>
      </div>

      {view === "notes" ? (
        <OverlayNotesPanel />
      ) : (
        <>
      <div className="overlay-mode-toggle" role="tablist" aria-label="Modus">
        <button
          type="button"
          className={`overlay-mode-toggle__item ${mode === "helper" ? "overlay-mode-toggle__item--active" : ""}`}
          disabled={runActive || !!selfDrive.sessionId}
          onClick={() => setMode("helper")}
        >
          Companion
        </button>
        <button
          type="button"
          className={`overlay-mode-toggle__item ${mode === "self_managing" ? "overlay-mode-toggle__item--active" : ""}`}
          disabled={runActive || pointing || guide.active}
          onClick={() => setMode("self_managing")}
        >
          Selbst-Steuerung
        </button>
      </div>

      {error ? (
        <div className="overlay-hint overlay-hint--error">
          <p>{error}</p>
          <button type="button" className="overlay-close" aria-label="Fehler schließen" onClick={() => setError(null)}>
            <X size={12} />
          </button>
          {mode === "self_managing" && !selfDriveNative && taskText.trim() ? (
            <button className="button button-compact" type="button" onClick={copyBrief}>
              {briefCopied ? <Check size={13} /> : <Copy size={13} />}
              {briefCopied ? "Kopiert" : "Brief kopieren"}
            </button>
          ) : null}
        </div>
      ) : null}

      {mode === "self_managing" ? (
        <div className="overlay-mode-toggle overlay-mode-toggle--sub" role="tablist" aria-label="Selbst-Steuerung-Variante">
          <button
            type="button"
            className={`overlay-mode-toggle__item ${selfDriveNative ? "overlay-mode-toggle__item--active" : ""}`}
            disabled={runActive || !!selfDrive.sessionId}
            onClick={() => setSelfDriveNative(true)}
          >
            Nativ
          </button>
          <button
            type="button"
            className={`overlay-mode-toggle__item ${!selfDriveNative ? "overlay-mode-toggle__item--active" : ""}`}
            disabled={runActive || !!selfDrive.sessionId}
            onClick={() => setSelfDriveNative(false)}
          >
            UI-TARS-Bridge (Legacy)
          </button>
        </div>
      ) : null}

      {mode === "self_managing" && !selfDriveNative ? (
        <>
          {config ? (
            <p className="overlay-model-hint">
              Modell: <strong>{config.vlm_model}</strong> (muss UI-TARS-kompatibel sein)
            </p>
          ) : null}
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
                  void handleLegacyStart();
                }
              }}
            />
            {!runActive ? (
              <button
                className="button button-primary button-compact"
                type="button"
                disabled={!taskText.trim() || starting}
                onClick={() => void handleLegacyStart()}
              >
                {starting ? <Loader2 size={15} className="overlay-spin" /> : <Play size={15} />}
                {starting ? "Startet…" : "Starten"}
              </button>
            ) : (
              <button className="button button-compact overlay-stop" type="button" onClick={() => void handleLegacyStop()}>
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
      ) : (
        <>
          <ChatStream
            entries={chat}
            endRef={logEndRef}
            emptyHint={
              mode === "helper"
                ? "Frag mich etwas zu deinem Bildschirm — oder aktiviere „Schritt für Schritt“, dann führe ich dich Klick für Klick."
                : "Beschreibe dein Ziel — ich übernehme Maus und Tastatur. Du kannst jederzeit pausieren oder mit Strg+Shift+Q abbrechen."
            }
          />

          {mode === "self_managing" && selfDriveNative ? (
            <SelfDrivePanel loop={selfDrive} autopilot={autopilot} onAutopilotChange={setAutopilot} />
          ) : null}

          {mode === "helper" && guide.active ? (
            <div className="overlay-companion-actions">
              <span className="overlay-muted">
                {guide.busy ? (
                  <>
                    <Loader2 size={13} className="overlay-spin" /> Beobachte…
                  </>
                ) : (
                  <>Geführt — klicke, wohin der Ring zeigt{guide.stepInfo ? ` (${guide.stepInfo.index}/${guide.stepInfo.max})` : ""}</>
                )}
              </span>
              <button className="button button-compact" type="button" onClick={guide.skip}>
                <ArrowRight size={13} /> Schritt überspringen
              </button>
              <button className="button button-compact overlay-stop" type="button" onClick={guide.stop}>
                <Square size={13} /> Beenden
              </button>
            </div>
          ) : null}

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

          {showSharedInput ? (
            <div className="overlay-chat-input">
              <input
                type="text"
                autoFocus
                placeholder={inputPlaceholder}
                value={question}
                disabled={inputBusy || (mode === "self_managing" && !!selfDrive.sessionId && !selfDrive.askQuestion)}
                onChange={(event) => setQuestion(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    void handleSubmit();
                  }
                }}
              />
              <button
                className="button button-primary button-compact"
                type="button"
                disabled={
                  !question.trim() ||
                  inputBusy ||
                  (mode === "self_managing" && !!selfDrive.sessionId && !selfDrive.askQuestion)
                }
                onClick={() => void handleSubmit()}
              >
                {inputBusy ? (
                  <Loader2 size={15} className="overlay-spin" />
                ) : mode === "helper" ? (
                  <MapPin size={15} />
                ) : (
                  <Send size={15} />
                )}
              </button>
            </div>
          ) : null}

          {mode === "helper" ? (
            <div className="overlay-companion-actions">
              <button
                className={`button button-compact overlay-source-toggle ${guideMode ? "overlay-source-toggle--active" : ""}`}
                type="button"
                disabled={pointing || guide.active}
                aria-pressed={guideMode}
                title="Schritt-für-Schritt-Führung: Ring zeigt den nächsten Klick, dein Klick schaltet automatisch weiter"
                onClick={() => setGuideMode((prev) => !prev)}
              >
                👣 Schritt für Schritt
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
          ) : null}
        </>
      )}
        </>
      )}
    </div>
  );
}
