# Workspace-Assistent: Belege, Eingabe und Abnahme

Stand: 8. September 2026. Implementierung des abgestimmten Plans; vorhandene Änderungen und die Extraktionshistorie bleiben erhalten. Cloud-Aufrufe erfolgten ausschließlich mit explizit gewähltem Provider und Modell. Keine lokalen Modelle oder Embeddings wurden gestartet oder heruntergeladen.

## Implementierung

- `parsing/layout.py` und `MarkerParser`: räumliche Lesereihenfolge, getrennte Spalten, Überschriften, Seitenränder und erkannte Tabellen; Wortabdeckung und Seitenpositionen. Der geschützte PDF-Kindprozess bleibt erhalten. Parser-Version: `spatial-blocks-v3`.
- `paper_passages` und `passage_documents` in derselben DuckDB: rekonstruierbarer Index mit Dokument-Fingerprint, Parser-Version, Seite, Textpositionen und stabilen Passagen-IDs. Neue Parse-/Extraktionsläufe indizieren; vorhandene PDFs werden bei Verwendung versioniert nachindiziert. Absatzstücke sind höchstens 1.500 Zeichen lang, mit höchstens 180 Zeichen Überlappung.
- CPU-BM25 kombiniert Originalfrage, übersetzte Suchbegriffe und den Bezug zu vorherigen Benutzerfragen. Eine begrenzte Nachbarschaftserweiterung nutzt die freigegebenen DuckDB-Beziehungen, die auch den Kuzu-Graphen speisen. Das Relationszitat muss im betreffenden Dokument vorkommen; Beziehungen liefern Suchbegriffe, niemals Ersatzbelege. Paperfilter greifen vor Limits; nur die neueste erfolgreiche Extraktion zählt. Evidenz wird vor Kontextbegrenzung dedupliziert.
- `query/answer_contract.py`: gemeinsamer Vertrag für KG- und PDF-Kontext, `claims_version=1`, Prüfversion `claims-v5`. Aussagen tragen stabile IDs, explizite Evidenz-IDs, Herkunftseinordnung und Prüfstatus. Der Router fordert JSON an; die Anwendung validiert Schema und IDs. Höchstens ein Reparaturversuch je strukturierter Ausgabe, einmalige Korrektur teilweise gestützter Aussagen mit erneuter Prüfung. Maximal 16 Aussagen pro Antwort.
- Textfund und inhaltliche Stützung sind getrennt. Die gebündelte Backend-Prüfung berücksichtigt Zahlen, Einheiten, Bedingungen, Negation und Attribution. Ein unabhängiger Zahlenabgleich kann ein positives Modellurteil zurückweisen. Unlesbare Mikrometer-Einheiten werden nicht aus Modellwissen ergänzt. Verbleibende ungestützte Entwürfe sind ausdrücklich als „Nicht belegt“ markiert und erhalten keine Zitatbindung.
- Finale Aussagefassung, Zitatlinks und UTF-16-Positionen entstehen zusammen. Identische Belege je Aussage werden einmal gebunden; unterschiedliche Passagen bleiben erhalten. Der Prüf-Cache hängt von Aussage, Belegtext, Dokument-/Parser-Version, Provider, Modell und Einstellungen ab.
- `POST /query/answer` bleibt bestehen; `/query/answer/stream` liefert Fortschritt und anschließend die geprüfte Endfassung als SSE. Optionale native JSON-/Thinking-Funktionen werden anhand von Providerantworten behandelt, ohne neue Modellnamen-Sonderfälle. Explizite Modellwahl erreicht auch Hilfsaufrufe. Der Router entfernt auch Denktext, dessen öffnendes Tag bereits vom Server vorgegeben wurde; damit wird das von GLM 5.3 Flash gelieferte Format korrekt verarbeitet.
- Enthält die Modellausgabe Begleittext, wird genau ein vollständiges, schema-gültiges JSON-Objekt akzeptiert; mehrdeutige oder abgeschnittene Objekte bleiben Fehler. Der einmalige Reparaturaufruf erhält den Validierungsfehler und die ursprünglichen Belege, ohne den langen Fehlversuch zusätzlich ins Kontextfenster zu kopieren. `gap` bezeichnet fehlende Antwortbelege; im Paper beschriebene Forschungslücken sind dagegen belegbare Aussagen.
- `AssistantComposer` hält Eingabe, Entwurf, Erwähnungen und Befehle lokal. Verlauf, Antwort, PDF und Notizen rendern nicht bei jedem Tastendruck. Eingabe bleibt während laufender Antworten möglich. Automatische Frontend-Modellprüfungen entfallen; manuelles Nachprüfen bleibt erhalten. Alte Zuordnungen werden gekennzeichnet. Manuelles Bearbeiten einer Antwort hebt ihre bisherigen strukturierten Prüfbindungen auf.
- Zitatzuordnungen sind vorindiziert; Hervorhebung und Übernahme nutzen den gebundenen Aussagebereich. PDF-Sprünge verwenden die hinterlegte Seite und warten auf gültige Seitengrößen. Lange Notizköpfe können den Nachrichtenbereich nicht mehr auf null Höhe drücken.

