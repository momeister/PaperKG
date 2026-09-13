import { afterEach, expect, it } from "vitest";
import { captureView, restoreView } from "./viewState";

afterEach(() => { document.body.innerHTML = ""; });

it("rebuilds text selection from stable boundary nodes after document adoption", () => {
  const root = document.createElement("div");
  root.innerHTML = "<p>First passage</p><p>Second passage</p>";
  document.body.append(root);
  const start = root.firstChild!.firstChild!, end = root.lastChild!.firstChild!;
  const range = document.createRange(); range.setStart(start, 2); range.setEnd(end, 6);
  document.getSelection()!.addRange(range);
  const expected = document.getSelection()!.toString();
  const snapshot = captureView(root);
  const frame = document.createElement("iframe"); document.body.append(frame);
  const remote = frame.contentDocument!;
  remote.body.append(root);
  restoreView(snapshot, root);
  expect(remote.getSelection()!.toString()).toBe(expected);
  const returning = captureView(root);
  document.body.append(root); restoreView(returning, root);
  expect(document.getSelection()!.toString()).toBe(expected);
  expect(document.getSelection()!.getRangeAt(0).startContainer).toBe(start);
});
