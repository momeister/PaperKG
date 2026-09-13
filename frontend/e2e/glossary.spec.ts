import { expect, test, type Page } from "@playwright/test";

function fixturePdf(rotation = 0, imageOnly = false) {
  const streams = [
    "BT /F1 14 Tf 30 540 Td (AI wide WWW iii AI) Tj 0 -40 Td (inter-) Tj 0 -20 Td (vention AI) Tj ET",
    "BT /F1 14 Tf 30 450 Td (Second page AI citation) Tj ET"
  ].map(s => imageOnly ? "" : s);
  const objects = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R 6 0 R] /Count 2 >>", `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 500 600] /Rotate ${rotation} /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>`, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>", `<< /Length ${streams[0].length} >>\nstream\n${streams[0]}\nendstream`, "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 500 600] /Resources << /Font << /F1 4 0 R >> >> /Contents 7 0 R >>", `<< /Length ${streams[1].length} >>\nstream\n${streams[1]}\nendstream`];
  let body = "%PDF-1.4\n";
  const offsets = objects.map((o, i) => { const pos = body.length; body += `${i + 1} 0 obj\n${o}\nendobj\n`; return pos; });
  const xref = body.length;
  body += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n${offsets.map(o => `${String(o).padStart(10, "0")} 00000 n \n`).join("")}trailer\n<< /Root 1 0 R /Size ${objects.length + 1} >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(body);
}

async function setup(page: Page) {
  let entries: any[] = [];
  let llmCalls = 0;
  await page.route(url => url.pathname === '/glossary' || url.pathname.startsWith('/glossary/'), async route => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    const payload = request.postDataJSON();
    let result: unknown;
    if (pathname.endsWith('/suggest')) { llmCalls++; result = { suggestion: 'Suggested explanation' }; }
    else if (request.method() === 'GET') result = { items: entries };
    else if (request.method() === 'POST') {
      const entry = { id: crypto.randomUUID(), ...payload, term_key: payload.term.toLowerCase(), created_at: '2026-01-01', updated_at: '2026-01-01' };
      entries.push(entry); result = entry;
    } else if (request.method() === 'PATCH') {
      const entry = entries.find(e => pathname.endsWith(e.id)); Object.assign(entry, payload); result = entry;
    } else { entries = entries.filter(e => !pathname.endsWith(e.id)); result = { deleted: true }; }
    await route.fulfill({ json: result });
  });
  await page.route('**/reading-fixture.pdf', route => route.fulfill({ contentType: 'application/pdf', body: fixturePdf() }));
  await page.route('**/glossary-test', route => route.fulfill({ contentType: 'text/html', body: `<!doctype html><div id="root"></div><script type="module">
    import RefreshRuntime from '/@react-refresh';
    RefreshRuntime.injectIntoGlobalHook(window); window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => type => type; window.__vite_plugin_react_preamble_installed__ = true;
    await import('/e2e/fixtures/glossaryHarness.tsx');
  </script>` }));
  await page.goto('/glossary-test');
  await expect(page.locator('.pdf-text-layer span').first()).toBeAttached();
  return { llmCalls: () => llmCalls };
}

async function selectPdf(page: Page) {
  await page.evaluate(() => {
    const span = document.querySelector('.pdf-text-layer span')!;
    const range = document.createRange(); range.setStart(span.firstChild!, 0); range.setEnd(span.firstChild!, 2);
    window.getSelection()!.removeAllRanges(); window.getSelection()!.addRange(range);
    span.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
  });
}

