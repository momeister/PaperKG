# ScienceKG - Dokumentationsstruktur

## 📋 Projektübersicht

Dieses Dokument erklärt die Dokumentationsstruktur für das **ScienceKG Knowledge Graph System**.

---

## 🎯 Haupt-Dokumentationen

### [README.md](README.md) - **START HERE**
- ✅ Phase 1: Harvester & Storage Foundation
- ✅ Phase 2: Citation Graph & Co-Citation Analysis  
- ✅ Phase 3: PDF Parsing & LLM Entity Extraction
- ✅ Phase 1, 2, 3 Verwendungsbeispiele
- ✅ Test-Ergebnisse (39/39 ✅)
- ✅ Technologie-Stack pro Phase

**👉 Nutze README.md für die meisten Fragen zur Verwendung**

---

## 📚 Phase-spezifische Dokumentationen

### Phase 1: Harvester & Storage
- Keine separaten Dateien - alles in README.md
- Tests: `tests/test_deduplication.py`, `tests/test_metadata_db.py`, `tests/test_file_manager.py`

### Phase 2: Citation Graph  
- Keine separaten Dateien - alles in README.md
- Tests: `tests/test_phase2_graph.py`

### Phase 3: PDF Parsing & LLM Entity Extraction
- **[QUICKSTART_PHASE3.md](QUICKSTART_PHASE3.md)** - Wie man Phase 3 nutzt (bevorzugt!)
  - Konkrete Beispiele
  - Troubleshooting
  - Performance Tips
  - API curl-Befehle
  
