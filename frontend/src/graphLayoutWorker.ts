import { computeForceLayout } from "./forceLayout";
import type { GraphEdge, GraphNode, Point } from "./types";

// `lib` in tsconfig.json is DOM-only (no "webworker"), so the ambient `self`
// from lib.dom.d.ts (Window) doesn't have the Worker-scope members we need.
// Shadow it locally with the exact shape this module relies on — module-scoped
// because this file has imports/exports, so it doesn't leak into the rest of
// the program or collide with the DOM lib's `self`.
declare const self: {
  onmessage: ((event: MessageEvent<{ nodes: GraphNode[]; edges: GraphEdge[] }>) => void) | null;
  postMessage: (message: { positions: Array<[string, Point]> }) => void;
};

self.onmessage = (event) => {
  const { nodes, edges } = event.data;
  const layout = computeForceLayout(nodes, edges);
  self.postMessage({ positions: Array.from(layout.entries()) });
};