test('global terms from PDF and notes persist across project switches and reload without editing notes', async ({ page }) => {

  const state = await setup(page);
  const editor = page.getByPlaceholder('Markdown schreiben');
  await expect(editor).toHaveValue(/AI and neural network/);
  const initial = await editor.inputValue();
  await selectPdf(page);
  await page.getByRole('region', { name: 'PDF-Auswahl' }).getByText('Zum Wörterbuch hinzufügen').click();
  await expect(page.getByLabel('Begriff', { exact: true })).toHaveValue('AI');
  await expect(page.getByLabel('Erklärung', { exact: true })).toHaveValue('');
  await page.getByLabel('Erklärung', { exact: true }).fill('Artificial intelligence');
  await page.getByRole('button', { name: 'Speichern', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(editor).toHaveValue(initial);
  await expect(page.locator('.glossary-editor-hit')).toHaveCount(2);
  expect(state.llmCalls()).toBe(0);

  await page.getByLabel('Testprojekt').selectOption('B');
  await expect(editor).toHaveValue(initial);
  await expect(page.locator('.glossary-editor-hit')).toHaveCount(2);
  await editor.focus();
  await editor.press('Control+Home');
  await editor.press('ArrowRight');
  await expect(page.getByRole('tooltip')).toContainText('Artificial intelligence');
  await editor.press('Escape');
  await expect(page.getByRole('tooltip')).toHaveCount(0);

  // Select a second term in the native textarea.
  await editor.press('Control+Home');
  for (let i = 0; i < 7; i++) await editor.press('ArrowRight');
  await editor.press('Shift+End');
  await editor.press('Shift+ArrowLeft');
  await page.locator('.glossary-selection-action').click();
  await expect(page.getByLabel('Begriff', { exact: true })).toHaveValue('neural network');
  await page.getByLabel('Erklärung', { exact: true }).fill('A connected model');
  await page.getByRole('button', { name: 'Speichern', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(editor).toHaveValue(initial);
  await page.getByRole('button', { name: 'Preview', exact: true }).click();
  await expect(page.locator('.markdown-preview .glossary-term')).toHaveCount(3);
  const term = page.locator('.markdown-preview .glossary-term').first();
  await term.hover();
  await expect(page.getByRole('tooltip')).toContainText('Artificial intelligence');
  await term.focus(); await page.keyboard.press('Escape');
  await expect(page.getByRole('tooltip')).toHaveCount(0);
  await page.getByLabel('Begriffshinweise anzeigen').uncheck();
  await expect(page.locator('.glossary-term')).toHaveCount(0);
  await page.reload();
  await expect(page.getByLabel('Begriffshinweise anzeigen')).not.toBeChecked();
  await page.getByLabel('Begriffshinweise anzeigen').check();
  await page.getByRole('button', { name: 'Preview', exact: true }).click();
  await expect(page.locator('.markdown-preview .glossary-term')).toHaveCount(3);
  await page.getByRole('button', { name: /Wörterbuch \(2\)/ }).click();
  await page.getByLabel('Wörterbuch durchsuchen').fill('neural');
  await page.getByRole('button', { name: 'Bearbeiten', exact: true }).click();
  await page.getByLabel('Erklärung', { exact: true }).fill('Updated meaning');
  await page.getByRole('button', { name: 'Speichern', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.locator('.glossary-term').filter({ hasText: 'neural network' }).focus();
  await expect(page.getByRole('tooltip')).toContainText('Updated meaning');
  await page.screenshot({ path: 'test-results/glossary-preview.png', fullPage: true });
  expect(state.llmCalls()).toBe(0);
});

test('preview selection and block editing keep glossary actions independent of note text', async ({ page }) => {
  await setup(page);
  await page.getByRole('button', { name: 'Preview', exact: true }).click();
  await page.locator('.markdown-preview p').first().evaluate(node => {
    const range = document.createRange(); range.selectNodeContents(node);
    window.getSelection()!.removeAllRanges(); window.getSelection()!.addRange(range);
    node.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
  });
  await page.locator('.glossary-selection-action').click();
  await expect(page.getByLabel('Begriff', { exact: true })).toHaveValue('AI and neural network.');
  await page.getByLabel('Begriff', { exact: true }).fill('AI');
  await page.getByLabel('Erklärung', { exact: true }).fill('Meaning');
  await page.getByRole('button', { name: 'Speichern', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  const block = page.locator('.editable-preview-block').first();
  await block.focus(); await page.keyboard.press('F2');
  const editor = page.locator('.preview-block-editor');
  await expect(editor).toHaveValue('AI and neural network.');
  await expect(page.locator('.glossary-block-editor .glossary-editor-hit')).toHaveCount(1);
  await editor.press('Control+Home');
  await editor.press('ArrowRight');
  await expect(page.getByRole('tooltip')).toContainText('Meaning');
  await page.keyboard.press('Escape');
  await expect(editor).toBeVisible();
  await editor.fill('AI remains editable.');
  await editor.press('Control+Enter');
  await expect(page.locator('.markdown-preview')).toContainText('AI remains editable.');
  await page.getByRole('button', { name: 'Edit', exact: true }).click();
  await expect(page.getByPlaceholder('Markdown schreiben')).toHaveValue('AI remains editable.\n\nSecond paragraph with AI.');
});

test('editor hover follows scrolling and resizing; deletion removes every hint', async ({ page }) => {
  await setup(page);
  await page.getByRole('button', { name: /Wörterbuch/ }).click();
  await page.getByRole('button', { name: 'Begriff anlegen', exact: true }).click();
  await page.getByLabel('Begriff', { exact: true }).fill('AI');
  await page.getByLabel('Erklärung', { exact: true }).fill('Hover meaning');
  await page.getByRole('button', { name: 'Speichern', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByRole('button', { name: /Wörterbuch/ }).click();
  const editor = page.getByPlaceholder('Markdown schreiben');
  const content = Array.from({ length: 80 }, (_, index) => `Line ${index}: AI remains selectable.`).join('\n');
  await editor.fill(content);
  await editor.evaluate((node: HTMLTextAreaElement) => { node.scrollTop = 220; node.dispatchEvent(new Event('scroll', { bubbles: true })); });
  await page.setViewportSize({ width: 1100, height: 850 });
  const hitPoint = await page.locator('.markdown-editor-wrap').evaluate(wrap => {
    const editor = wrap.querySelector('textarea')!.getBoundingClientRect();
    const rect = Array.from(wrap.querySelectorAll('.glossary-editor-hit')).map(node => node.getBoundingClientRect()).find(rect => rect.top > editor.top + 30 && rect.bottom < editor.bottom - 30)!;
    return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
  });
  await page.mouse.move(hitPoint.x, hitPoint.y);
  await expect(page.getByRole('tooltip')).toContainText('Hover meaning');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('tooltip')).toHaveCount(0);
  await expect(editor).toHaveValue(content);
  await page.getByRole('button', { name: /Wörterbuch/ }).click();
  await page.getByRole('button', { name: 'Löschen', exact: true }).click();
  await page.getByRole('button', { name: 'Löschen bestätigen', exact: true }).click();
  await expect(page.locator('.glossary-editor-hit')).toHaveCount(0);
  await expect(editor).toHaveValue(content);
  await page.getByRole('button', { name: 'Preview', exact: true }).click();
  await expect(page.locator('.glossary-term')).toHaveCount(0);
});