- [README.md](README.md#phase-3) - Allgemeine Phase 3 Info (in Main README integriert)

**Nicht mehr nutzen:**
- ~~PHASE3_SUMMARY.md~~ (redundant)
- ~~PHASE3_STATUS.md~~ (redundant)
- ~~PHASE3_COMPLETION_REPORT.md~~ (Archiv-Info, nicht für Nutzer)

---

## 🔧 Konfiguration

### [config.yaml](config.yaml)
- **Phase 1 Section**: Harvester APIs (arXiv, Semantic Scholar, OpenAlex, Unpaywall)
- **Phase 2 Section**: Graph Speicherung (DuckDB, Kuzu)
- **Phase 3 Section**: LLM Provider (Ollama, LM Studio, OpenAI)
- **Logging & Rate Limiting**: Global

---

## 📖 Empfohlen Leseanleitung

### Für Anfänger: Phase 1 verstehen
1. Lese [README.md - Phase 1 Section](README.md#phase-1-harvester--storage-foundation)
2. Probiere: `python scripts/try_phase1.py "machine learning" --max-results 10`
3. Schau die Tests: `pytest tests/test_deduplication.py -v`

### Für Mittere Nutzer: Phase 2 nutzen
1. Lese [README.md - Phase 2 Section](README.md#phase-2-nutzung)
2. Starte: `python scripts/run_phase2.py`
3. Besuche UI: http://localhost:8501

### Für Fortgeschrittene: Phase 3 mit LLMs
1. Lese [QUICKSTART_PHASE3.md](QUICKSTART_PHASE3.md)
2. Konfiguriere dein LLM in [config.yaml](config.yaml) (Ollama, LM Studio, oder OpenAI)
3. Starte: `python scripts/run_phase3.py`
4. Probiere: `curl http://localhost:8000/docs`

### Für Entwickler: Architektur verstehen
1. Schau [README.md Technologie-Stack](README.md)
2. Lese die Python Docstrings in den Modulen
3. Führe Tests aus: `pytest -v`
4. Debugge mit: `python -c "from extraction.entity_extractor import EntityExtractor; help(EntityExtractor)"`

---

## 📂 Dateiorganisation

```
ScienceKG/
├── README.md                    ← START HERE (alle Phasen)
├── QUICKSTART_PHASE3.md         ← Phase 3 How-To
├── config.yaml                  ← Konfiguration (alle Phasen)
│
├── harvester/                   ← Phase 1: APIs
├── storage/                     ← Phase 1: DuckDB, PDFs
├── extraction/                  ← Phase 3: Entity Extraction
├── parsing/                     ← Phase 3: PDF Parsing
├── query/                       ← Phase 3: LLM Router
├── graph/                       ← Phase 2: Citation Graph
│
├── api/
│   ├── main.py                  ← Phase 2 API
│   └── phase3_main.py           ← Phase 3 API
│
├── ui/
│   ├── graph_visualization.py   ← Phase 2 UI
│   └── phase3_extraction.py     ← Phase 3 UI
│
├── scripts/
│   ├── run_phase2.py            ← Phase 2 One-Commander
│   ├── run_phase3.py            ← Phase 3 One-Commander
│   └── try_phase1.py            ← Phase 1 Demo
│
├── tests/                       ← Alle 39 Tests
│
└── requirements.txt             ← Python Dependencies
```

---

## ✅ Test-Status

| Phase | Tests | Status |
|-------|-------|--------|
| Phase 1 | 7 | ✅ All Pass |
| Phase 2 | 6 | ✅ All Pass |
| Phase 3 | 26 | ✅ All Pass |
| **Total** | **39** | **✅ 100%** |

**Run Tests:**
```bash
pytest                              # Alle
pytest tests/test_phase2_graph.py   # Phase 2 only
pytest tests/test_phase3_extraction.py -v  # Phase 3 verbose
```

---

## 🚀 Quick Start Commands

```bash
# Phase 1: Search & Store Papers
python scripts/try_phase1.py "machine learning" --full-phase1

# Phase 2: Build Citation Graph & Visualize
python scripts/run_phase2.py

# Phase 3: Extract Entities & Use LLM
python scripts/run_phase3.py
```

---

## 🔗 Häufig gestellte Fragen

### "Warum sind meine Tests fehlgeschlagen?"
→ Schau die Error Message, dann [README.md Troubleshooting Section](README.md)

### "Wie wechsle ich zwischen Ollama, LM Studio, OpenAI?"
→ [QUICKSTART_PHASE3.md - Configuration](QUICKSTART_PHASE3.md#configuration-switch-llm-providers)

### "Wie verwende ich Phase 3 programmatisch?"
→ [QUICKSTART_PHASE3.md - Common Tasks](QUICKSTART_PHASE3.md#common-tasks)

### "Wie funktioniert die Co-Citation-Similarity in Phase 2?"
→ [README.md - Phase 2 Section](README.md#phase-2-nutzung)

### "Kann ich die Batch-Größe für Phase 3 ändern?"
→ [QUICKSTART_PHASE3.md - Troubleshooting](QUICKSTART_PHASE3.md#troubleshooting)

---

## 📝 Dokumentations-Philosophie

- **README.md**: Hauptreferenz für alle Phasen
- **QUICKSTART_PHASE3.md**: Spezifisches How-To für Phase 3 
- **config.yaml**: Ausführliche Beispiele inline als Kommentare
- **Tests**: Live-Dokumentation durch Test-Beispiele
- **Inline Docstrings**: In jedem Python-Modul

---

## 🛠️ Für Maintainer

### Wo dokumentiere ich Änderungen?

| Was | Wo |
|-----|-----|
| Neue API Endpoints | [README.md Phase 4+](README.md) + inline Docstring |
| Neue Config-Optionen | [config.yaml](config.yaml) als Kommentar |
| Phase 3 How-To | [QUICKSTART_PHASE3.md](QUICKSTART_PHASE3.md) |
| Architektur-Details | README + inline Docstring |
| Bugs/Fixes | Inline Code-Kommentar + Git Commit |

---

## 📞 Support

- **Für Fragen zu Phase 1-3**: README.md
- **Für How-To Phase 3**: QUICKSTART_PHASE3.md  
- **Für API Details**: `http://localhost:8000/docs` (FastAPI SwaggerUI)
- **Für Architektur**: Inline Docstrings in den Python-Dateien
- **Für Bugs**: Tests ausführen mit `-v` Flag

---

## 🎓 Nächste Schritte (Phase 4+)

Phase 4 & 5 werden ebenfalls dokumentiert in:
- README.md (Hauptdokumentation)
- QUICKSTART_PHASE4.md (Phase 4 How-To)
- (keine Redundanz zwischen Dateien!)

---

**Letzte Aktualisierung**: Nach Phase 3 Completion
**Status**: ✅ Dokumentation konsolidiert und optimiert
