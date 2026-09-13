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

async function setup(page: Page, rotation = 0, imageOnly = false) {
  await page.route("**/reading-fixture.pdf", route => route.fulfill({ contentType: "application/pdf", body: fixturePdf(rotation, imageOnly) }));
  await page.route("**/reading-test", route => route.fulfill({ contentType: "text/html", body: `<!doctype html><div id="root"></div><script type="module">
    import RefreshRuntime from '/@react-refresh';
    RefreshRuntime.injectIntoGlobalHook(window); window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => type => type; window.__vite_plugin_react_preamble_installed__ = true;
    await import('/e2e/fixtures/readingHarness.tsx');
  </script>` }));
  page.on("pageerror", error => console.log("Reading page error:", error.message));
  await page.goto('/reading-test');
  if (!imageOnly) await expect(page.locator('.pdf-text-layer span').first()).toBeAttached();
  await page.getByRole('button', { name: 'Suche', exact: true }).click();
}

async function selectText(page: Page, crossPage = false) {
  await page.evaluate(cross => {
    const spans = Array.from(document.querySelectorAll('.pdf-text-layer span')).filter(n => n.firstChild?.nodeType === Node.TEXT_NODE);
    const range = document.createRange();
    range.setStart(spans[0].firstChild!, 0);
    const end = cross ? spans[spans.length - 1] : spans[0];
    range.setEnd(end.firstChild!, end.textContent!.length);
    window.getSelection()!.removeAllRanges(); window.getSelection()!.addRange(range);
    end.dispatchEvent(new MouseEvent('mouseup', { bubbles:true }));
  }, crossPage);
  await expect(page.getByRole('region', {name:'PDF-Auswahl'})).toBeVisible();
}

test('individual PDF occurrences, navigation, zoom and image-PDF status', async ({ page }) => {
  await setup(page);
  const search = page.getByPlaceholder('In PDF suchen');
  await search.fill('AI');
  await expect(page.locator('.pdf-search-row')).toContainText('1 / 4 Treffer');
  await search.press('Enter');
  await expect(page.locator('.pdf-search-row')).toContainText('2 / 4 Treffer');
  await search.press('Shift+Enter');
  await expect(page.locator('.pdf-search-row')).toContainText('1 / 4 Treffer');
  await page.locator('.pdf-search-results button').last().click();
  await expect(page.locator('.pdf-search-row')).toContainText('4 / 4 Treffer');
  await search.fill('iii');
  await expect(page.locator('.pdf-highlight--search')).toHaveCount(1);
  const width = await page.locator('.pdf-highlight--search').evaluate(n => n.getBoundingClientRect().width);
  await search.fill('WWW');
  await expect.poll(() => page.locator('.pdf-highlight--search').evaluate(n => n.getBoundingClientRect().width)).toBeGreaterThan(width * 2);
  await page.getByRole('button', {name:'Vergroessern', exact:true}).click();
  await expect(page.locator('.pdf-search-row')).toContainText('1 / 1 Treffer');
  await search.fill('intervention');
  await expect(page.locator('.pdf-search-row')).toContainText('1 / 1 Treffer');
});

test('selection survives writing, translates, saves exact anchors and reopens after reload', async ({ page }) => {
  await setup(page);
  await selectText(page, true);
  await page.evaluate(() => (window as any).createReadingNote());
  const editor = page.getByPlaceholder('Markdown schreiben');
  await editor.fill('Meine Notiz\n\n');
  await editor.press('End');
  await expect(page.locator('.pdf-highlight--selection')).toHaveCount(4);
  await page.getByRole('region', {name:'PDF-Auswahl'}).getByRole('button', {name:'Übersetzen', exact:true}).click();
  await expect(page.getByRole('region', {name:'PDF-Auswahl'})).toContainText('Deutsche Übersetzung');
  await page.evaluate(() => { (window as any).failSave = true; });
  await page.getByRole('button', {name:'Übersetzung als Zitat einfügen'}).click();
  await expect(page.getByRole('alert')).toContainText('Speicherfehler');
  await expect(page.locator('.pdf-highlight--selection')).toHaveCount(4);
  await page.evaluate(() => { (window as any).failSave = false; });
  await page.getByRole('button', {name:'Übersetzung als Zitat einfügen'}).click();
  await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('reading.note') || '{}').citations?.length)).toBe(1);
  const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('reading.note')!));
  expect(stored.markdown).toContain('Übersetzung (Deutsch)');
  expect(stored.citations[0].pdf_anchors).toHaveLength(2);
  expect(stored.citations[0].reference_text).toContain('Second page AI citation');
  expect(stored.citations[0].reference_text).not.toContain('Seite 2');
  await page.reload();
  await expect(editor).toHaveValue(/Deutsche Übersetzung/);
  await page.getByRole('button', {name:'Preview', exact:true}).click();
  await page.locator('.citation-link').first().click();
  await expect(page.locator('.pdf-highlight--anchor')).toHaveCount(4);
  await selectText(page);
  await page.keyboard.press('Escape');
  await expect(page.getByRole('region', {name:'PDF-Auswahl'})).toHaveCount(0);
});

