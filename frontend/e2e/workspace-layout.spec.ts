import { expect, test, type Page } from '@playwright/test';
import { windowPdf } from './fixtures/windowPdf';

const model = 'Very-long-research-model-name-with-large-context-and-extended-capabilities:cloud';
const excerpt = 'Window PDF search selection. '.repeat(100) + 'Ende der vollständigen Textstelle.';
const evidence = { paper_id: 'fixture', kind: 'quote', reference_text: 'Window PDF search selection', pdf_excerpt: excerpt, found_in_pdf_text: true, metadata: { page: 1 }, matched_terms: [] };
const source = { paper_id: 'fixture', title: 'Layout-PDF', pdf_url: '/layout.pdf', local_pdf_url: '/layout.pdf', has_local_pdf: true, pdf_available: true, evidence: [evidence] };
const answer = { question: 'Lange Antwort', answer: 'Ein Absatz zum Lesen.\n\n'.repeat(80) + 'Letzte Antwort vollständig erreichbar.', sources: [source], evidence: [], claims_version: 1 };
const turn = { id: 'layout-turn', question: 'Lange Antwort', answer, verification: [source], createdAt: '2026-09-12', blocks: [{ id: 'block-1', question: 'Lange Antwort', answer, verification: [source], createdAt: '2026-09-12' }] };
async function setup(page: Page, scale: number) {
  await page.addInitScript(({ scale, model, turn }) => {
    localStorage.setItem('sciencekg.fontScale', String(scale));
    localStorage.setItem('sciencekg.provider', 'ollama');
    localStorage.setItem('sciencekg.model', model);
    localStorage.setItem('sciencekg.providerRow.height', '9999');
    localStorage.setItem('sciencekg.pdf.excerpt.height', '9999');
    localStorage.setItem('sciencekg.assistant.session.__all_papers__', JSON.stringify({ history: [turn], activeTurnId: turn.id, savedAt: Date.now() }));
  }, { scale, model, turn });
  let note = { id: 'layout-note', project_id: '__all_papers__', title: 'Layout-Notiz', markdown: '# Notiz\n\n' + 'Langer Notiztext.\n\n'.repeat(60) + 'Notizende', citations: [], assets: [] };
  await page.route('**/layout.pdf', r => r.fulfill({ contentType: 'application/pdf', body: Buffer.from(windowPdf()) }));
  await page.route(url => url.port !== '5173', async route => {
    const path = new URL(route.request().url()).pathname;
    let json: unknown = { items: [], total: 0 };
    if (path === '/models/providers') json = { default_provider: 'ollama', providers: [{ name: 'ollama', provider_type: 'ollama', default_model: model, models: [model], settings: {} }] };
    else if (path.endsWith('/discover')) json = { models: [model] };
    else if (path === '/projects') json = { projects: [] };
    else if (path === '/system/health-report') json = { status: 'ok', warnings: ['Prüfhinweis zum Backend'] };
    else if (path.startsWith('/workspace/sessions/')) json = { payload: { history: [turn], activeTurnId: turn.id } };
    else if (path.endsWith('/notes')) json = { items: [note], total: 1 };
    else if (path === '/notes/layout-note') { if (route.request().method() === 'PATCH') note = { ...note, ...route.request().postDataJSON() }; json = { note }; }
    else if (path === '/papers') json = { items: [{ id: 'fixture', title: 'Layout-PDF', has_full_text: true }], total: 1 };
    else if (path.endsWith('/meta')) json = { paper_id: 'fixture', title: 'Layout-PDF', has_local_pdf: true };
    else if (path.endsWith('/annotations')) json = { annotations: [] };
    else if (path.endsWith('/pdf')) { await route.fulfill({ contentType: 'application/pdf', body: Buffer.from(windowPdf()) }); return; }
    await route.fulfill({ json });
  });
  await page.goto('/#/workspace');
  await expect(page.locator('.workspace-assistant-pane .answer-text')).toContainText('Letzte Antwort');
}

