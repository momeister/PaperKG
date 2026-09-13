# Workspace Session 1

Umgesetzt: atomare Provider-/Modellwahl, Anfragegenerationen gegen verspätete
Ergebnisse, verwendeter Provider und vom Server gemeldetes Modell sowie sichtbare
Fallback-Gründe. Der bestehende Antwortvertrag bleibt erhalten.

Die PDF-Textsuche zählt einzelne Vorkommen unabhängig von der ungefähren
Zitatlokalisierung. Normalisierte Zeichen verweisen auf Originalzeichen;
DOM-Range-Messungen ersetzen im Viewer die proportionale Schätzung. Die
Textschicht folgt auch PDF-Seitendrehungen. Suche und Auswahl verwenden getrennte
Markierungen, seitenübergreifende Auswahlen bleiben beim Schreiben sichtbar.

Original oder gekennzeichnete Übersetzung werden über den vorhandenen Notizcursor
eingefügt. Originaltext und optionale `pdf_anchors` werden in `note_citations`
persistiert; `POST`/`PATCH` für Notizen speichern Text und Zitate gemeinsam in einer
Transaktion. Bestehende IDs und Zitate ohne Anker bleiben kompatibel.
„Aktive Textstelle“ und Providerbereich können eingeklappt und mit Maus oder
Pfeiltasten in der Höhe verändert werden. Der optionale Schreibmodus übernimmt
die aktuelle Zeilenhöhe; Scrollen, Cursorbewegungen und Breitenänderungen setzen
den Anker neu.

## Reale Modellprüfung am 8. September 2026

Vorhandener Ollama-Zugang, ausschließlich eine synthetische Quelle:
„In this fictional benchmark, the system processed 20 documents in 5 seconds.“
Frage: „Wie viele Dokumente verarbeitet der fiktive Benchmark und wie lange dauert
es?“ Alle Läufe mit derselben Quelle, ohne Gesprächsverlauf, Temperatur 0,
max_tokens 2048 und einer eigenen temporären Prüfdatenbank ohne Cacheübernahme.

1. `glm-5.2:cloud`, Servermodell `glm-5.2`: richtige Angaben, kein Fallback.
2. Derselbe Router danach mit `deepseek-v4-flash:cloud`, Servermodell
   `deepseek-v4-flash`: richtige Angaben, kein Fallback.
3. Frischer Router mit demselben DeepSeek-Modell: identische finale Antwort und
   Quellenverweise wie Lauf 2.

Beide DeepSeek-Antworten:

> Der fiktive Benchmark verarbeitet 20 Dokumente. [fixture:benchmark]
>
> Die Verarbeitung der 20 Dokumente dauert 5 Sekunden. [fixture:benchmark]

GLM und DeepSeek sind in dieser Installation zwei Modelle des Providers `ollama`.
Der Wechsel zwischen unterschiedlichen Providern wird zusätzlich simuliert. Das
ursprünglich gemeldete Verhalten ist mit dieser Kontrollquelle nicht reproduziert;
die damalige Frage und Quellenkonstellation lagen nicht vor.

## Automatische Prüfung

- Python: API-Notizen, Migration/Neuladen von PDF-Ankern, unveränderte Alt-IDs,
  Transaktions-Rollback, Antwortvertrag, LLM-Router und Phase-4-Abfragen.
- Frontend: vollständige Vitest-Suite, einschließlich Providerwechsel,
  verspäteten Antworten/Fehlern und normalisierten PDF-Zeichenoffsets.
- Chromium: echte pdf.js-Textschicht mit wiederholten und kurzen Suchbegriffen,
  variablen Zeichenbreiten, Zeilentrennung, Zoom, gedrehter Seite und Bild-PDF;
  Auswahl → Schreiben → Übersetzen → Speicherfehler → Wiederholung → Neuladen →
  Zitatklick; neue Notiz ohne aktiven Editor; Schreibanker bei Scrollen,
  Cursorbewegung, Umbruch, Größenänderung und unsichtbarem Cursor.
- Die Browserprüfung verwendet lokale PDF-Fixtures und simulierte API-Antworten;
  DuckDB-Persistenz und echte LLM-Antworten werden separat wie oben geprüft.
- Frontend-Produktionsbuild (`tsc --noEmit` und Vite).

Wörterbuch und loslösbare Desktop-Fenster bleiben Session 2 beziehungsweise 3.
OCR ist nicht Teil dieser Umsetzung.

## Abschlussprüfung am 9. September 2026

- 148 betroffene Python-Tests bestanden: Phase 4, Produkt-API, LLM-Router,
  Antwortvertrag und die beiden neuen Workspace-Testdateien.
- Vollständige Frontend-Suite: 257 Tests bestanden; nach den letzten Änderungen
  98 betroffene Tests erneut bestanden.
- Acht Chromium-Prüfungen bestanden; zusätzlich zum PDF-Zoom wurde die Ausrichtung
  der Zitatrechtecke beim globalen UI-Zoom geprüft.
- Ruff für die betroffenen Notiz-/Routerdateien und neuen Python-Tests sowie
  `git diff --check` bestanden.
- Vite meldet die bestehende Warnung zu großen Bundles; die Python-Prüfung meldet
  die bestehende Starlette/httpx-Deprecation. Beide verhindern die Prüfung nicht.
