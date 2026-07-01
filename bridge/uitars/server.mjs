// Optional local bridge for PaperKG's Desktop-Agent v2 (Selbst-Steuerung + Assistent).
//
// PaperKG compiles a Parallel-Research variant into a task brief and POSTs it here. This
// process runs on THIS machine and is the only thing that ever touches the screen/mouse/
// keyboard or takes screenshots — PaperKG itself stays text-only ("the brain"), this bridge
// is "the eyes/hands".
//
// Two modes, both reasoning with a local VLM (e.g. UI-TARS-1.5-7B served by LM Studio/Ollama):
//   - Selbst-Steuerung (POST /run): the UI-TARS GUI agent (@ui-tars/sdk + NutJS operator)
//     screenshots the screen, reasons, and drives mouse/keyboard to complete a task. Cancelable
//     via POST /cancel (AbortSignal) — PaperKG's Tauri shell also hard-kills this whole process
//     as a guaranteed fallback if a step doesn't honor the abort in time.
//   - Assistent (POST /observe/start|ask|stop): periodically screenshots the screen and asks
//     the VLM to describe it (no mouse/keyboard control), building a rolling context the user
//     can ask live questions against via POST /observe/ask. Screenshots are never written to
//     disk or returned to the caller — only short text descriptions leave this process.
//
// ⚠️  Selbst-Steuerung actually controls your computer. Run it only on your own machine, watch
//     it, and use Stop/Cancel to interrupt it. Assistent mode only reads the screen (no control)
//     but still captures everything visible — it's explicit opt-in and should be stopped when
//     done. PaperKG never drives the machine itself — it only hands over the text brief and
//     relays SSE events. The manual channel (Kanal A: copy the brief into UI-TARS-Desktop)
//     needs none of this.
//
// Env vars (all optional):
//   BRIDGE_PORT             default 8787      — must match agent_bridge.url in config.yaml
//   VLM_BASE_URL            default http://127.0.0.1:1234/v1   (LM Studio; Ollama: http://127.0.0.1:11434/v1)
//   VLM_API_KEY             default "lm-studio"
//   VLM_MODEL               default "ui-tars-1.5-7b"            — used for Selbst-Steuerung (action grounding)
//   HELPER_VLM_MODEL        default = VLM_MODEL                 — used for Assistent (freeform description/Q&A);
//                                                                  a general vision model often answers better here
//   OBSERVE_INTERVAL_MS     default 4000      — screenshot cadence for Assistent mode
//   OBSERVE_CONTEXT_SIZE    default 8         — how many rolling descriptions are kept per session
//
// NOTE: this is a small reference implementation against the documented @ui-tars/sdk API
// (https://github.com/bytedance/UI-TARS-desktop/blob/main/docs/sdk.md). Run `npm install`
// here first; if the installed SDK major version renames an export, adjust the imports below.

import http from 'node:http';
import crypto from 'node:crypto';
import { GUIAgent } from '@ui-tars/sdk';
import { NutJSOperator } from '@ui-tars/operator-nut-js';

const PORT = process.env.BRIDGE_PORT ? Number(process.env.BRIDGE_PORT) : 8787;
const VLM_BASE_URL = process.env.VLM_BASE_URL || 'http://127.0.0.1:1234/v1';
const VLM_API_KEY = process.env.VLM_API_KEY || 'lm-studio';
const VLM_MODEL = process.env.VLM_MODEL || 'ui-tars-1.5-7b';
const HELPER_VLM_MODEL = process.env.HELPER_VLM_MODEL || VLM_MODEL;
const OBSERVE_INTERVAL_MS = process.env.OBSERVE_INTERVAL_MS ? Number(process.env.OBSERVE_INTERVAL_MS) : 4000;
const OBSERVE_CONTEXT_SIZE = process.env.OBSERVE_CONTEXT_SIZE ? Number(process.env.OBSERVE_CONTEXT_SIZE) : 8;

function sse(res, payload) {
  res.write(`data: ${JSON.stringify(payload)}\n\n`);
}

async function readJsonBody(req) {
  let body = '';
  for await (const chunk of req) body += chunk;
  try {
    return JSON.parse(body || '{}');
  } catch {
    return {};
  }
}

