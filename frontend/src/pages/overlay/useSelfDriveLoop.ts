// Native Selbst-Steuerung loop (R7): autopilot by default — plan, execute, settle,
// re-plan — with an optional per-action confirmation mode, pause/resume and the
// global emergency stop (Ctrl+Shift+Q, handled by the shell via `abort`).
//
// The backend runs the verify → stall → plan → refine pipeline per screenshot and
// resolves `lookup` actions itself; this hook only executes actions (control.rs)
// and pauses for `ask` actions until the user answers.

import { useCallback, useEffect, useRef, useState } from "react";

import {
  answerSelfDrive,
  startSelfDrive,
  stepSelfDrive,
  stopSelfDrive,
  updateCompanionSession,
} from "../../api";
import { isTauri, nativeInvoke } from "../../native";
import type { CaptureResult, SelfDriveAction, SelfDriveStepResult } from "../../types";
import { describeSelfDriveAction, shouldAutoExecute, type OverlayChatEntry } from "./companionSession";

const sleep = (ms: number) => new Promise<void>((resolve) => window.setTimeout(resolve, ms));

class DeadlineError extends Error {
  constructor(label: string, ms: number) {
    super(`${label} nach ${Math.round(ms / 1000)} s abgebrochen (Timeout).`);
    this.name = "DeadlineError";
  }
}

/** Hard deadline around one async phase — a wedged capture/plan/execute round must
 * never leave the loop (and the armed shell) hanging forever. */
async function withDeadline<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
  let timer: number | undefined;
  try {
    return await Promise.race([
      promise,
      new Promise<never>((_, reject) => {
        timer = window.setTimeout(() => reject(new DeadlineError(label, ms)), ms);
      }),
    ]);
  } finally {
    window.clearTimeout(timer);
  }
}

type SelfDriveLoopOptions = {
  monitor: string;
  provider?: string;
  model?: string;
  autopilot: boolean;
  settleMs: number;
  /** companion.self_drive.mouse_abort_px — user movement threshold for the jerk stop. */
  mouseAbortPx: number;
  /** companion.self_drive.action_timeout_ms — per enigo action (enforced in Rust). */
  actionTimeoutMs: number;
  /** companion.self_drive.step_timeout_ms — capture→plan round trip (enforced here). */
  stepTimeoutMs: number;
  /** Ref-getter (not state) — the durable session may be created in the same tick. */
  getDbSessionId: () => string | null;
  onEntry: (entry: OverlayChatEntry) => void;
  onError: (message: string) => void;
};

