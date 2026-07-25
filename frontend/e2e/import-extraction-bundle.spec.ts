/**
 * Durchklick fuer die Umbauten der zwoelften Session: Import-Reihenfolge,
 * Themen-Chips, einklappbares Hauptthema, „Alle speichern", Extraktions-Zaehler
 * und die Bundle-Dropzone in der Projektuebersicht.
 *
 * Wie `product.spec.ts` vollstaendig gemockt — kein Backend, damit der Test
 * nicht mit einem laufenden Backend um den DuckDB-Schreib-Lock streitet.
 */
import { expect, test, type Page } from "@playwright/test";

const API = "http://127.0.0.1:8000";

/** Alles, was ein Test nicht selbst mockt, laeuft ins Leere — wie ohne Backend.
 *
 *  Bewusst `abort()` statt einer leeren Antwort: ein `{items: []}` auf eine
 *  Route, die etwas anderes erwartet, laesst die Komponente auflaufen, ein
 *  Verbindungsfehler dagegen ist ein Zustand, den die Oberflaeche kennt.
 *  Zuerst registrieren — Playwright nimmt die zuletzt registrierte Route. */
async function quietBackend(page: Page) {
  await page.route(`${API}/**`, async (route) => {
    await route.abort();
  });
}

type Project = { id: string; name: string; paper_ids: string[]; paper_count: number; year_min: null; year_max: null };

async function mockProjects(page: Page, projects: Project[]) {
  await page.route(/\/projects(?:\?.*)?$/, async (route) => {
    if (route.request().method() === "POST") {
      const payload = route.request().postDataJSON() as { name: string; paper_ids?: string[] };
      const project: Project = {
        id: payload.name,
        name: payload.name,
        paper_ids: payload.paper_ids ?? [],
        paper_count: 0,
        year_min: null,
        year_max: null
      };
      projects.push(project);
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ project }) });
      return;
    }
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ projects }) });
  });
}

/** Legt ein Projekt an und waehlt es in der Kopfzeile aus — „Alle speichern"
 *  erscheint nur bei einem echten Projekt (nicht bei „Alle Papers"). */
async function createAndSelectProject(page: Page, name: string) {
  await page.getByPlaceholder("Neues Projekt").fill(name);
  await page.getByRole("button", { name: /Anlegen/ }).click();
  await expect(page.getByRole("button", { name: new RegExp(name) })).toBeVisible();
  await page.getByLabel("Projekt").selectOption(name);
  await expect(page.getByLabel("Projekt")).toHaveValue(name);
}