## Haptic-Paper und Regressionen

Das Paper *Vibrotactile Display: Perception, Technology, and Applications* wurde mit dem korrigierten Parser neu indiziert (12 Seiten, 70.182 Zeichen) und über `deepseek-v4-flash:cloud` neu extrahiert. Erfolgreicher Datensatz: **20679**, zehn Konzepte. Die ältere Extraktion **20640** sowie die beiden vorherigen, am Kontextbudget abgewiesenen Versuche **20677/20678** bleiben erhalten. Nachweis: `data/eval/workspace-contract-v5/browser/extraction.json`.

Die Regressionen umfassen unter anderem doppelte Evidenz-IDs, mehrere Passagen derselben Aussage, Paper-IDs mit Kommas, UTF-16 mit Emoji, deutsche Abkürzungen, Dezimalkomma, Zahlen ohne Beleg, Korrektur bei stabiler Aussage-ID, Schemafehler, SSE-Fehler, Cache-Invalidierung, Filter vor Limits, räumliche PDF-Blöcke und belegte Graph-Erweiterung. Der Browserablauf prüft zusätzlich eine Belegstelle hinter einer langen Titelseite.

| Prüfung | Ergebnis |
|---|---:|
| Betroffene Backend-Regressionen einschließlich Router | 324 bestanden |
| Frontend-Unit-Tests | 251 bestanden |
| Browser-End-to-End-Tests | 4 bestanden |
| Typecheck und Produktionsbuild | bestanden |
| Ruff für neue Module/Tests, `git diff --check` | bestanden |

Der Produktionsbuild meldet weiterhin große bestehende Bundles (u. a. Monaco/Mermaid). Caveman ist unter `~/.codex/skills/caveman` installiert.


## Browsermessung

Chromium 148.0.7778.96, vorhandener Verlauf mit 80 Evidenzeinträgen, 87 Eingabeereignisse. Gemessen wurde die Zeit vom Eingabeereignis bis zum zweiten `requestAnimationFrame` als Gelegenheit zur Darstellung; dies ist keine reine React-Renderdauer.

| Messung | Vor Untersuchung | Finale Messung |
|---|---:|---:|
| Median | 289 ms | 15,4 ms |
| p95 | 306 ms | **29,8 ms** |
| Maximum | – | 31,9 ms |

Das Ziel p95 < 50 ms ist erfüllt. Keine Browserfehler und keine automatischen Modellprüfungen beim Öffnen/Tippen. Die Prüfung der Schwellenzitation bestätigt für DeepSeek und GLM 5.3 Flash dieselbe gebundene Aussage, Seite **2** und eine sichtbare aktive PDF-Markierung. Browser-Schreibzugriffe wurden in den Messskripten abgefangen; der vorhandene Chat wurde nicht umgeschrieben.

Artefakte: `data/eval/workspace-contract-v5/browser/input.json`, `input.png`, `citation-threshold.json`, `citation-threshold.png`, `citation-glm53-threshold.json`, `citation-glm53-threshold.png`.

Backend-Diagnosen enthalten getrennte Zeiten für Retrieval, Textlokalisierung, Generierung, Prüfung und Antwortformatierung. Der Browser erfasst die Anzeige einer neuen Antwort separat als Performance-Eintrag `sciencekg:answer-display`.

## Cloud-Wiederholungsmessungen