test('rotated page uses measured glyph rectangles', async ({ page }) => {
  await setup(page, 90);
  await page.getByPlaceholder('In PDF suchen').fill('WWW');
  const mark = page.locator('.pdf-highlight--search');
  await expect(mark).toHaveCount(1);
  const rect = await mark.boundingBox();
  expect(rect!.height).toBeGreaterThan(rect!.width);
});

test('image-only PDF clearly reports missing text layer', async ({ page }) => {
  // No text spans exist, so do not use setup's text-ready wait.
  await setup(page, 0, true);
  await page.getByPlaceholder('In PDF suchen').fill('AI');
  await expect(page.locator('.pdf-search-results')).toContainText('keine durchsuchbare Textschicht');
});

test('citation creates a note when no note is active', async ({ page }) => {
  await setup(page);
  await selectText(page);
  await page.getByRole('button', {name:'Zitat in Notiz einfügen', exact:true}).click();
  await expect(page.getByPlaceholder('Markdown schreiben')).toHaveValue(/AI wide WWW iii AI/);
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('reading.note')!).citations[0].pdf_anchors)).toHaveLength(1);
});

test('writing line follows manual scroll, cursor navigation, wrapping and resize', async ({ page }) => {
  await setup(page);
  await page.evaluate(() => (window as any).createReadingNote());
  const editor = page.getByPlaceholder('Markdown schreiben');
  await editor.fill(Array.from({length: 60}, (_, i) => `Zeile ${i}: Ein Satz mit Text für den Schreibmodus.`).join('\n'));
  await page.getByRole('button', {name:'Schreibmodus', exact:true}).click();
  const place = async (line: number) => editor.evaluate((element, line) => {
    const node = element as HTMLTextAreaElement;
    node.focus();
    const position = node.value.indexOf(`Zeile ${line}:`);
    node.setSelectionRange(position, position);
    const caret = (window as any).caretTop(node);
    node.scrollTop = caret.top - 110;
  }, line);
  const y = () => editor.evaluate(node => (window as any).caretTop(node).top - (node as HTMLTextAreaElement).scrollTop);
  await place(40);
  const before = await y();
  await editor.press('Enter');
  await expect.poll(y).toBeGreaterThan(before - 3);
  await expect.poll(y).toBeLessThan(before + 3);
  await editor.evaluate(n => { n.scrollTop -= 45; });
  const scrolled = await y();
  await editor.pressSequentially('weitere Wörter '.repeat(15));
  await expect.poll(y).toBeGreaterThan(scrolled - 3);
  await expect.poll(y).toBeLessThan(scrolled + 3);
  await editor.press('ArrowDown');
  const moved = await y();
  await editor.press('Enter');
  await expect.poll(y).toBeLessThan(moved + 3);
  await page.setViewportSize({width: 1100, height: 800});
  await place(30);
  const resized = await y();
  await editor.press('Enter');
  await expect.poll(y).toBeLessThan(resized + 3);
  await editor.evaluate(n => { n.scrollTop = 0; });
  await editor.press('Enter');
  await expect.poll(y).toBeLessThan(await editor.evaluate(n => n.clientHeight));
});

test('excerpt height and collapsed header survive reload and remain keyboard reachable', async ({ page }) => {
  await setup(page);
  const header = page.getByRole('button', {name:/Aktive Textstelle/});
  const grip = page.getByRole('separator', {name:'Aktive Textstelle: Höhe anpassen'});
  await expect(header).toHaveAttribute('aria-expanded', 'false');
  await header.click();
  await grip.focus();
  await grip.press('ArrowDown');
  await expect(grip).toHaveAttribute('aria-valuenow', '176');
  await header.click();
  await expect(header).toHaveAttribute('aria-expanded', 'false');
  await page.reload();
  await expect(header).toHaveAttribute('aria-expanded', 'false');
  await header.click();
  await expect(grip).toHaveAttribute('aria-valuenow', '176');
  await expect(page.locator('.pdf-text-layer span').first()).toBeAttached();
  await expect(grip).toBeInViewport();
  await page.screenshot({path:'/tmp/paperkg-reading-ui.png'});
});

test('citation geometry stays aligned under the global UI zoom', async ({ page }) => {
  await setup(page);
  await page.evaluate(() => { document.documentElement.style.zoom = '1.25'; });
  await expect.poll(async () => page.locator('.pdf-page').first().evaluate(page => {
    const span = page.querySelector('.pdf-text-layer span')!;
    const range = document.createRange(); range.selectNodeContents(span);
    const text = range.getBoundingClientRect();
    const mark = page.querySelector('.pdf-highlight--active')?.getBoundingClientRect();
    return mark ? Math.max(Math.abs(mark.left - text.left), Math.abs(mark.top - text.top), Math.abs(mark.width - text.width)) : 1000;
  })).toBeLessThan(2);
});