test("Import-Stufe: Reihenfolge, Themen-Chips, einklappbares Hauptthema, Alle speichern", async ({ page }) => {
  const projects: Project[] = [];
  const savedGrey: Array<{ url: string }> = [];
  const searchedQueries: string[] = [];
  const researchedQuestions: string[] = [];

  await quietBackend(page);
  await mockProjects(page, projects);

  await page.route(/\/harvest\/search$/, async (route) => {
    const payload = route.request().postDataJSON() as { query: string };
    searchedQueries.push(payload.query);
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        query: payload.query,
        results: [
          {
            id: "arxiv:2401.00001",
            title: `Treffer fuer ${payload.query}`,
            abstract: "Abstract des Treffers.",
            authors: ["A. Autor"],
            source: "arxiv",
            source_id: "2401.00001",
            year: 2024,
            has_full_text: true
          }
        ],
        warnings: []
      })
    });
  });

  // Die Analyse hinter den KI-Vorschlaegen: `analysis.queries` wurde frueher
  // verworfen und ist jetzt die Quelle der klickbaren Themen-Chips.
  await page.route(/\/discovery\/from-topic$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        analysis: {
          topic_summary: "Zusammenfassung des Themas.",
          methods: ["RCT", "Meta-Analyse"],
          queries: [
            { query: "attention dwell time", reason: "Kernbegriff" },
            { query: "eye tracking reading", reason: "Messmethode" }
          ]
        },
        candidates: [
          {
            id: "arxiv:2402.00002",
            title: "KI-Vorschlag Paper",
            abstract: "Vorgeschlagen von der Analyse.",
            source: "arxiv",
            source_id: "2402.00002",
            year: 2025,
            has_full_text: true,
            discovery_reason: "passt zum Thema"
          }
        ]
      })
    });
  });

  await page.route(/\/research\/deep$/, async (route) => {
    const payload = route.request().postDataJSON() as { question: string };
    researchedQuestions.push(payload.question);
    const isMain = researchedQuestions.length === 1;
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        question: payload.question,
        provider: "fake",
        queries: [payload.question],
        topic_summary: "Web-Zusammenfassung.",
        related_topics: isMain ? ["Verwandtes Thema A"] : [],
        findings: [
          {
            url: `https://example.org/${encodeURIComponent(payload.question)}/1`,
            title: `Webquelle 1 zu ${payload.question}`,
            snippet: "Auszug 1",
            summary: "Zusammenfassung 1",
            injection_flags: [],
            quarantined: false,
            raw_excerpt: "Rohtext 1"
          },
          {
            url: `https://example.org/${encodeURIComponent(payload.question)}/2`,
            title: `Webquelle 2 zu ${payload.question}`,
            snippet: "Auszug 2",
            summary: "Zusammenfassung 2",
            injection_flags: [],
            quarantined: false,
            raw_excerpt: "Rohtext 2"
          }
        ],
        warnings: []
      })
    });
  });

  await page.route(/\/projects\/[^/]+\/grey-sources$/, async (route) => {
    if (route.request().method() !== "POST") {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ project_id: "x", grey_sources: [] }) });
      return;
    }
    const payload = route.request().postDataJSON() as { sources?: Array<{ url: string }>; query?: string };
    const sources = payload.sources ?? [];
    savedGrey.push(...sources);
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ project_id: "x", saved: sources }) });
  });

  await page.goto("/");
  await createAndSelectProject(page, `e2e-import-${Date.now()}`);

  await page.getByRole("link", { name: /Forschung/ }).click();
  await page.getByRole("button", { name: "Stufe Import" }).click();
  await expect(page.getByRole("heading", { name: "Import" })).toBeVisible();

  // --- Reihenfolge: Treffer -> KI-Vorschlaege -> Web-Recherche -------------
  await page.getByPlaceholder("Topic oder Frage").fill("attention");
  await page.getByRole("button", { name: "Suchen" }).click();
  await expect(page.locator(".pick-list-heading", { hasText: "Treffer" })).toBeVisible();

  await page.getByRole("button", { name: "KI-Vorschläge", exact: true }).click();
  await expect(page.locator(".pick-list-heading", { hasText: "KI-Vorschläge zum Thema" })).toBeVisible();

  // Reihenfolge im DOM, nicht nur Sichtbarkeit: die Treffer standen frueher
  // hinter zwei Panels, die man gar nicht angestossen hatte.
  const sectionOrder = await page.evaluate(() => {
    const text = document.body.innerText;
    return {
      treffer: text.indexOf("Treffer"),
      vorschlaege: text.indexOf("KI-Vorschläge zum Thema"),
      web: text.indexOf("Web-Recherche (Deep Research)")
    };
  });
  expect(sectionOrder.treffer).toBeGreaterThan(-1);
  expect(sectionOrder.vorschlaege).toBeGreaterThan(sectionOrder.treffer);
  expect(sectionOrder.web).toBeGreaterThan(sectionOrder.vorschlaege);

  // --- Themen-Chips aus analysis.queries ----------------------------------
  const chips = page.locator(".discovery-topic-chips .topic-chip");
  await expect(chips).toHaveCount(2);
  await expect(chips.first()).toContainText("attention dwell time");
  await chips.nth(1).click();
  await expect(page.getByPlaceholder("Topic oder Frage")).toHaveValue("eye tracking reading");
  await expect.poll(() => searchedQueries).toContain("eye tracking reading");

  await page.screenshot({ path: "e2e-artifacts/import-treffer-vorschlaege.png"});

  // --- Web-Recherche: Hauptthema klappt zu, „Alle speichern" --------------
  // „Verwandte Themen" ist standardmaessig aus; eingeschaltet entsteht neben dem
  // Hauptthema eine zweite Gruppe — nur so ist „Alle speichern" mehr als eine Gruppe.
  await page.getByRole("checkbox", { name: "Verwandte Themen" }).check();
  await page.getByPlaceholder("Frage für die Web-Recherche").fill("Wie wirkt Aufmerksamkeit?");
  await page.getByRole("button", { name: "Recherchieren" }).click();

  const mainGroup = page.locator(".topic-group").filter({ hasText: "Wie wirkt Aufmerksamkeit?" });
  const mainHeader = mainGroup.locator(".topic-group-header");
  await expect(mainHeader).toHaveAttribute("aria-expanded", "true");
  await expect(mainGroup.getByText("Webquelle 1 zu Wie wirkt Aufmerksamkeit?")).toBeVisible();

  // Das Hauptthema war als einziges festgenagelt — genau das ist der Umbau.
  await expect(mainHeader).toHaveClass(/topic-group-header--main/);
  await mainHeader.click();
  await expect(mainHeader).toHaveAttribute("aria-expanded", "false");
  await expect(mainGroup.getByText("Webquelle 1 zu Wie wirkt Aufmerksamkeit?")).toHaveCount(0);
  await mainHeader.click();
  await expect(mainHeader).toHaveAttribute("aria-expanded", "true");

  // Das verwandte Thema kommt als eigene, zugeklappte Gruppe dazu.
  const relatedGroup = page.locator(".topic-group").filter({ hasText: "Verwandtes Thema A" });
  await expect(relatedGroup.locator(".topic-group-header")).toHaveAttribute("aria-expanded", "false");

  // Pro Gruppe speichern + global speichern. Auf das Ende der Untersuche warten,
  // sonst blendet der „pending"-Zweig den globalen Knopf noch aus.
  await expect(mainGroup.getByRole("button", { name: /Dieses Thema speichern \(2\)/ })).toBeVisible();
  const saveAll = page.getByRole("button", { name: /Alle speichern \(\d+\)/ });
  await expect(saveAll).toBeVisible();
  await expect(saveAll).toContainText("Alle speichern (4)"); // 2 Haupt- + 2 Unterthema-Treffer
  await page.screenshot({ path: "e2e-artifacts/import-web-recherche.png"});
  await saveAll.click();
  await expect.poll(() => savedGrey.length).toBe(4);
});