**DeepSeek V4 Flash und GLM 5.3 Flash** (`deepseek-v4-flash:cloud`, `glm-5.3-flash:cloud`), sieben Fragenfamilien, zwei Kontextmodi und je zwei Wiederholungen: **56 Abnahmefälle**. Kritischer und Standardmodus wechseln innerhalb jeder Fragenfamilie zwischen den Kontextmodi. Das ist keine vollständige zusätzliche Kreuzung beider Antwortstile. Pro Modell läuft höchstens ein Aufruf gleichzeitig. Die endgültige Flash-Reihe wurde nach der DeepSeek-Reihe ausgeführt. Die vorher irrtümlich verwendeten GLM-5.2-Aufrufe wurden nach Benutzerkorrektur gestoppt; diese Dateien bleiben Vergleichsdaten und zählen nicht zur Abnahme. Messzeit ist die gesamte Antwort-Endpunktzeit einschließlich Hilfsaufrufen und Backend-Verarbeitung.

Die Reihe wurde nach Unterbrechungen der Arbeitsumgebung mit identischer Prüfversion fortgesetzt. Betriebssystem-/Browserlast und warme Caches können die Messungen beeinflussen; die Zeiten sind keine kontrollierte Provider-Benchmarkstudie. Rohantworten und Phasenzeiten liegen unter `data/eval/workspace-contract-v5/`. Frühere Diagnoseordner dokumentieren Zwischenstände und gehören nicht zur finalen Messreihe.

**Ergebnis: 56 Fälle ausgewertet; 53 liefern strukturierte Antworten ohne Generierungs- oder Prüfprotokollfehler. Die Cloud-Abnahme ist damit nur teilweise erfüllt.** DeepSeek: 28/28; GLM 5.3 Flash: 25/28. GLM scheitert bei beiden PDF-Übersichten (ein Generierungs-, ein Prüfprotokollfehler) und bei einem PDF-Empfehlungslauf (Prüfprotokollfehler). Auch nach dem jeweils erlaubten Reparaturversuch lag bei den Übersichten kein verwertbares JSON-Objekt vor. Beim Empfehlungslauf verwies die Prüfung auf Evidenz-IDs, die der betreffenden Aussage nicht zugeordnet waren; die Anwendung wies diese Bindung zurück. Diese drei Fälle bleiben als Fehler im finalen Datensatz; sie wurden nicht durch wiederholtes Probieren aus der Statistik entfernt.

In allen 56 Antworten: keine doppelten Evidenz-IDs oder Belegbindungen, keine falschen UTF-16-Zitatpositionen und keine Belegbindung an eine ungestützte Aussage. Jeder Linkkontext stimmt mit seiner endgültigen Aussage überein. Fehlgeschlagene Prüfungen geben keine Aussage als geprüft aus. Erfolgreich strukturiert bedeutet nicht, dass jeder Entwurf gestützt ist: abgewiesene Zahlen, unlesbare Einheiten und sonstige Lücken bleiben ausdrücklich markiert.

| Modell | Strukturierte Antwort | Median gesamt | Spanne |
|---|---:|---:|---:|
| DeepSeek V4 Flash | 28/28 | 21,0 s | 11,7–34,4 s |
| GLM 5.3 Flash | 25/28 | 42,4 s | 19,9–252,1 s |

Median je Fragenfamilie und Kontextmodus, jeweils zwei Wiederholungen; Fehlerlaufzeiten sind enthalten:

| Modell | Frage | KG (s) | PDF falls passend (s) |
|---|---|---:|---:|
| DeepSeek V4 Flash | Übersicht | 29,2 | 30,4 |
| DeepSeek V4 Flash | RA/PC und Summation | 21,8 | 18,1 |
| DeepSeek V4 Flash | Schwellen | 18,2 | 25,2 |
| DeepSeek V4 Flash | Weber-Bruch | 12,8 | 13,1 |
| DeepSeek V4 Flash | Autorenempfehlung | 17,8 | 17,6 |
| DeepSeek V4 Flash | Folgefrage | 26,9 | 21,6 |
| DeepSeek V4 Flash | Unbelegbare Frage | 12,0 | 24,5 |
| GLM 5.3 Flash | Übersicht | 82,1 | 214,3 (2/2 Fehler) |
| GLM 5.3 Flash | RA/PC und Summation | 43,5 | 26,1 |
| GLM 5.3 Flash | Schwellen | 38,5 | 48,0 |
| GLM 5.3 Flash | Weber-Bruch | 31,5 | 28,3 |
| GLM 5.3 Flash | Autorenempfehlung | 38,0 | 193,2 (1/2 Fehler) |
| GLM 5.3 Flash | Folgefrage | 92,0 | 50,9 |
| GLM 5.3 Flash | Unbelegbare Frage | 21,6 | 28,1 |

