# Globales Wörterbuch

Das Wörterbuch steht direkt unter der globalen Kopfzeile. Es gilt für alle Projekte,
auch für die globale Bibliothek. „Wörterbuch“ klappt die Verwaltung auf;
„Begriffshinweise anzeigen“ schaltet unabhängig davon die Hinweise um.
Beide Einstellungen bleiben lokal erhalten. Beim ersten Start ist die Verwaltung
zugeklappt und die Anzeige aktiviert.

## Bedienung

- „Begriff anlegen“ öffnet einen leeren Entwurf. Eine Textauswahl im PDF,
  Notizeditor oder in der Vorschau bietet „Zum Wörterbuch hinzufügen“ an.
- Begriff und Erklärung lassen sich bearbeiten. Nur „Speichern“ schreibt einen Eintrag.
  „Abbrechen“ verwirft den Entwurf. Während des Speicherns sind die Felder und
  „Abbrechen“ gesperrt, damit ein bereits gestarteter Schreibvorgang nicht als
  verworfener Entwurf erscheint.
- „KI-Vorschlag anfordern“ verwendet die aktuelle Anbieter-/Modellauswahl.
  Der Aufruf enthält ausschließlich den Begriff und den ausgewählten Text.
  Die KI erhält keine zusätzlich geladenen Dokumente. Ihr Vorschlag bleibt separat,
  bis „Vorschlag übernehmen“ ihn in die Erklärung kopiert. Anschließend muss der
  Eintrag ausdrücklich gespeichert werden. Fehler erhalten eigene Eingaben;
  Antworten für verworfene oder geänderte Entwürfe werden ignoriert.
- Bei einem doppelten Begriff bietet das Formular den vorhandenen Eintrag zur
  Bearbeitung an. Groß-/Kleinschreibung und überzählige Leerzeichen unterscheiden
  keine Einträge.
- Ganze Begriffe und Mehrwortbegriffe werden ohne Beachtung der Groß-/Kleinschreibung
  erkannt. Bei Überschneidungen gewinnt der längere Treffer. Es gibt keine
  automatische Erkennung von Synonymen oder Wortformen. Code, URLs, Markdown-Syntax
  und Zitatverknüpfungen sind ausgeschlossen.
- Die Vorschau zeigt Erklärungen bei Hover oder Tastaturfokus. Im Editor erscheinen
  dezente Unterstreichungen; Hover oder der Cursor im Begriff zeigen die Erklärung.
  Escape schließt sie. Die blockweise Vorschau-Bearbeitung nutzt dieselbe Funktion.

Wörterbuchaktionen verändern weder Notiztext noch PDF-Anmerkungen. Projektwechsel,
Umbenennung, Löschung und Projekt-Bundles beeinflussen die Einträge nicht.

## Umsetzung

Die globale DuckDB-Tabelle `glossary` wird beim Öffnen der bestehenden Metadatenbank
angelegt. Sie enthält ID, Begriff, einen eindeutigen normalisierten Schlüssel,
Erklärung sowie Erstellungs- und Änderungszeit. Sie hat keine Projektspalte und
steht weder in `PROJECT_SCOPED_TABLES` noch in der Bundle-Tabelle `TABLE_FILES`.

Produkt-API:

- `GET /glossary`: `{ items: GlossaryEntry[] }`
- `POST /glossary`: `{ term, explanation }`
- `PATCH /glossary/{id}`: Begriff und/oder Erklärung
- `DELETE /glossary/{id}`
- `POST /glossary/suggest`: `{ term, selected_text, provider?, model? }`, liefert
  `{ suggestion }` und speichert nichts. Ausschließlich dieser Endpunkt ruft
  `LLMRouter` auf.

Leere Felder werden abgelehnt. Duplikate liefern HTTP 409 mit dem bestehenden Eintrag.
Die Datenbank erzwingt die Eindeutigkeit zusätzlich über einen UNIQUE-Schlüssel.
Begriffe sind auf 500, Erklärungen und KI-Auswahltexte auf 16.000 Zeichen begrenzt.

Im Frontend liegt die gemeinsame Logik unter `frontend/src/glossary/`. Der
React-Query-Schlüssel ist `['glossary']`, ohne Projekt-ID. Änderungen invalidieren
alle angeschlossenen Notizflächen; Storage-Ereignisse aktualisieren weitere Fenster.
Die Einstellungen liegen unter `sciencekg.glossary.enabled` und
`sciencekg.glossary.expanded` in localStorage. Die Einträge selbst liegen in DuckDB.
Ein unsichtbarer, synchronisierter Textspiegel misst Hoverpositionen; Eingabe und
Auswahl bleiben beim nativen Textarea.

## Prüfung (2026-09-09)

```bash
.venv/bin/python -m pytest tests/test_glossary.py tests/test_product_api.py tests/test_graph_bundle.py tests/test_workspace_pdf_citations.py -q
npm --prefix frontend test
npm --prefix frontend run test:e2e -- glossary.spec.ts workspace-reading.spec.ts --workers=1
npm --prefix frontend run build
```

Ergebnis: 60 Python-Tests, 268 Frontend-Tests und 11 gezielte Browsertests bestanden.
Der Frontend-Build einschließlich TypeScript-Prüfung ist erfolgreich.

Die Backendtests verwenden temporäre DuckDB-Dateien. Sie prüfen CRUD, Duplikate,
Persistenz über neue Verbindungen, Projektumbenennung/-löschung, Bundle-Ausschluss
sowie explizite KI-Aufrufe ohne Speicherung. Die Frontendtests prüfen Erkennung,
Ausschlüsse, Überlappungen, Einstellungen, Entwürfe und verspätete Antworten.

Die Browserprüfung verwendet reale PDF- und Notizkomponenten mit einer Test-PDF;
API und KI sind kontrollierte Testantworten. Geprüft werden PDF-/Notizauswahl,
Projektwechsel, Neuladen, Bearbeiten, Löschen, Hover nach Scrollen und Größenänderung,
Tastaturbedienung, Vorschau-Bearbeitung und unveränderter Notiztext.
Es wurden keine externen KI-Anbieter aufgerufen.

Zusätzlich wurde `frontend/e2e/product.spec.ts` ausgeführt. Der Test scheitert an
Zeile 803: Cursorposition nach einer PDF-Zitateinfügung ist 0, erwartet wird eine
Position größer als 9431. Derselbe Fehler wurde ohne die neue
Wörterbuchintegration im Frontend reproduziert. Die vorhandene Einfügelogik und dieser Test
wurden deshalb unverändert erhalten.