test("Extraktion: ein Zaehler, drei Anzeigen — PDF, nur Abstract, ohne Text", async ({ page }) => {
  await quietBackend(page);
  await mockProjects(page, []);

  // 2x PDF (davon 1 extrahiert), 1x nur Abstract, 1x ohne Text, 1x graue Quelle.
  // Erwartet: extrahierbar 3, extrahiert 1, offen 2.
  await page.route(/\/extraction\/library(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        items: [
          {
            paper_id: "pdf-extrahiert",
            title: "Paper mit PDF (extrahiert)",
            filename: "a.pdf",
            pdf_path: "library/a.pdf",
            pdf_available: true,
            abstract_available: true,
            source_type: "pdf",
            latest_extraction_status: "success",
            known_paper: true
          },
          {
            paper_id: "pdf-offen",
            title: "Paper mit PDF (offen)",
            filename: "b.pdf",
            pdf_path: "library/b.pdf",
            pdf_available: true,
            abstract_available: true,
            source_type: "pdf",
            latest_extraction_status: null,
            known_paper: true
          },
          {
            paper_id: "nur-abstract",
            title: "Paper ohne PDF, mit Abstract",
            filename: "",
            pdf_path: "",
            pdf_available: false,
            abstract_available: true,
            source_type: "pdf",
            latest_extraction_status: null,
            known_paper: true
          },
          {
            paper_id: "ohne-text",
            title: "Paper ohne PDF und ohne Abstract",
            filename: "",
            pdf_path: "",
            pdf_available: false,
            abstract_available: false,
            source_type: "pdf",
            latest_extraction_status: null,
            known_paper: true
          },
          {
            paper_id: "graue-quelle",
            title: "Webquelle",
            filename: "",
            pdf_path: "",
            pdf_available: false,
            abstract_available: true,
            source_type: "grey",
            latest_extraction_status: null,
            known_paper: true
          }
        ],
        total: 5
      })
    });
  });

  await page.goto("/");
  await page.getByRole("link", { name: /Forschung/ }).click();
  await page.getByRole("button", { name: "Stufe Extraktion" }).click();
  await expect(page.getByRole("heading", { name: "Extraktion" })).toBeVisible();

  // Kopfzeilen-Badge und Batch-Panel muessen dieselbe Zahl zeigen — das war der
  // „480 vs. 285"-Widerspruch.
  const badge = page.locator(".extraction-overview-badge");
  await expect(badge).toContainText("1/3");
  await expect(badge).toContainText("2 PDF · 1 nur Abstract · 1 ohne Text");

  const batchPanel = page.locator(".extraction-batch-panel");
  await expect(batchPanel).toContainText("1/3 extrahiert");
  await expect(batchPanel).toContainText("2 PDF · 1 nur Abstract · 1 ohne Text");
  await expect(batchPanel.getByRole("button", { name: "Alle (3)" })).toBeVisible();
  await expect(batchPanel.getByRole("button", { name: "PDF (2)" })).toBeVisible();
  await expect(batchPanel.getByRole("button", { name: "Abstract (1)" })).toBeVisible();
  await expect(batchPanel.getByRole("button", { name: /Nicht extrahiert \(2\)/ })).toBeVisible();

  // Ohne PDF *und* ohne Abstract ist nichts zu extrahieren — die Zeile darf
  // nicht als „nur Abstract" ausgezeichnet sein.
  const ohneText = batchPanel.locator(".data-row", { hasText: "Paper ohne PDF und ohne Abstract" });
  await expect(ohneText).toContainText("ohne Text");
  await expect(ohneText).not.toContainText("nur Abstract");

  // Filter schraenkt den Offen-Zaehler ein: mit PDF ist genau eines offen.
  await batchPanel.getByRole("button", { name: "PDF (2)" }).click();
  await expect(batchPanel.getByRole("button", { name: /Nicht extrahiert \(1\)/ })).toBeVisible();
  await batchPanel.getByRole("button", { name: "Abstract (1)" }).click();
  await expect(batchPanel.getByRole("button", { name: /Nicht extrahiert \(1\)/ })).toBeVisible();
  // „Abstract (1)" muss auch genau eine Zeile zeigen — vorher standen hier zwei,
  // weil der Filter nur „kein PDF" pruefte.
  await expect(batchPanel.locator(".data-row", { hasText: "Paper ohne PDF, mit Abstract" })).toBeVisible();
  await expect(batchPanel.locator(".data-row", { hasText: "Paper ohne PDF und ohne Abstract" })).toHaveCount(0);

  await page.screenshot({ path: "e2e-artifacts/extraktion-zaehler.png"});
});

