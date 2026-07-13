// Guided-sequence loop (R7): plan one pointer step per screenshot, auto-advance on
// the user's real click (native click watcher → `companion://click`).
//
// Flow per round trip: capture_screen → POST /companion/guide/step → show the ring
// (pointer_show, physical space + capture origin) → wait for the click event →
// settle → capture again with event:"click" (+ click coords in screenshot px, so
// the backend can verify the step's expectation) → next step … until done.
//
// Busy-gating: click events are ignored while a round trip is in flight (double
// clicks / drag releases emit extra events) and when the click landed on the chat
// card itself (`on_overlay`).

import { useCallback, useEffect, useRef, useState } from "react";

import { startGuide, stepGuide, stopGuide } from "../../api";
import { isTauri, nativeInvoke, nativeListen } from "../../native";
import type { CaptureResult, CompanionClickPayload, GuideStepResult } from "../../types";
import type { OverlayChatEntry } from "./companionSession";

type GuideFlowOptions = {
  monitor: string;
  provider?: string;
  model?: string;
  usePapers: boolean;
  useWeb: boolean;
  /** Ref-getter (not state) — the durable session may be created in the same tick. */
  getDbSessionId: () => string | null;
  onEntry: (entry: OverlayChatEntry) => void;
  onError: (message: string) => void;
};

export function useGuideFlow(options: GuideFlowOptions) {
  const [active, setActive] = useState(false);
  const [busy, setBusy] = useState(false);
  const [stepInfo, setStepInfo] = useState<{ index: number; max: number } | null>(null);

  const optionsRef = useRef(options);
  optionsRef.current = options;
  const guideIdRef = useRef<string | null>(null);
  const clickSettleRef = useRef(700);
  const busyRef = useRef(false);
  const captureRef = useRef<CaptureResult | null>(null);
  const unlistenRef = useRef<(() => void) | null>(null);

  const cleanup = useCallback(async (dropSession: boolean) => {
    const guideId = guideIdRef.current;
    guideIdRef.current = null;
    busyRef.current = false;
    setActive(false);
    setBusy(false);
    setStepInfo(null);
    unlistenRef.current?.();
    unlistenRef.current = null;
    if (isTauri()) {
      await nativeInvoke("click_watch_stop").catch(() => {});
      await nativeInvoke("pointer_hide").catch(() => {});
    }
    if (dropSession && guideId) await stopGuide({ guide_id: guideId }).catch(() => {});
  }, []);

  useEffect(() => () => void cleanup(true), [cleanup]);

  const showStep = useCallback(async (step: { x: number; y: number; label: string }) => {
    const capture = captureRef.current;
    await nativeInvoke("pointer_show", {
      x: step.x,
      y: step.y,
      label: step.label || null,
      space: "physical",
      originX: capture?.origin_x ?? null,
      originY: capture?.origin_y ?? null,
      monitorWidth: capture?.width ?? null,
      monitorHeight: capture?.height ?? null,
    }).catch(() => {});
  }, []);

  const runStep = useCallback(
    async (event: "start" | "click" | "skip", clickX?: number, clickY?: number) => {
      const guideId = guideIdRef.current;
      if (!guideId || busyRef.current) return;
      busyRef.current = true;
      setBusy(true);
      try {
        const capture = await nativeInvoke<CaptureResult>("capture_screen", {
          monitor: optionsRef.current.monitor ? Number(optionsRef.current.monitor) : null,
        });
        // Verify uses the click position relative to the *previous* capture's monitor;
        // both captures target the same monitor, so the fresh origins are equivalent.
        captureRef.current = capture;
        const result: GuideStepResult = await stepGuide({
          guide_id: guideId,
          image_base64: capture.image_base64,
          event,
          click_x: clickX ?? null,
          click_y: clickY ?? null,
        });
        if (result.error) {
          optionsRef.current.onError(result.error);
          await cleanup(true);
          return;
        }
        if (result.verification && result.verification.ok === false) {
          optionsRef.current.onEntry({
            role: "system",
            text: `Prüfung: der letzte Klick hat nicht gewirkt${result.verification.note ? ` — ${result.verification.note}` : ""}`,
            verification: result.verification,
          });
        }
        if (result.instruction) {
          optionsRef.current.onEntry({ role: "assistant", text: result.instruction });
        }
        setStepInfo(
          result.step_index != null
            ? { index: result.step_index, max: result.max_steps ?? 0 }
            : null,
        );
        if (result.done) {
          await cleanup(true);
          return;
        }
        if (result.step) {
          await showStep(result.step);
        } else if (isTauri()) {
          await nativeInvoke("pointer_hide").catch(() => {});
        }
      } catch (err) {
        optionsRef.current.onError(err instanceof Error ? err.message : String(err));
        await cleanup(true);
      } finally {
        busyRef.current = false;
        setBusy(false);
      }
    },
    [cleanup, showStep],
  );

  const start = useCallback(
    async (goal: string) => {
      if (!isTauri()) {
        optionsRef.current.onError(
          "Der geführte Modus braucht die native Desktop-App (Bildschirm + Klick-Erkennung).",
        );
        return;
      }
      if (guideIdRef.current) await cleanup(true);
      const opts = optionsRef.current;
      const started = await startGuide({
        goal,
        provider: opts.provider || undefined,
        model: opts.model || undefined,
        monitor: opts.monitor ? Number(opts.monitor) : null,
        use_papers: opts.usePapers,
        use_web: opts.useWeb,
        session_id: opts.getDbSessionId(),
      });
      if (started.error || !started.guide_id) {
        opts.onError(started.error ?? "Geführter Modus konnte nicht gestartet werden.");
        return;
      }
      guideIdRef.current = started.guide_id;
      clickSettleRef.current = started.click_settle_ms ?? 700;
      setActive(true);

      // Watch the user's real clicks; each one advances the sequence.
      try {
        await nativeInvoke("click_watch_start");
        unlistenRef.current = await nativeListen<CompanionClickPayload>(
          "companion://click",
          (payload) => {
            if (!guideIdRef.current || busyRef.current || payload.on_overlay) return;
            const capture = captureRef.current;
            const clickX = payload.x - (capture?.origin_x ?? 0);
            const clickY = payload.y - (capture?.origin_y ?? 0);
            window.setTimeout(() => {
              void runStep("click", clickX, clickY);
            }, clickSettleRef.current);
          },
        );
      } catch (err) {
        opts.onError(err instanceof Error ? err.message : String(err));
        await cleanup(true);
        return;
      }
      await runStep("start");
    },
    [cleanup, runStep],
  );

  const skip = useCallback(() => void runStep("skip"), [runStep]);
  const stop = useCallback(() => void cleanup(true), [cleanup]);

  return { active, busy, stepInfo, start, skip, stop };
}
