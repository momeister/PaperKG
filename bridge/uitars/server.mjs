// Optional local bridge for PaperKG's Parallel-mode "An Desktop-Agent übergeben" (Kanal B).
//
// PaperKG compiles a Parallel-Research variant into a task brief and POSTs it here as
// { "task": "<plain-text brief>" }. This process runs the brief on THIS machine with the
// UI-TARS GUI agent (@ui-tars/sdk + NutJS operator): it screenshots the screen, reasons
// with a local VLM (UI-TARS-1.5-7B served by LM Studio / Ollama), and drives mouse/keyboard.
// Progress is streamed back as Server-Sent Events that PaperKG appends to the variant.
//
// ⚠️  This actually controls your computer. Run it only on your own machine, watch it, and
//     keep UI-TARS-Desktop or this terminal in reach to stop it. PaperKG never drives the
//     machine itself — it only hands over the text brief. The manual channel (Kanal A:
//     copy the brief into UI-TARS-Desktop) needs none of this.
//
// Env vars (all optional):
//   BRIDGE_PORT   default 8787      — must match agent_bridge.url in config.yaml
//   VLM_BASE_URL  default http://127.0.0.1:1234/v1   (LM Studio; Ollama: http://127.0.0.1:11434/v1)
//   VLM_API_KEY   default "lm-studio"
//   VLM_MODEL     default "ui-tars-1.5-7b"
//
// NOTE: this is a small reference implementation against the documented @ui-tars/sdk API
// (https://github.com/bytedance/UI-TARS-desktop/blob/main/docs/sdk.md). Run `npm install`
// here first; if the installed SDK major version renames an export, adjust the imports below.

import http from 'node:http';
import { GUIAgent } from '@ui-tars/sdk';
import { NutJSOperator } from '@ui-tars/operator-nut-js';

const PORT = process.env.BRIDGE_PORT ? Number(process.env.BRIDGE_PORT) : 8787;
const VLM_BASE_URL = process.env.VLM_BASE_URL || 'http://127.0.0.1:1234/v1';
const VLM_API_KEY = process.env.VLM_API_KEY || 'lm-studio';
const VLM_MODEL = process.env.VLM_MODEL || 'ui-tars-1.5-7b';

function sse(res, payload) {
  res.write(`data: ${JSON.stringify(payload)}\n\n`);
}

const server = http.createServer(async (req, res) => {
  if (req.method === 'GET' && req.url === '/health') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true, model: VLM_MODEL, vlm: VLM_BASE_URL }));
    return;
  }
  if (req.method !== 'POST' || req.url !== '/run') {
    res.writeHead(404, { 'Content-Type': 'text/plain' });
    res.end('not found');
    return;
  }

  let body = '';
  for await (const chunk of req) body += chunk;
  let task = '';
  try {
    task = String((JSON.parse(body || '{}').task ?? '')).trim();
  } catch {
    task = '';
  }
  if (!task) {
    res.writeHead(400, { 'Content-Type': 'text/plain' });
    res.end('missing task');
    return;
  }

  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache',
    Connection: 'keep-alive',
  });
  sse(res, { status: 'started', model: VLM_MODEL });

  const agent = new GUIAgent({
    model: { baseURL: VLM_BASE_URL, apiKey: VLM_API_KEY, model: VLM_MODEL },
    operator: new NutJSOperator(),
    onData: ({ data }) => {
      try {
        const last = data?.conversations?.at?.(-1);
        sse(res, { status: 'step', from: last?.from ?? null, value: last?.value ?? null });
      } catch {
        /* never let a reporting error abort the run */
      }
    },
    onError: ({ error }) => sse(res, { status: 'error', error: String(error?.message ?? error) }),
  });

  try {
    await agent.run(task);
    sse(res, { status: 'done' });
  } catch (err) {
    sse(res, { status: 'error', error: String(err?.message ?? err) });
  } finally {
    res.end();
  }
});

server.listen(PORT, '127.0.0.1', () => {
  console.log(
    `[paperkg-uitars-bridge] listening on http://127.0.0.1:${PORT}  ` +
      `(model=${VLM_MODEL}, vlm=${VLM_BASE_URL})`,
  );
});
