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
//   - Assistent "zeig mir" pointer (POST /observe/point): locates a UI element the user asks
//     about ("wo ist der Download-Button?") and returns a real screen point — reusing the same
//     click(start_box=...) grounding action grammar and `@ui-tars/sdk/core` coordinate math as
//     Selbst-Steuerung, but as a single lookup against VLM_MODEL (the UI-TARS-family grounding
//     model, not HELPER_VLM_MODEL — a general vision model isn't trained to emit usable click
//     coordinates). Nothing is executed: PaperKG's overlay only draws a highlight there.
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
import { GUIAgent, UITarsModelVersion } from '@ui-tars/sdk';
import { SYSTEM_PROMPT, SYSTEM_PROMPT_TEMPLATE, DEFAULT_FACTORS } from '@ui-tars/sdk/constants';
import { parseBoxToScreenCoords } from '@ui-tars/sdk/core';
import { NutJSOperator } from '@ui-tars/operator-nut-js';
import { actionParser } from '@ui-tars/action-parser';
import { Jimp } from 'jimp';

const PORT = process.env.BRIDGE_PORT ? Number(process.env.BRIDGE_PORT) : 8787;
const VLM_BASE_URL = process.env.VLM_BASE_URL || 'http://127.0.0.1:1234/v1';
const VLM_API_KEY = process.env.VLM_API_KEY || 'lm-studio';
const VLM_MODEL = process.env.VLM_MODEL || 'ui-tars-1.5-7b';
const HELPER_VLM_MODEL = process.env.HELPER_VLM_MODEL || VLM_MODEL;
const OBSERVE_INTERVAL_MS = process.env.OBSERVE_INTERVAL_MS ? Number(process.env.OBSERVE_INTERVAL_MS) : 4000;
const OBSERVE_CONTEXT_SIZE = process.env.OBSERVE_CONTEXT_SIZE ? Number(process.env.OBSERVE_CONTEXT_SIZE) : 8;

// @ui-tars/action-parser decodes a model's raw `start_box` numbers differently per UI-TARS
// generation: v1.0 emits coordinates on a fixed 1000x1000 grid (divide by DEFAULT_FACTORS);
// v1.5 emits them in its own "smart-resized" image-pixel space (divide by a screenshot-size-
// dependent factor instead — see actionParser's `smartResizeForV15`). Neither `GUIAgent` nor
// `actionParser` infer this from the model name — `uiTarsVersion`/`modelVer` must be passed in,
// or parsing silently defaults to v1.0 math even for a v1.5 model, i.e. wrong click/pointer
// coordinates. Detected from VLM_MODEL's name since config.yaml doesn't carry a version field.
const UI_TARS_VERSION = /1\.5/.test(VLM_MODEL) ? UITarsModelVersion.V1_5 : UITarsModelVersion.V1_0;

// UI-TARS-1.5 writes click coordinates in the pixel space of the image it actually
// *received* — not the native screenshot. When the raw screenshot is POSTed to an
// OpenAI-compatible VLM server (LM Studio, Ollama), that server silently downscales
// it to its own vision budget before the model sees it, so the model's numbers are in
// that smaller frame while `actionParser` would normalize them against the native
// size → coordinates compressed by a constant factor (measured ~0.6x on a 3440x1440
// screen). Fix: downscale the screenshot OURSELVES to a known size within a
// conservative budget the server won't shrink further, then normalize by that exact
// size. Budget defaults to 1280 tokens² of pixels (the common Qwen2.5-VL default,
// safely under LM Studio's observed cap); override via GROUNDING_MAX_PIXELS.
const IMAGE_FACTOR = 28;
const GROUNDING_MAX_PIXELS = process.env.GROUNDING_MAX_PIXELS
  ? Number(process.env.GROUNDING_MAX_PIXELS)
  : 1280 * IMAGE_FACTOR * IMAGE_FACTOR;
const GROUNDING_MIN_PIXELS = 100 * IMAGE_FACTOR * IMAGE_FACTOR;

/** Smart-resize (Qwen2.5-VL convention) to a width/height that are multiples of
 * IMAGE_FACTOR and whose area is within [MIN, MAX] pixels — the same math the VLM's
 * own image processor uses, so the picture we send is one it accepts without further
 * rescaling and the model's coordinates are in exactly this frame. */
