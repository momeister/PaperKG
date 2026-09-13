import { windowPdf } from "./fixtures/windowPdf";
import { expect, test, type Page } from "@playwright/test";

async function setup(page: Page) {
  const pdf = windowPdf();
  await page.route("**/window-fixture.pdf", route => route.fulfill({ contentType: "application/pdf", body: Buffer.from(pdf) }));
  await page.route("**/window-tests", route => route.fulfill({ contentType: "text/html", body: `<!doctype html><div id="root"></div><script type="module">
    import RefreshRuntime from '/@react-refresh';
    RefreshRuntime.injectIntoGlobalHook(window); window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => type => type; window.__vite_plugin_react_preamble_installed__ = true;
    await import('/e2e/fixtures/workspaceWindowsHarness.tsx');
  </script>` }));
  await page.goto("/window-tests");
  await expect(page.getByPlaceholder("Markdown schreiben")).toHaveValue("Entwurf");
}
async function detach(page: Page, title: string) {
  const popup = page.waitForEvent("popup");
  await page.getByRole("button", { name: `${title} loslösen`, exact: true }).click();
  const child = await popup;
  await expect(child.getByRole("button", { name: `${title} andocken`, exact: true })).toBeVisible();
  return child;
}

test("all four views detach, stay active off-route, and repeatedly dock without remounting", async ({ page }) => {
  await setup(page);
  await page.getByPlaceholder("Navigator suchen").fill("Datensuche");
  await page.getByPlaceholder("Frage stellen", { exact: false }).fill("Assistant-Entwurf");
  await page.getByRole("button", { name: "Arbeit starten" }).click();
  const children: Page[] = [];
  for (const title of ["Navigator", "PDF · Daten · Analyse", "Assistant", "Notizen"]) children.push(await detach(page, title));
  await expect(children[0].getByPlaceholder("Navigator suchen")).toHaveValue("Datensuche");
  await expect(children[2].getByPlaceholder("Frage stellen", { exact: false })).toHaveValue("Assistant-Entwurf");
  await children[3].getByPlaceholder("Markdown schreiben").fill("Fenster-Entwurf");
  await page.getByRole("button", { name: "Seite wechseln" }).click();
  await page.evaluate(() => (window as any).finish());
  await expect(children[2].locator("output")).toHaveText("Antwort fertig");
  await expect.poll(() => page.evaluate(() => (window as any).saved.at(-1)?.markdown)).toBe("Fenster-Entwurf");
  await page.getByRole("button", { name: "Seite wechseln" }).click();
  for (let i = 0; i < children.length; i++) {
    const title = ["Navigator", "PDF · Daten · Analyse", "Assistant", "Notizen"][i];
    await children[i].getByRole("button", { name: `${title} andocken`, exact: true }).click();
    await expect(page.getByRole("button", { name: `${title} loslösen`, exact: true })).toBeVisible();
  }
  const again = await detach(page, "Assistant");
  await expect(again.locator("output")).toHaveText("Antwort fertig");
  await again.getByRole("button", { name: "Assistant andocken", exact: true }).click();
  expect(await page.evaluate(() => (window as any).work)).toEqual({ started: 1, completed: 1, mounts: 1 });
});

test("text selection and undo survive moving the editor between documents", async ({ page }) => {
  await setup(page);
  const editor = page.getByPlaceholder("Markdown schreiben");
  await editor.fill(""); await editor.pressSequentially("abc");
  await editor.evaluate((node: HTMLTextAreaElement) => { node.focus(); node.setSelectionRange(1, 2); });
  const child = await detach(page, "Notizen");
  const remote = child.getByPlaceholder("Markdown schreiben");
  expect(await remote.evaluate((node: HTMLTextAreaElement) => [node.selectionStart, node.selectionEnd])).toEqual([1, 2]);
  await remote.focus(); await remote.press("Control+z");
  await expect(remote).toHaveValue("ab");
  await child.getByRole("button", { name: "Notizen andocken", exact: true }).click();
  await editor.focus(); await editor.press("Control+Shift+z");
  await expect(editor).toHaveValue("abc");
});

