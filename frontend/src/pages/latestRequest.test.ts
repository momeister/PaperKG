import { expect, it } from "vitest";
import { LatestRequest } from "./latestRequest";

function deferred<T>() { let resolve!: (value: T) => void, reject!: (error: Error) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }

it("freezes starting provider/model and ignores late success and failure after a switch", async () => {
  const gate = new LatestRequest();
  const choice = { provider: "glm", model: "glm-model", sources: ["paper1"], settings: { temperature: 0 } };
  const first = gate.start(choice), old = deferred<string>();
  let display = "";
  const publish = async (ticket: typeof first, response: Promise<string>) => { try { const value = await response; if (ticket.isCurrent()) display = value; } catch { if (ticket.isCurrent()) display = "error"; } };
  const firstPending = publish(first, old.promise);
  choice.provider = "deepseek"; choice.model = "deepseek-default"; choice.sources.push("paper2");
  const second = gate.start(choice);
  await publish(second, Promise.resolve("deepseek result"));
  old.resolve("late glm result"); await firstPending;
  expect(display).toBe("deepseek result");
  expect(first.settings).toEqual({provider:"glm", model:"glm-model", sources:["paper1"], settings:{temperature:0}});
  await publish(first, Promise.reject(new Error("late failure")));
  expect(display).toBe("deepseek result");
});

it("rechecks after delayed source verification and invalidates on leaving a project", async () => {
  const gate = new LatestRequest();
  const first = gate.start({project:"one"}), verification = deferred<void>();
  let committed = false;
  const pending = (async () => { await verification.promise; if (first.isCurrent()) committed = true; })();
  gate.invalidate(); gate.start({project:"one"});
  verification.resolve(); await pending;
  expect(committed).toBe(false);
});