function smartResizeToBudget(width, height) {
  const round28 = (n) => Math.round(n / IMAGE_FACTOR) * IMAGE_FACTOR;
  const floor28 = (n) => Math.floor(n / IMAGE_FACTOR) * IMAGE_FACTOR;
  const ceil28 = (n) => Math.ceil(n / IMAGE_FACTOR) * IMAGE_FACTOR;
  let wBar = Math.max(IMAGE_FACTOR, round28(width));
  let hBar = Math.max(IMAGE_FACTOR, round28(height));
  if (wBar * hBar > GROUNDING_MAX_PIXELS) {
    const beta = Math.sqrt((width * height) / GROUNDING_MAX_PIXELS);
    wBar = Math.max(IMAGE_FACTOR, floor28(width / beta));
    hBar = Math.max(IMAGE_FACTOR, floor28(height / beta));
  } else if (wBar * hBar < GROUNDING_MIN_PIXELS) {
    const beta = Math.sqrt(GROUNDING_MIN_PIXELS / (width * height));
    wBar = ceil28(width * beta);
    hBar = ceil28(height * beta);
  }
  return [wBar, hBar];
}

// UI-TARS models (Qwen-based) tend to write the `Thought` part in Chinese even though the SDK's
// default system prompt is English-only and gives no language instruction at all. This inserts a
// language directive *before* `## User Instruction` (where the SDK appends the task text), so it
// isn't buried after the actual instruction. It's a mitigation, not a guarantee — the base model
// can still lapse; a non-UI-TARS HELPER_VLM_MODEL (Assistent) follows language instructions far
// more reliably since it isn't specialized for GUI action grounding.
const SELF_STEERING_ACTION_SPACES = NutJSOperator.MANUAL?.ACTION_SPACES ?? [];
const SELF_STEERING_SYSTEM_PROMPT = (
  SELF_STEERING_ACTION_SPACES.length
    ? SYSTEM_PROMPT_TEMPLATE.replace('{{action_spaces_holder}}', SELF_STEERING_ACTION_SPACES.join('\n'))
    : SYSTEM_PROMPT
).replace(
  '## User Instruction',
  '## Language\nWrite the `Thought` text in German (Deutsch). Keep `Action:` lines in the exact ' +
    'English syntax shown above.\n\n## User Instruction',
);

// Assistent "zeig mir" pointer: a single-shot grounding call, not a loop — the action space is
// narrowed to just `click` (there is nothing to type/drag/scroll when only locating an element),
// and the model is told explicitly that it's locating, not actually clicking.
const POINTER_ACTION_SPACE = ["click(start_box='[x1, y1, x2, y2]')"];
const POINTER_SYSTEM_PROMPT = SYSTEM_PROMPT_TEMPLATE.replace(
  '{{action_spaces_holder}}',
  POINTER_ACTION_SPACE.join('\n'),
).replace(
  '## User Instruction',
  '## Language\nWrite the `Thought` text in German (Deutsch). Keep the `Action:` line in the exact ' +
    'English syntax shown above.\n\n## Hinweis\nDu klickst NICHT wirklich — du lokalisierst nur ein ' +
    'UI-Element und gibst dessen Bounding-Box als eine einzelne click-Aktion zurück. Ist das Element ' +
    'nicht sichtbar, wähle die wahrscheinlichste Stelle und sag das im Thought.\n\n## User Instruction',
);

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

/** Locate a natural-language element reference ("wo ist der Download-Button?") on the current
 * screen and return a real screen point — never dispatches mouse/keyboard input. Reuses
 * VLM_MODEL (the UI-TARS grounding model — HELPER_VLM_MODEL isn't trained to emit usable click
 * coordinates), `@ui-tars/action-parser`'s official `Thought:`/`Action: click(start_box=...)`
 * grammar, and `@ui-tars/sdk/core`'s `parseBoxToScreenCoords` — the exact same coordinate math
 * `@ui-tars/operator-nut-js`'s `execute()` uses for real clicks, so this doesn't invent a second
 * coordinate scheme. `width`/`height` (screenshot pixel size, same space the returned x/y are in)
 * are decoded the same way `GUIAgent` does internally, via `Jimp`. */