for (const [width, height] of [[1920, 1080], [1366, 768], [1024, 600]]) {
  for (const scale of [1, 1.25]) test(`compact workspace reachable at ${width}x${height}, ${scale * 100}%`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await setup(page, scale);
    const bar = page.locator('.topbar');
    expect((await bar.boundingBox())!.height).toBeLessThanOrEqual(48 * scale);
    const choice = bar.locator('.topbar-model-choice > button');
    await expect(choice).toContainText('nicht lokal');
    await choice.focus(); await page.keyboard.press('Enter');
    await expect(page.getByLabel('Temperatur', { exact: true })).toBeVisible();
    await page.getByLabel('Temperatur', { exact: true }).fill('0.35');
    await page.keyboard.press('Escape'); await expect(choice).toBeFocused();
    await bar.getByRole('button', { name: /1 Warnungen/ }).click();
    await expect(page.getByText('Prüfhinweis zum Backend', { exact: true })).toBeVisible();
    await page.keyboard.press('Escape');
    const overflow = bar.getByRole('button', { name: 'Mehr', exact: true });
    if (await overflow.isVisible()) await overflow.click();
    else await bar.getByRole('button', { name: 'Wörterbuch', exact: true }).click();
    await expect(page.getByLabel('Begriffshinweise anzeigen')).toBeVisible();
    await page.getByLabel('Begriffshinweise anzeigen').uncheck();
    await page.keyboard.press('Escape');
    if (await overflow.isVisible()) await overflow.click();
    else await bar.getByRole('button', { name: 'Darstellung', exact: true }).click();
    await expect(page.getByRole('radiogroup', { name: 'Farbschema' })).toBeVisible();
    await page.getByRole('button', { name: 'Schriftgröße zurücksetzen' }).scrollIntoViewIfNeeded();
    await page.keyboard.press('Escape');
    const options = page.getByRole('button', { name: 'Optionen', exact: true });
    await options.click();
    await page.getByLabel('Antwortlänge', { exact: true }).selectOption('ausführlich');
    await expect(page.getByLabel('Antwortlänge', { exact: true })).toHaveValue('ausführlich');
    await page.keyboard.press('Escape'); await expect(options).toBeFocused();
    const answers = page.locator('.workspace-answer-panel');
    await answers.evaluate(node => { node.scrollTop = node.scrollHeight; });
    await answers.scrollIntoViewIfNeeded();
    await expect.poll(() => page.locator('.answer-text').evaluate(node => {
      const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
      let last: Node | null = null; while (walker.nextNode()) last = walker.currentNode;
      if (!last) return false;
      const range = document.createRange(); range.setStart(last, Math.max(0, (last.textContent?.length ?? 0) - 10)); range.setEnd(last, last.textContent?.length ?? 0);
      const rect = range.getBoundingClientRect();
      const pane = node.closest('.workspace-answer-panel')!.getBoundingClientRect();
      return rect.bottom <= Math.min(window.innerHeight, pane.bottom) + 2 && rect.top >= Math.max(0, pane.top) - 2;
    })).toBe(true);
    const editor = page.getByPlaceholder('Markdown schreiben');
    await editor.fill('Notiz mit unterer Aktion');
    await editor.press('Control+End');
    await expect(editor).toBeInViewport();
    await page.getByRole('button', { name: 'PDFs', exact: false }).first().click();
    await page.locator('.workspace-paper-main').first().click();
    await expect(page.locator('.pdf-text-layer span').first()).toBeAttached();
    const controls = page.locator('.pdf-control-stack');
    await controls.getByRole('button', { name: 'Suche', exact: true }).click();
    await page.getByPlaceholder('In PDF suchen').fill('Window');
    await expect(page.locator('.pdf-search-row')).toContainText('6 Treffer');
    await page.getByRole('button', { name: 'Belege ein- oder ausklappen' }).click();
    await page.locator('.evidence-panel .source-row-title').first().click();
    await page.locator('.workspace-assistant-actions').getByRole('button', { name: 'PDF öffnen', exact: true }).click();
    const excerptHeader = page.getByRole('button', { name: /Aktive Textstelle/ });
    await expect(excerptHeader).toHaveAttribute('aria-expanded', 'true');
    const excerptBody = page.locator('.pdf-pane .resizable-section-body');
    expect((await excerptBody.boundingBox())!.height).toBeLessThan(400 * scale);
    await excerptBody.evaluate(node => { node.scrollTop = node.scrollHeight; });
    await excerptBody.scrollIntoViewIfNeeded();
    await expect(excerptBody).toBeInViewport();
    await expect(excerptBody).toContainText('Ende der vollständigen Textstelle.');
    await expect.poll(() => excerptBody.evaluate(node => Math.abs(node.scrollHeight - node.scrollTop - node.clientHeight))).toBeLessThan(2);
    await excerptHeader.click();
    await page.locator('.workspace-assistant-actions').getByRole('button', { name: 'PDF öffnen', exact: true }).click();
    await expect(excerptHeader).toHaveAttribute('aria-expanded', 'true');
    expect(await page.evaluate(() => localStorage.getItem('sciencekg.pdf.excerpt.height'))).toBe('9999');
    await page.screenshot({ path: `/tmp/paperkg-layout-${width}-${scale}.png` });
  });
}
