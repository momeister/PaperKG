# Kompakter Arbeitsplatz und PDF-Fenster

Stand: 2026-09-12.

Die globale Kopfzeile bündelt Projekt, Modell und Status in einer Zeile. Modellwahl
und LLM-Parameter teilen ein Popover; Wörterbuch und Darstellung stehen bei wenig
Platz unter „Mehr“. Der aktive Modellname (mit vollständigem Tooltip) und die
bestehende Kennzeichnung „nicht lokal“ bleiben sichtbar. Die feste Providerzeile
entfällt. Ihre alten gespeicherten Werte werden nicht verändert oder ausgewertet.

Die Arbeitsplatzbereiche verwenden eine gemeinsame Titelleiste für Titel,
Einklappen und Fensteraktionen. Verdeckte, weiter gemountete Ansichten tragen
keine Überschrift in die sichtbare Titelleiste ein. PDF-Seite und Zoom sind
gebündelt; die Suche bleibt bei vorhandenem Suchtext offen. Zitatwechsel öffnen
die zunächst eingeklappte Textstelle. Seltene Assistant-Einstellungen stehen
unter „Optionen“. Andere Seiten behalten ihre bisherigen PDF-Bedienelemente.

PDF-Beobachter und Animationsframes verwenden das beherbergende Fenster.
Renderaufträge werden abgebrochen und vor dem nächsten Auftrag vollständig
abgearbeitet. Die Leseposition wird relativ zur Seite wiederhergestellt;
Größenänderungen vorhergehender Seiten werden dabei ebenfalls berücksichtigt.
Der persistente Workspace-Controller und das geladene PDF bleiben bestehen.

Die Höhenkette reserviert Werkzeugleisten nur ihre tatsächliche Höhe. PDF,
Antwortverlauf, Textstelle und Editor haben begrenzte Scrollbereiche; bei kleinen
Fenstern bleiben weitere Inhalte durch übergeordnete Scrollbereiche erreichbar.
Gespeicherte Wunschhöhen werden nur für die Darstellung begrenzt.

Die native Prüfung deckte zusätzlich einen Fehler beim gleichzeitigen Andocken
mehrerer Fenster auf: Das Speichern aller Fenstergrößen fragte bereits zerstörte
Webviews ab und verhinderte das Schließen weiterer Fenster. Beim Andocken wird
jetzt nur die Größe des betroffenen Fensters gespeichert. Die Bestätigung wartet,
bis dessen native Fensterkennung für ein erneutes Öffnen freigegeben ist.
Backend-API und Datenbankschema bleiben unverändert.

## Geprüft

- Frontend-Typecheck und Produktionsbuild; die bestehende Warnung zu großen
  Vite-Chunks bleibt bestehen.
- 49 Vitest-Tests: LLM-Auswahl, PDF-Textsuche, Composer, Wörterbuch,
  Workspace-Fensterverwaltung, Editorzustand, Notizentwürfe und Shutdown.
- 26 Playwright-Tests aus `workspace-layout`, `workspace-windows`,
  `workspace-reading`, `glossary` und `product` (in mehreren gezielten Läufen).
- Layout: 1920×1080, 1366×768 und 1024×600, jeweils bei 100 % und 125 %
  Schriftgröße. Kopfzeile höchstens 48 CSS-Pixel bei 100 %. Geprüft wurden lange
  Modellnamen, Menübedienung per Tastatur/Escape mit Fokusrückgabe, letzte
  Antwortpassage, Notizeditor, offene Belege und vollständig scrollbar erreichbare
  Textstellen bei gespeicherter Wunschhöhe von 9999 Pixeln.
- Fenster: echte dunkle Canvas-Pixel vor und nach drei Ab-/Andockzyklen auf Seite 5,
  auch bei ausgeblendetem Hauptarbeitsplatz. Leseposition und Zoom bleiben erhalten.
  Suche und PDF-Auswahl überleben Fensterwechsel. Ein 360×240-Pixel-Fenster lässt
  untere PDF-Notizaktionen erreichen und bewahrt den Notizentwurf beim Andocken.
- Drei Rust-Tests der Fensterverwaltung bestanden.
- Native Tauri-Prüfsonde unter X11/Xvfb und GNOME/Wayland erfolgreich: Canvas-Pixel
  auf PDF-Seite 5 vor/nach drei Fensterwechseln, ausgeblendeter Hauptarbeitsplatz,
  vier verwandte Webviews, identischer Editor und Auswahl, Autosave, laufende
  Hintergrundarbeit sowie gleichzeitiges Andocken und sofortiges Wiederöffnen.

## Reproduktion

Vite muss während der gesamten nativen Prüfung weiterlaufen. Ein von Playwright
gestarteter Server endet mit dessen Tests und eignet sich dafür nicht.

```bash
npm --prefix frontend run dev -- --port 5173 --strictPort
# In einem zweiten Terminal:
npm --prefix frontend run build
npm --prefix frontend test -- src/components/LlmPicker.test.tsx src/components/pdfTextSearch.test.ts src/pages/AssistantComposer.test.tsx src/workspace src/glossary
npm --prefix frontend run test:e2e -- workspace-layout.spec.ts workspace-windows.spec.ts workspace-reading.spec.ts glossary.spec.ts product.spec.ts --workers=2
cargo test --manifest-path src-tauri/Cargo.toml --lib workspace_windows::tests
SCIENCEKG_PORTAL_PROBE_REACT=1 GDK_BACKEND=x11 xvfb-run -a cargo run --manifest-path src-tauri/Cargo.toml --example workspace_portal_probe
SCIENCEKG_PORTAL_PROBE_REACT=1 GDK_BACKEND=wayland cargo run --manifest-path src-tauri/Cargo.toml --example workspace_portal_probe
```

Die native Prüfsonde nutzt einen separaten App-Identifier und API-Doubles. Sie
startet keinen zweiten Backend-Prozess. Ein neues Installationspaket,
Mehrmonitor-Skalierung sowie Windows und macOS wurden nicht geprüft.