async function groundPoint(operator, question) {
  const shot = await operator.screenshot();
  const full = await Jimp.fromBuffer(Buffer.from(shot.base64, 'base64'));
  const nativeWidth = full.bitmap.width;
  const nativeHeight = full.bitmap.height;
  // Downscale to a size the VLM server won't shrink further, so the model's pixel
  // coordinates are in a frame we know exactly (see smartResizeToBudget). Skip the
  // work if the screenshot is already within budget.
  const [sentWidth, sentHeight] = smartResizeToBudget(nativeWidth, nativeHeight);
  const sentBase64 =
    sentWidth === nativeWidth && sentHeight === nativeHeight
      ? shot.base64
      : (await full.clone().resize({ w: sentWidth, h: sentHeight }).getBuffer('image/png')).toString('base64');
  const messages = [
    { role: 'system', content: POINTER_SYSTEM_PROMPT },
    {
      role: 'user',
      content: [
        { type: 'image_url', image_url: { url: `data:image/png;base64,${sentBase64}` } },
        { type: 'text', text: `Finde auf dem Bildschirm: ${question}` },
      ],
    },
  ];
  const prediction = await callVLM(messages, { model: VLM_MODEL });
  // Normalize the model's coordinates against the size we actually sent (sentWidth/
  // sentHeight are multiples of IMAGE_FACTOR and within budget, so actionParser's
  // smartResizeForV15 returns them unchanged) → a [0,1] box …
  const { parsed } = actionParser({
    prediction,
    factor: DEFAULT_FACTORS,
    screenContext: { width: sentWidth, height: sentHeight },
    modelVer: UI_TARS_VERSION,
  });
  const startBox = parsed?.[0]?.action_inputs?.start_box;
  if (!startBox) {
    console.error('[groundPoint] no box. sent=%dx%d ver=%s prediction=%j', sentWidth, sentHeight, UI_TARS_VERSION, prediction);
    throw new Error('Konnte kein passendes Element lokalisieren.');
  }
  // … then map that [0,1] box onto the native screen (parseBoxToScreenCoords cancels
  // its own factor, so native dims in → real screen pixels out).
  const { x, y } = parseBoxToScreenCoords({ boxStr: startBox, screenWidth: nativeWidth, screenHeight: nativeHeight });
  if (x == null || y == null) {
    throw new Error('Bounding-Box konnte nicht in Bildschirmkoordinaten umgerechnet werden.');
  }
  return { x, y, width: nativeWidth, height: nativeHeight, thought: parsed[0].thought || '' };
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
          'sichtbare Fehler/Probleme). Keine Spekulation über Inhalte außerhalb des Bildes. ' +
          'Antworte auf Deutsch.' +
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
      systemPrompt: SELF_STEERING_SYSTEM_PROMPT,
      uiTarsVersion: UI_TARS_VERSION,
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
    if (!question) {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'missing question' }));
      return;
    }
    // Stateless-capable: if no live observation session exists, answer against a fresh
    // one-shot screenshot (on-demand operator, no rolling context). This lets "Fragen"
    // work without first starting the periodic observation loop.
    const operator = session ? session.operator : new NutJSOperator();
    try {
      const context = session
        ? session.descriptions.map((d) => `- ${d.value}`).join('\n') || '(noch keine Beobachtung)'
        : '(keine laufende Beobachtung — Antwort nur aus dem aktuellen Bild)';
      let imageContent = [];
      try {
        const shot = await operator.screenshot();
        imageContent = [{ type: 'image_url', image_url: { url: `data:image/png;base64,${shot.base64}` } }];
      } catch {
        /* fall back to text-only context if a fresh screenshot fails */
      }
      const messages = [
        {
          role: 'system',
          content:
            'Du hilfst einem Nutzer live, der dir seinen Bildschirm zeigt. Beantworte seine Frage ' +
            'kurz, konkret und hilfreich, basierend auf dem aktuellen Bild und dem bisherigen ' +
            'Beobachtungsverlauf — auch wenn die Frage nicht direkt mit der ursprünglichen Aufgabe ' +
            'zusammenhängt (z.B. wo sich ein sichtbares UI-Element wie ein Button befindet). Lehne ' +
            'nur ab, wenn die Antwort weder aus dem Bild noch aus dem Beobachtungsverlauf ableitbar ' +
            'ist. Antworte auf Deutsch.' +
            (session?.primer
              ? `\n\nHintergrund (Aufgabe, an der der Nutzer arbeitet — nur zur Einordnung, keine ` +
                `Einschränkung deiner Antworten):\n${session.primer}`
              : '') +
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

  if (req.method === 'POST' && req.url === '/observe/point') {
    const body = await readJsonBody(req);
    const sessionId = String(body.sessionId ?? '');
    const question = String(body.question ?? '').trim();
    const session = observeSessions.get(sessionId);
    if (!question) {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'missing question' }));
      return;
    }
    // Stateless-capable (see /observe/ask): ground against an on-demand operator when no
    // live observation session exists, so "Zeigen" needs no prior Starten.
    const operator = session ? session.operator : new NutJSOperator();
    try {
      const point = await groundPoint(operator, question);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(point));
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