export function useSelfDriveLoop(options: SelfDriveLoopOptions) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [paused, setPaused] = useState(false);
  const [thought, setThought] = useState("");
  const [stepInfo, setStepInfo] = useState<{ step: number; max: number } | null>(null);
  const [pendingAction, setPendingAction] = useState<SelfDriveAction | null>(null);
  const [askQuestion, setAskQuestion] = useState<string | null>(null);

  const optionsRef = useRef(options);
  optionsRef.current = options;
  const sessionRef = useRef<string | null>(null);
  const pausedRef = useRef(false);
  const waitingResumeRef = useRef(false);
  const busyRef = useRef(false);
  const captureRef = useRef<CaptureResult | null>(null);

  const reset = useCallback((message?: string) => {
    sessionRef.current = null;
    pausedRef.current = false;
    waitingResumeRef.current = false;
    busyRef.current = false;
    setSessionId(null);
    setArmed(false);
    setBusy(false);
    setPaused(false);
    setPendingAction(null);
    setAskQuestion(null);
    setStepInfo(null);
    if (message) optionsRef.current.onEntry({ role: "system", text: message });
  }, []);

  /** Persist the run outcome on the durable DuckDB session (best-effort). */
  const persistStatus = useCallback(async (status: "stopped" | "done" | "failed") => {
    const dbId = optionsRef.current.getDbSessionId();
    if (dbId) await updateCompanionSession(dbId, { status }).catch(() => {});
  }, []);

  /** User stop: disarm the shell (hides the border) and drop the backend session. */
  const stop = useCallback(async () => {
    const id = sessionRef.current;
    reset();
    if (isTauri()) await nativeInvoke("self_drive_disarm").catch(() => {});
    if (id) {
      await stopSelfDrive({ session_id: id }).catch(() => {});
      await persistStatus("stopped");
    }
  }, [persistStatus, reset]);

  /** Emergency stop arrived from Rust — enigo is already disarmed there; just drop.
   * `reason` comes from the event payload ("hotkey" | "mouse" | "timeout"). */
  const abort = useCallback(
    async (reason?: string) => {
      const id = sessionRef.current;
      const message =
        reason === "mouse"
          ? "Not-Aus durch Mausbewegung ausgelöst — Selbst-Steuerung gestoppt."
          : reason === "timeout"
            ? "Not-Aus durch Aktions-Timeout ausgelöst — Selbst-Steuerung gestoppt."
            : "Not-Aus ausgelöst — Selbst-Steuerung gestoppt.";
      reset(message);
      if (id) {
        await stopSelfDrive({ session_id: id }).catch(() => {});
        await persistStatus("stopped");
      }
    },
    [persistStatus, reset],
  );

  useEffect(() => () => void stop(), [stop]);

  const finish = useCallback(
    async (result: SelfDriveStepResult) => {
      optionsRef.current.onEntry({
        role: "assistant",
        text: `Selbst-Steuerung beendet (${result.action?.type ?? "done"}). ${result.thought ?? ""}`.trim(),
      });
      const outcome = result.action?.type === "fail" ? "failed" : "done";
      const id = sessionRef.current;
      reset();
      if (isTauri()) await nativeInvoke("self_drive_disarm").catch(() => {});
      if (id) {
        await stopSelfDrive({ session_id: id }).catch(() => {});
        await persistStatus(outcome);
      }
    },
    [persistStatus, reset],
  );

  const executeAction = useCallback(async (action: SelfDriveAction) => {
    const capture = captureRef.current;
    const timeoutMs = optionsRef.current.actionTimeoutMs;
    const toPhysical = (value: number, axis: "x" | "y") =>
      (axis === "x" ? capture?.origin_x ?? 0 : capture?.origin_y ?? 0) + value;
    if ((action.type === "click" || action.type === "double_click") && action.x != null && action.y != null) {
      await nativeInvoke("control_click", {
        x: toPhysical(action.x, "x"),
        y: toPhysical(action.y, "y"),
        button: "left",
        double: action.type === "double_click",
        timeoutMs,
      });
    } else if (action.type === "move" && action.x != null && action.y != null) {
      await nativeInvoke("control_move", {
        x: toPhysical(action.x, "x"),
        y: toPhysical(action.y, "y"),
        timeoutMs,
      });
    } else if (action.type === "type") {
      await nativeInvoke("control_type", { text: action.text ?? "", timeoutMs });
    } else if (action.type === "key") {
      await nativeInvoke("control_key", { combo: action.keys ?? "", timeoutMs });
    } else if (action.type === "scroll") {
      await nativeInvoke("control_scroll", { dx: action.dx ?? 0, dy: action.dy ?? 0, timeoutMs });
    }
    // "wait" falls through — just observe again after the settle delay.
  }, []);

  // Forward declaration dance: plan ↔ runAction call each other.
  const planRef = useRef<() => Promise<void>>(async () => {});

  const runAction = useCallback(
    async (action: SelfDriveAction) => {
      if (!sessionRef.current) return;
      setPendingAction(null);
      busyRef.current = true;
      setBusy(true);
      try {
        // Rust enforces the per-action deadline itself; this outer deadline only
        // catches a wedged IPC round trip, hence the small grace on top.
        await withDeadline(
          executeAction(action),
          optionsRef.current.actionTimeoutMs + 2000,
          "Aktion",
        );
        optionsRef.current.onEntry({ role: "system", text: describeSelfDriveAction(action) });
      } catch (err) {
        optionsRef.current.onError(err instanceof Error ? err.message : String(err));
        busyRef.current = false;
        setBusy(false);
        if (err instanceof DeadlineError) await stop();
        return;
      }
      await sleep(optionsRef.current.settleMs);
      busyRef.current = false;
      setBusy(false);
      if (pausedRef.current) {
        waitingResumeRef.current = true;
        return;
      }
      await planRef.current();
    },
    [executeAction, stop],
  );

  const plan = useCallback(async () => {
    const id = sessionRef.current;
    if (!id || busyRef.current) return;
    if (pausedRef.current) {
      waitingResumeRef.current = true;
      return;
    }
    busyRef.current = true;
    setBusy(true);
    try {
      // One hard deadline over the whole capture→plan round trip (verify + plan +
      // refine on a local VLM legitimately take a while — default 120 s).
      const stepTimeoutMs = optionsRef.current.stepTimeoutMs;
      const capture = await withDeadline(
        nativeInvoke<CaptureResult>("capture_screen", {
          monitor: optionsRef.current.monitor ? Number(optionsRef.current.monitor) : null,
        }),
        stepTimeoutMs,
        "Bildschirmaufnahme",
      );
      captureRef.current = capture;
      const result = await withDeadline(
        stepSelfDrive({ session_id: id, image_base64: capture.image_base64 }),
        stepTimeoutMs,
        "Planungsschritt",
      );
      if (result.error) {
        optionsRef.current.onError(result.error);
        return;
      }
      if (result.verification && result.verification.ok === false) {
        optionsRef.current.onEntry({
          role: "system",
          text: `Prüfung: fehlgeschlagen${result.verification.note ? ` — ${result.verification.note}` : ""}`,
          verification: result.verification,
        });
      }
      setThought(result.thought ?? "");
      setStepInfo(result.step != null ? { step: result.step, max: result.max_steps ?? 0 } : null);
      if (result.done || !result.action || result.action.type === "done" || result.action.type === "fail") {
        await finish(result);
        return;
      }
      const action = result.action;
      if (action.type === "ask") {
        setAskQuestion(action.question ?? "Wie soll ich fortfahren?");
        optionsRef.current.onEntry({
          role: "assistant",
          text: action.question ?? "Wie soll ich fortfahren?",
        });
        return;
      }
      if (shouldAutoExecute(action, optionsRef.current.autopilot, pausedRef.current)) {
        busyRef.current = false;
        setBusy(false);
        await runAction(action);
        return;
      }
      if (action.sensitive && optionsRef.current.autopilot) {
        optionsRef.current.onEntry({
          role: "system",
          text: `Sensible Aktion — Bestätigung nötig${action.sensitive_reason ? `: ${action.sensitive_reason}` : "."}`,
        });
      }
      setPendingAction(action);
    } catch (err) {
      optionsRef.current.onError(err instanceof Error ? err.message : String(err));
      if (err instanceof DeadlineError) {
        busyRef.current = false;
        setBusy(false);
        await stop();
      }
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }, [finish, runAction, stop]);
  planRef.current = plan;

  const start = useCallback(
    async (goal: string) => {
      if (!isTauri()) {
        optionsRef.current.onError("Selbst-Steuerung braucht die native Desktop-App.");
        return;
      }
      if (sessionRef.current) await stop();
      const opts = optionsRef.current;
      const res = await startSelfDrive({
        goal,
        monitor: opts.monitor ? Number(opts.monitor) : null,
        provider: opts.provider || undefined,
        model: opts.model || undefined,
        session_id: opts.getDbSessionId(),
      });
      if (res.error || !res.session_id) {
        opts.onError(res.error ?? "Selbst-Steuerung konnte nicht gestartet werden.");
        return;
      }
      sessionRef.current = res.session_id;
      setSessionId(res.session_id);
      await nativeInvoke("self_drive_arm", { thresholdPx: opts.mouseAbortPx }).catch(() => {});
      setArmed(true);
      await plan();
    },
    [plan, stop],
  );

  const pause = useCallback(() => {
    pausedRef.current = true;
    setPaused(true);
  }, []);

  const resume = useCallback(() => {
    pausedRef.current = false;
    setPaused(false);
    if (waitingResumeRef.current) {
      waitingResumeRef.current = false;
      void planRef.current();
    }
  }, []);

  /** Confirm the pending action (confirmation mode). */
  const confirm = useCallback(() => {
    if (pendingAction) void runAction(pendingAction);
  }, [pendingAction, runAction]);

  /** Skip the pending action without executing (re-observe + re-plan). */
  const skip = useCallback(() => {
    setPendingAction(null);
    void planRef.current();
  }, []);

  /** Answer an `ask` action; the loop resumes with the next planning round. */
  const answer = useCallback(async (text: string) => {
    const id = sessionRef.current;
    if (!id) return;
    setAskQuestion(null);
    optionsRef.current.onEntry({ role: "user", text });
    const res = await answerSelfDrive({ session_id: id, answer: text });
    if (res.error) {
      optionsRef.current.onError(res.error);
      return;
    }
    await planRef.current();
  }, []);

  return {
    sessionId,
    armed,
    busy,
    paused,
    thought,
    stepInfo,
    pendingAction,
    askQuestion,
    start,
    stop,
    abort,
    pause,
    resume,
    confirm,
    skip,
    answer,
  };
}