test("Projektübersicht: Bundle-Dropzone zeigt Vorschau und importiert", async ({ page }) => {
  const projects: Project[] = [];
  await quietBackend(page);
  await mockProjects(page, projects);

  await page.route(/\/bundles\/preview$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        preview: {
          project: "Hirn und LLM",
          exported_at: "2026-07-25T21:00:00",
          app_version: "5",
          bundle_version: 1,
          includes_pdfs: true,
          counts: { extraction_results: 79, grey_sources: 8, entity_embeddings: 811 },
          papers_existing: 5,
          papers_new: 130,
          project_exists: false,
          warnings: []
        }
      })
    });
  });

  await page.route(/\/bundles\/import/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        report: {
          project: "Hirn und LLM",
          mode: "merge",
          papers_imported: 130,
          papers_skipped: 5,
          extractions_imported: 79,
          grey_sources_imported: 8,
          embeddings_imported: 811,
          pdfs_imported: 12,
          paper_count: 135
        }
      })
    });
  });

  await page.goto("/");
  const bundlePanel = page.locator(".panel").filter({ hasText: "Projekt importieren" });
  await expect(bundlePanel).toBeVisible();
  await expect(bundlePanel.getByText("Bundle-ZIP hierher ziehen oder auswählen")).toBeVisible();

  await bundlePanel.locator('.drop-zone input[type="file"]').setInputFiles({
    name: "hirn-und-llm.zip",
    mimeType: "application/zip",
    buffer: Buffer.from("PK")
  });

  // Erst zeigen, was drin ist — dann entscheiden lassen.
  await expect(bundlePanel.locator(".bundle-preview-facts")).toContainText("Hirn und LLM");
  const counts = bundlePanel.locator(".bundle-preview-counts");
  await expect(counts).toContainText("130 neue Paper");
  await expect(counts).toContainText("5 bereits vorhanden");
  await expect(counts).toContainText("79 Extraktionen");
  await expect(counts).toContainText("8 Web-Quellen");
  await expect(bundlePanel.getByText("hirn-und-llm.zip")).toBeVisible();

  await page.screenshot({ path: "e2e-artifacts/bundle-vorschau.png"});

  await bundlePanel.getByRole("button", { name: "Importieren" }).click();
  await expect(bundlePanel.locator(".hint-row")).toContainText("130 Paper neu");
  await expect(bundlePanel.locator(".hint-row")).toContainText("Das Projekt hat jetzt 135 Paper");
});