test("window creation failure leaves the original editor usable", async ({ page }) => {
  await setup(page);
  await page.evaluate(() => { (window as any).failOpen = true; });
  await page.getByRole("button", { name: "Notizen loslösen", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("nicht geöffnet");
  await page.getByPlaceholder("Markdown schreiben").fill("Nach Fehler weitergeschrieben");
  await expect(page.getByPlaceholder("Markdown schreiben")).toHaveValue("Nach Fehler weitergeschrieben");
});

test("PDF search, selection and document stay live across a handoff", async ({ page }) => {
  await setup(page);
  await page.getByRole("button", { name: "Suche", exact: true }).click();
  const search = page.getByPlaceholder("In PDF suchen");
  await search.fill("Window");
  await expect(page.locator(".pdf-search-row")).toContainText("1 / 6 Treffer");
  await page.evaluate(() => {
    const span = document.querySelector(".pdf-text-layer span")!;
    const range = document.createRange(); range.selectNodeContents(span);
    window.getSelection()!.removeAllRanges(); window.getSelection()!.addRange(range);
    span.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
  });
  await expect(page.getByRole("region", { name: "PDF-Auswahl" })).toBeVisible();
  const child = await detach(page, "PDF · Daten · Analyse");
  await expect(child.getByPlaceholder("In PDF suchen")).toHaveValue("Window");
  await expect(child.getByRole("region", { name: "PDF-Auswahl" })).toBeVisible();
  await child.getByRole("button", { name: "PDF · Daten · Analyse andocken", exact: true }).click();
  await expect(search).toHaveValue("Window");
  await expect(page.getByRole("region", { name: "PDF-Auswahl" })).toBeVisible();
});

test("closing a related view returns its editor to the main window", async ({ page }) => {
  await setup(page);
  const child = await detach(page, "Notizen");
  await child.getByPlaceholder("Markdown schreiben").fill("Fenster geschlossen, Text bleibt");
  await child.close();
  await expect(page.getByPlaceholder("Markdown schreiben")).toHaveValue("Fenster geschlossen, Text bleibt");
  await expect(page.getByPlaceholder("Markdown schreiben")).toBeVisible();
});

test("only pane drags dock onto the target; file drops do not dock", async ({ page }) => {
  await setup(page);
  const child = await detach(page, "Notizen");
  const target = page.getByLabel("Notizen Andockfläche", { exact: true });
  const file = await page.evaluateHandle(() => {
    const data = new DataTransfer(); data.items.add(new File(["pdf"], "paper.pdf", { type: "application/pdf" })); return data;
  });
  await target.dispatchEvent("drop", { dataTransfer: file });
  await expect(child.getByPlaceholder("Markdown schreiben")).toBeVisible();
  const pane = await page.evaluateHandle(() => {
    const data = new DataTransfer(); data.setData("application/x-sciencekg-pane", "notes"); return data;
  });
  await target.dispatchEvent("drop", { dataTransfer: pane });
  await expect(page.getByPlaceholder("Markdown schreiben")).toBeVisible();
});

async function expectInk(page: Page, pageNumber: number) {
  const canvas = page.locator(`.pdf-page[data-page-number="${pageNumber}"] canvas`);
  await expect.poll(() => canvas.evaluate((node: HTMLCanvasElement) => {
    if (!node.width || !node.height) return 0;
    const pixels = node.getContext('2d')!.getImageData(0, 0, node.width, node.height).data;
    let ink = 0;
    for (let i = 0; i < pixels.length; i += 4) {
      if (pixels[i + 3] > 200 && pixels[i] < 180 && pixels[i + 1] < 180 && pixels[i + 2] < 180) ink++;
    }
    return ink;
  })).toBeGreaterThan(100);
}

async function visiblePage(page: Page) {
  return page.locator('.pdf-canvas-wrap').evaluate(root => {
    const top = root.getBoundingClientRect().top;
    const pages = [...root.querySelectorAll<HTMLElement>('.pdf-page')];
    const current = pages.find(p => p.getBoundingClientRect().bottom > top + 16)!;
    const rect = current.getBoundingClientRect();
    return { page: Number(current.dataset.pageNumber), fraction: (top - rect.top) / rect.height };
  });
}

test('visible canvas pixels and later reading position survive repeated moves with hidden owner', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await setup(page);
  await expectInk(page, 1);
  await page.getByLabel('Seite', { exact: true }).selectOption('5');
  await expect.poll(async () => (await visiblePage(page)).page).toBe(5);
  await expectInk(page, 5);
  const canvas = page.locator('.pdf-page[data-page-number="5"] canvas');
  const beforeZoom = await canvas.evaluate(node => node.width);
  await page.getByRole('button', { name: 'Vergroessern', exact: true }).click();
  await expect.poll(() => canvas.evaluate(node => node.width)).toBeGreaterThan(beforeZoom);
  await expectInk(page, 5);
  const zoomedWidth = await canvas.evaluate(node => node.width);
  const initial = await visiblePage(page);
  for (let round = 0; round < 3; round++) {
    const child = await detach(page, 'PDF · Daten · Analyse');
    await page.getByRole('button', { name: 'Seite wechseln' }).click();
    await expect.poll(async () => (await visiblePage(child)).page).toBe(initial.page);
    await expectInk(child, initial.page);
    expect(Math.abs((await visiblePage(child)).fraction - initial.fraction)).toBeLessThan(0.12);
    await page.getByRole('button', { name: 'Seite wechseln' }).click();
    await child.getByRole('button', { name: 'PDF · Daten · Analyse andocken', exact: true }).click();
    await expect.poll(async () => (await visiblePage(page)).page).toBe(initial.page);
    await expectInk(page, initial.page);
    await expect.poll(() => canvas.evaluate(node => node.width)).toBe(zoomedWidth);
  }
});


test('short detached PDF keeps selection actions and controls reachable', async ({ page }) => {
  await setup(page);
  await expect(page.locator('.pdf-text-layer span').first()).toBeAttached();
  await page.evaluate(() => {
    const span = document.querySelector('.pdf-text-layer span')!;
    const range = document.createRange(); range.selectNodeContents(span);
    window.getSelection()!.removeAllRanges(); window.getSelection()!.addRange(range);
    span.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
  });
  const child = await detach(page, 'PDF · Daten · Analyse');
  await child.setViewportSize({ width: 360, height: 240 });
  const addNote = child.getByRole('button', { name: 'PDF-Notiz hinzufügen', exact: true });
  await addNote.click();
  await child.getByLabel('PDF-Notiz', { exact: true }).fill('Entwurf am unteren Rand');
  const save = child.getByRole('button', { name: 'PDF-Notiz speichern', exact: true });
  await save.scrollIntoViewIfNeeded(); await expect(save).toBeInViewport();
  await child.getByRole('button', { name: 'PDF · Daten · Analyse andocken', exact: true }).click();
  await expect(page.getByLabel('PDF-Notiz', { exact: true })).toHaveValue('Entwurf am unteren Rand');
});