Die inhaltliche Sichtprüfung der gezielten Fragen bestätigt die Einordnung der 20–30-%-Empfehlung als Autorenerfahrung sowie die Trennung von referierten Befunden. Bei Fragen nach dem nicht belegten Autorenexperiment werden keine Teilnehmerzahl oder p-Werte als geprüft angegeben. Die Eingabe bleibt unabhängig von der Cloud-Laufzeit bedienbar.

Neun frühere GLM-5.3-Flash-Fehlversuche sind unter `glm53-before-router-fix`, `glm53-before-preamble-fix` und `glm53-before-gap-clarification` archiviert. Sie dokumentieren die Formatkorrekturen während der Umsetzung und zählen nicht als zusätzliche Wiederholungen der finalen Matrix. Die verbleibenden drei Fehler zeigen, dass diese Anpassungen die Ausgabe bei langen Kontexten noch nicht verlässlich machen. Ein erfolgreicher Empfehlungslauf dauerte ebenfalls 252,1 s, überwiegend für die Prüfung.

Ein zusätzlicher Diagnoseaufruf der PDF-Übersicht außerhalb dieser Matrix bestätigt eine Ursache: 20.189 Eingabetokens, alle 8.192 Ausgabetokens verbraucht, `done_reason=length`. Trotz angenommenem `think=false` und JSON-Modus lieferte GLM weiterhin Denktext und keine fertige Antwort. Nachweis: `diagnostics/glm53-final-context-output.json`. Anschließend ergänzt und mit sechs Offline-Regressionen geprüft: Der Antwortvertrag verwirft Ausgaben mit gemeldetem Tokenlimit oder ausschließlich zurückgegebenem Denktext, selbst wenn darin ein schema-gültiger Beispielentwurf steht. Ein tatsächlich abgeschlossener Reparaturversuch bleibt zulässig. Diese abschließende Schutzprüfung wurde nicht mit einer weiteren vollständigen Cloud-Matrix vermessen; sie behebt nicht das beobachtete Providerverhalten. Modell und konfiguriertes Tokenlimit bleiben erhalten.

Aggregierte Phasenzeiten, Aussagezustände und Fehlerfälle: `data/eval/workspace-contract-v5/browser/summary.json`. Das Abnahmeskript wertet auch gespeicherte Ergebnisse erneut aus und endet bei Fehlern mit Exit-Code 1.


## Reproduktion und Grenzen

```bash
.venv/bin/python -m pytest -q tests/test_answer_contract.py tests/test_llm_router_tools.py tests/test_phase4_query.py tests/test_source_verifier.py tests/test_pdf_guard.py tests/test_phase3_parsers.py tests/test_study_quality.py tests/test_product_api.py tests/test_priority_retrieval.py tests/test_context_budget.py tests/test_auto_answer.py tests/test_cross_language_retrieval.py tests/test_research_tree.py
.venv/bin/python scripts/eval_workspace_contract.py --output data/eval/workspace-contract-v5 --repeats 2
node scripts/benchmark_workspace_input.mjs SESSION.json INPUT.json
node scripts/check_workspace_citations.mjs ANSWER.json CITATION.json
npm --prefix frontend test -- --maxWorkers=2 --minWorkers=1
npm --prefix frontend run test:e2e
npm --prefix frontend run build
```

Die Cloud-Reihe benötigt das laufende Produkt-Backend auf Port 42849 und expliziten Zugriff auf die beiden Cloud-Modelle. Browsermessungen verwenden Vite auf Port 5173. Das Skript überspringt abgeschlossene Fälle; `--force` wiederholt sie. Fehlerhafte HTTP-Fälle werden beim Fortsetzen erneut versucht.

Die inhaltliche Stützung bleibt ein Modellurteil mit zusätzlichen deterministischen Kontrollen, kein unabhängiger wissenschaftlicher Wahrheitsbeweis. Das Haptic-PDF enthält beschädigte Zeichenzuordnungen (`(cid:2)m`); betroffene Größen werden daher als ungesichert behandelt. Randlose Tabellen werden textlich erhalten, ihre Zellstruktur jedoch nicht vollständig rekonstruiert. Der erste Aufbau eines großen Passagenindex kann dauern. „PDF falls passend“ nutzt bei zu großem Kontext nur passende Passagen und weist die tatsächliche Volltextverwendung in den Diagnosen aus. Eine unterbrochene SSE-Verbindung beendet die Veröffentlichung; ein bereits laufender Provideraufruf kann im Backend noch zu Ende laufen.
