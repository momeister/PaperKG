/** Browser input benchmark against a saved session; backend writes are intercepted. */
import { createRequire } from 'node:module';
import fs from 'node:fs';
const require = createRequire(new URL('../frontend/package.json', import.meta.url));
const { chromium } = require('@playwright/test');
const [sessionPath, outputPath = '/tmp/workspace-input.json'] = process.argv.slice(2);
if (!sessionPath) throw new Error('Usage: node scripts/benchmark_workspace_input.mjs SESSION.json [OUTPUT.json]');
const stored = JSON.parse(fs.readFileSync(sessionPath, 'utf8'));
const project = stored.project_id;
const session = stored.payload;
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [], checks = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', async route => {
    const req = route.request(); const url = new URL(req.url());
    if (url.pathname.includes('claim-check')) checks.push(url.pathname);
    if (url.pathname.includes('/workspace/sessions/')) {
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(stored) });
    }
    if (!['GET', 'HEAD'].includes(req.method())) return route.fulfill({ contentType: 'application/json', body: '{}' });
    return route.continue();
  });
  await page.addInitScript(({ project, session }) => {
    window.__API_BASE__ = 'http://127.0.0.1:42849';
    localStorage.setItem('sciencekg.project', project);
    localStorage.setItem('sciencekg.assistant.session.' + project, JSON.stringify(session));
    localStorage.setItem('sciencekg.workspace.mode.' + project, 'research');
  }, { project, session });
  await page.goto('http://127.0.0.1:5173/#/workspace');
  const input = page.getByPlaceholder('Frage stellen — / für Befehle, @ für Papers');
  await input.waitFor({ timeout: 30000 });
  await page.locator('.answer-text-content').first().waitFor({ timeout: 30000 });
  await page.waitForTimeout(1500);
  await input.focus();
  await page.evaluate(() => {
    window.__inputTimes = [];
    document.addEventListener('input', e => {
      if (!(e.target instanceof HTMLInputElement) || !e.target.placeholder.startsWith('Frage stellen')) return;
      const started = performance.now();
      requestAnimationFrame(() => requestAnimationFrame(() => window.__inputTimes.push(performance.now() - started)));
    });
  });
  await input.pressSequentially('Wie unterscheiden sich RA und PC? Bitte erläutere die Schwellen und die Weber-Fraktion.', { delay: 45 });
  await page.waitForTimeout(100);
  const times = await page.evaluate(() => window.__inputTimes);
  times.sort((a,b) => a-b);
  const result = { samples: times.length, median_ms: times[Math.floor(times.length*.5)], p95_ms: times[Math.floor(times.length*.95)], maximum_ms: times.at(-1), evidence_count: session.history[0]?.answer?.evidence?.length, errors, automatic_checks: checks, metric: 'input event to second requestAnimationFrame (paint opportunity)', browser: await browser.version() };
  await page.screenshot({ path: outputPath.replace(/\.json$/, '.png') });
  fs.writeFileSync(outputPath, JSON.stringify(result, null, 2));
  console.log(result);
  if (errors.length || checks.length || result.p95_ms >= 50) process.exitCode = 1;
} finally { await browser.close(); }
