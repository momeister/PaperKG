# CodeSearch

Ein Werkzeug, um zu verstehen, **warum** eine fremde Codebase so funktioniert, wie sie funktioniert — ohne dafür selbst tief in den Code einsteigen zu müssen.

## Die Grundregel

Der Code-Graph ist die einzige Wahrheitsquelle, und **keine Kante existiert ohne Beleg**. `Edge::new` in `cs-core` lässt sich nicht ohne eine `Evidence` aufrufen, die auf die Quellzeile zeigt, die die Beziehung belegt. Zusätzlich trägt jede Kante ihre Sicherheitsstufe:

| Stufe | Verfahren | Anzeige |
|---|---|---|
| **verifiziert** | Language Server | ● |
| **aufgelöst** | Imports, Sichtbarkeitsbereich, Vererbung, Bindungen | ◐ |
| **vermutet** | gewichteter Namensabgleich, mit Kandidatenzahl | ○ |
| **gemessen** | Laufzeitaufzeichnung (Phase 2) | ◆ |

Was statisch prinzipiell nicht auflösbar ist — Reflection, `eval`, Dependency Injection — wird als sichtbare **Lücke** in den Graphen geschrieben, nicht stillschweigend weggelassen und nicht erfunden.

## Was funktioniert

- **Index** für 13 Sprachen (Python, JS, TS, TSX, Go, Rust, Java, C, C++, C#, Ruby, PHP, Bash), inkrementell über Inhalts-Hashes
- **Auflösung** in drei Stufen mit Vererbungskette, Re-Exporten und Konstruktor-Bindungen
- **Relevanz** aus PageRank, Reichweite, git-Churn, Risiko und Abdeckung — mit aufklappbarer Herleitung
- **Oberfläche**: Überblick, Karte (WebGL), Bauplan, Code, Nebeneinander, Diagramm, Befehlspalette
- **Terminal** mit echtem PTY; erkannte `datei:zeile` in der Ausgabe werden zu Sprungmarken in den Graphen
- **Diagramme** als Mermaid und SVG, geratene Aufrufe gestrichelt
- **Begleiter** mit lokalen Modellen und geprüften Belegen
- **CLI** für kopfloses Indizieren, Diagnose und Fragen

## Der Begleiter

Das Modell sieht den Code nicht. Es hat acht Werkzeuge auf den Graphen — suchen, Nachbarn ansehen, Pfad finden, Zeilen lesen — und jede Werkzeugantwort trägt die Sicherheitsstufe der Beziehung mit. Es kann eine Vermutung also gar nicht als Tatsache darstellen, ohne dass die Antwort im Widerspruch zu dem steht, was es selbst bekommen hat.

Belege werden **geprüft, nicht erbeten**. Vor dem Anzeigen einer Antwort prüft `cs-llm::citation`:

1. Existiert die Datei im Index?
2. Wurden genau diese Zeilen in diesem Gespräch nachgeschlagen?
3. Ist die Datei seither unverändert?
4. Stimmt ein wörtliches Zitat mit den Bytes überein?

Was durchfällt, wird markiert — als Chip in der Antwort und als Zahl in der Fußzeile. `crates/cs-llm/tests/hallucination.rs` deckt jede Fabrikationsart einzeln ab.

**Modelle**: gefunden wird nur über Loopback — Ollama (11434), LM Studio (1234), llama.cpp (8080), vLLM (8000). Ein Anbieter im Netz muss von Hand eingetragen werden und ist überall sichtbar als „extern" markiert. Ohne Modell funktioniert alles außer Chat und formulierten Erklärungen.

Ein 7B-Modell reicht inhaltlich: die Suche im Graphen macht die Arbeit, das Modell navigiert und fasst zusammen.

**Zur Geschwindigkeit, gemessen statt geschätzt.** Auf reiner CPU-Inferenz (qwen3.5:4b, 8 Kerne, keine GPU) dauert ein Modellaufruf rund zwei Minuten. Eine Frage braucht drei bis fünf davon, also etwa zehn Minuten. Die Navigation ist dabei korrekt — das Modell sucht die richtigen Bezeichner und folgt den richtigen Kanten — aber es ist nichts, worauf man wartet. Mit GPU-Beschleunigung fällt das auf Sekunden. Alle anderen Ansichten sind davon unberührt und antworten sofort.

## Gemessen

| | Flask (Python, 84 Dateien) | CodeSearch selbst (Rust + TS, 47) |
|---|---|---|
| Erstes Einlesen | 2,7 s · 68 MB RSS | 0,9 s |
| Zweiter Lauf | 0 geparst, 441 ms | inkrementell über Inhalts-Hashes |
| Kanten | 3221 | 1698 |
| davon vermutet | 30 % | 31 % |
| dynamische Lücken | 39 sichtbar | 0 |

Der Vermutungsanteil ist eine Verhältniszahl: mehr belegte Struktur im Graphen senkt ihn, ohne dass eine einzige Vermutung verschwindet. Die absolute Zahl steht in `cs stats` daneben.

## Bauen und starten

Voraussetzungen: Rust ≥ 1.82, Node ≥ 20. Unter Linux zusätzlich `libwebkit2gtk-4.1-dev`, `libsoup-3.0-dev`, `build-essential`, `pkg-config`.

```bash
cd web && npm install          # einmalig
npm run tauri dev              # Entwicklung mit Hot Reload
npm run tauri build            # Pakete für das eigene System
```

## Ohne Fenster: das CLI

Nützlich zum Prüfen der Analyse, für Skripte und wenn etwas nicht stimmt.

```bash
cargo build --release --bin cs

./target/release/cs index /pfad/zum/projekt   # einlesen
./target/release/cs top   /pfad/zum/projekt   # die relevantesten Symbole
./target/release/cs find  /pfad/zum/projekt suchbegriff
./target/release/cs show  /pfad/zum/projekt funktionsname
./target/release/cs stats /pfad/zum/projekt   # wie viel davon ist Vermutung?

# Frage an den Begleiter, inklusive Belegprüfung im Klartext
CS_MODEL=qwen3.5:4b ./target/release/cs ask /pfad/zum/projekt "warum ist X so?"
```

`cs show` zeigt Fakten, Relevanz mit Aufschlüsselung sowie Aufrufer und Aufgerufene, jeweils mit Sicherheitsstufe und Belegstelle.

## Aufbau

```
crates/
  cs-core       Domänenmodell · stabile IDs · die Beleg-Invariante
  cs-index      tree-sitter · Sprachpakete · Dateiauswahl
  cs-graph      SQLite (WAL, FTS5-Trigramm) · Traversierung
  cs-resolve    dreistufige Auflösung · Modulpfade
  cs-rank       PageRank · Perzentil-Normalisierung
  cs-git        Churn, Risiko, Autoren über gix
  cs-llm        Anbieter · Werkzeuge auf dem Graphen · Zitatprüfung
  cs-pty        echtes Pseudo-Terminal · Positionen in der Ausgabe
  cs-workspace  die Pipeline · das CLI
languages/      <sprache>/tags.scm + manifest.toml
web/            React + TypeScript
web/src-tauri/  Desktop-Schale und IPC
```

Eine Sprache hinzuzufügen heißt: eine `tags.scm`, eine `manifest.toml` und eine Zeile in `registry.rs`. Resolver, Ranking und Oberfläche bleiben unberührt.

## Tests

```bash
cargo test --workspace
cd web && npx tsc --noEmit -p tsconfig.app.json
```

Der Test `every_language_pack_compiles` ist die Absicherung gegen die unangenehmste Fehlerklasse: eine Tag-Query, die einen Knotentyp nennt, den die Grammatik nicht kennt, schlägt komplett fehl und kostet lautlos alle Symbole dieser Sprache.

## Was bewusst noch fehlt

Laufzeit-Tracing (echte Aufrufzahlen, gemessene Sequenzdiagramme, Speicheransicht), der Schreibmodus im Editor, die Datenbank-/Tabellenansicht, Auswirkungsanalyse und der MCP-Server. Paketiert und gestartet ist bisher nur Linux.

Der Plan dafür liegt in `~/.claude/plans/`. Das Graph-Schema ist bereits darauf ausgelegt: `Confidence::Measured` und die `hits`-Spalte warten auf Phase 2, und ein gemessener Aufruf schlägt jede statische Aussage automatisch, weil die Sicherheitsstufen geordnet sind.