/** One chat-completion call against the local, OpenAI-compatible VLM. */
async function callVLM(messages, { model } = {}) {
  const resp = await fetch(`${VLM_BASE_URL.replace(/\/$/, '')}/chat/completions`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${VLM_API_KEY}`,
    },
    body: JSON.stringify({
      model: model || HELPER_VLM_MODEL,
      messages,
      temperature: 0.2,
      max_tokens: 400,
    }),
  });
  if (!resp.ok) {
    throw new Error(`VLM request failed: ${resp.status} ${await resp.text().catch(() => '')}`);
  }
  const json = await resp.json();
  return String(json?.choices?.[0]?.message?.content ?? '').trim();
}

// Active Selbst-Steuerung runs, keyed by runId, so POST /cancel can abort them gracefully.
const runs = new Map();

// Active Assistent observation sessions, keyed by sessionId. Each holds its own operator
// (for screenshot()), a rolling description buffer, and the live SSE response/interval.
const observeSessions = new Map();

function stopObserveSession(sessionId) {
  const session = observeSessions.get(sessionId);
  if (!session) return false;
  if (session.timer) clearInterval(session.timer);
  observeSessions.delete(sessionId);
  try {
    session.res.end();
  } catch {
    /* response may already be closed by the client */
  }
  return true;
}

async function observeTick(sessionId) {
  const session = observeSessions.get(sessionId);
  if (!session) return;
  try {
    const shot = await session.operator.screenshot();
    const messages = [
      {
        role: 'system',
        content:
          'Du beobachtest live den Bildschirm eines Nutzers, der an einer Aufgabe arbeitet. ' +
          'Beschreibe in 1-2 knappen Sätzen, was gerade sichtbar ist (Anwendung, Tätigkeit, ' +
          'sichtbare Fehler/Probleme). Keine Spekulation über Inhalte außerhalb des Bildes.' +
          (session.primer ? `\n\nAufgabe:\n${session.primer}` : ''),
      },
      {
        role: 'user',
        content: [
          { type: 'image_url', image_url: { url: `data:image/png;base64,${shot.base64}` } },
          { type: 'text', text: 'Was ist gerade auf dem Bildschirm zu sehen?' },
        ],
      },
    ];
    const description = await callVLM(messages);
    const entry = { t: Date.now(), value: description };
    session.descriptions.push(entry);
    if (session.descriptions.length > OBSERVE_CONTEXT_SIZE) session.descriptions.shift();
    sse(session.res, { status: 'observation', value: description, t: entry.t });
  } catch (err) {
    sse(session.res, { status: 'error', error: String(err?.message ?? err) });
  }
}

const server = http.createServer(async (req, res) => {
  if (req.method === 'GET' && req.url === '/health') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true, model: VLM_MODEL, vlm: VLM_BASE_URL }));
    return;
  }

  if (req.method === 'POST' && req.url === '/run') {
    const body = await readJsonBody(req);
    const task = String(body.task ?? '').trim();
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

    const runId = crypto.randomUUID();
    const controller = new AbortController();
    runs.set(runId, controller);
    sse(res, { status: 'started', runId, model: VLM_MODEL });

    const agent = new GUIAgent({
      model: { baseURL: VLM_BASE_URL, apiKey: VLM_API_KEY, model: VLM_MODEL },
      operator: new NutJSOperator(),
      signal: controller.signal,
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
      if (controller.signal.aborted) {
        sse(res, { status: 'aborted' });
      } else {
        sse(res, { status: 'error', error: String(err?.message ?? err) });
      }
    } finally {
      runs.delete(runId);
      res.end();
    }
    return;
  }

  if (req.method === 'POST' && req.url === '/cancel') {
    const body = await readJsonBody(req);
    const runId = String(body.runId ?? '');
    const controller = runs.get(runId);
    res.writeHead(200, { 'Content-Type': 'application/json' });
    if (controller) {
      controller.abort();
      res.end(JSON.stringify({ ok: true }));
    } else {
      res.end(JSON.stringify({ ok: false, error: 'run not found' }));
    }
    return;
  }

  if (req.method === 'POST' && req.url === '/observe/start') {
    const body = await readJsonBody(req);
    const sessionId = String(body.sessionId ?? '') || crypto.randomUUID();
    const intervalMs = Number(body.intervalMs) > 0 ? Number(body.intervalMs) : OBSERVE_INTERVAL_MS;
    const primer = String(body.primer ?? '').trim();

    // Replace any previous session under the same id (e.g. a restart).
    stopObserveSession(sessionId);

    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive',
    });

    const session = { operator: new NutJSOperator(), primer, descriptions: [], timer: null, res };
    observeSessions.set(sessionId, session);
    sse(res, { status: 'started', sessionId });

    await observeTick(sessionId);
    session.timer = setInterval(() => void observeTick(sessionId), intervalMs);

    req.on('close', () => stopObserveSession(sessionId));
    return;
  }

  if (req.method === 'POST' && req.url === '/observe/ask') {
    const body = await readJsonBody(req);
    const sessionId = String(body.sessionId ?? '');
    const question = String(body.question ?? '').trim();
    const session = observeSessions.get(sessionId);
    if (!session || !question) {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'unknown session or missing question' }));
      return;
    }
    try {
      const context =
        session.descriptions.map((d) => `- ${d.value}`).join('\n') || '(noch keine Beobachtung)';
      let imageContent = [];
      try {
        const shot = await session.operator.screenshot();
        imageContent = [{ type: 'image_url', image_url: { url: `data:image/png;base64,${shot.base64}` } }];
      } catch {
        /* fall back to text-only context if a fresh screenshot fails */
      }
      const messages = [
        {
          role: 'system',
          content:
            'Du hilfst einem Nutzer live bei einer Aufgabe, indem du seinen Bildschirm beobachtest. ' +
            'Antworte kurz und konkret auf seine Frage, basierend auf dem aktuellen Bild und dem ' +
            'bisherigen Beobachtungsverlauf.' +
            (session.primer ? `\n\nAufgabe:\n${session.primer}` : '') +
            `\n\nBisherige Beobachtungen:\n${context}`,
        },
        { role: 'user', content: [...imageContent, { type: 'text', text: question }] },
      ];
      const answer = await callVLM(messages);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ answer }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: String(err?.message ?? err) }));
    }
    return;
  }

  if (req.method === 'POST' && req.url === '/observe/stop') {
    const body = await readJsonBody(req);
    const sessionId = String(body.sessionId ?? '');
    const stopped = stopObserveSession(sessionId);
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: stopped }));
    return;
  }

  res.writeHead(404, { 'Content-Type': 'text/plain' });
  res.end('not found');
});

server.listen(PORT, '127.0.0.1', () => {
  console.log(
    `[paperkg-uitars-bridge] listening on http://127.0.0.1:${PORT}  ` +
      `(model=${VLM_MODEL}, helper_model=${HELPER_VLM_MODEL}, vlm=${VLM_BASE_URL})`,
  );
});
