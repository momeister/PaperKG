# ScienceKG — Scientific Knowledge Graph System

**Vollständig lokales, privacy-preserving System zur automatisierten wissenschaftlichen Paper-Analyse, Knowledge-Graph-Konstruktion und LLM-gestützten Forschungsassistenz.**

## Phase 1: Harvester & Storage Foundation

Diese Phase implementiert das automatisierte Paper-Harvesting und strukturierte lokale Speicherung.

### Features nach Phase 1

- ✅ Automatisches Paper-Download von arXiv, Semantic Scholar, OpenAlex, Unpaywall
- ✅ Duplikat-Erkennung via DOI-Fingerprinting + normalisiertem Titel
- ✅ Versionsverwaltung (arXiv-Versionierungen korrekt erfasst)
- ✅ Strukturierte Metadaten in DuckDB
- ✅ Lokale PDF-Ablage mit Versionierung
- ✅ Cross-Source-Linking (dieselbe Paper aus mehreren APIs)
- ✅ Rate-Limiting für alle APIs
- ✅ Async-First Architecture (schnelle Batch-Verarbeitung)

### Technologie-Stack Phase 1

| Komponente | Tool | Version |
|---|---|---|
| HTTP Client | `httpx` | 0.27.0 |
| PDF Parsing | `feedparser` | 6.0.11 |
| Metadata DB | `duckdb` | 1.5.2 |
| Configuration | `pyyaml` | 6.0.1 |
| Async Runtime | Python `asyncio` | 3.10+ |

### Installation

```bash
pip install -r requirements.txt
```

### Verwendung

#### Schnelltest

Wenn du Phase 1 direkt ausprobieren willst, nutze das Demo-Script:

```bash
python scripts/try_phase1.py "machine learning" --max-results 10
```

Für einen vollständigen Phase-1-Durchlauf (alle Harvester + Dedup + Storage + optional Download):

```bash
python scripts/try_phase1.py "machine learning" --max-results 10 --full-phase1
python scripts/try_phase1.py "machine learning" --max-results 10 --full-phase1 --download
```

Das Script:
- fragt arXiv nach Papers ab
- kann zusätzlich Semantic Scholar, OpenAlex, PapersWithCode und Unpaywall prüfen
- dedupliziert die Treffer
- speichert die Metadaten in `data/metadata.duckdb`
- zeigt dir die ersten Ergebnisse inkl. Speicherpfad
- zeigt am Ende eine Phase-1-Zusammenfassung der einzelnen Komponenten

### Tests

Automatisierte Tests für Phase 1 laufen mit `pytest`:

```bash
pytest -q
```

Die Suite prüft u. a.:
- Deduplication (DOI + Titel-Normalisierung)
- FileManager (save/load/list/delete)
- MetadataDB (Insert, Query, dedup_log, leere Placeholder-DB)
- Demo-Flow mit gemockten Clients (vollständiger `--full-phase1`-Pfad)

#### 1. Harvester initialisieren

```python
from harvester.arxiv_client import ArxivClient, ArxivClientConfig
from storage.metadata_db import MetadataDB
from storage.file_manager import FileManager

# Clients aufsetzen
arxiv = ArxivClient(ArxivClientConfig())
metadata_db = MetadataDB("data/metadata.duckdb")
file_mgr = FileManager("data/pdfs")
```

#### 2. Nach Papers suchen

```python
import asyncio

async def search_and_store():
    # arXiv Search
    papers = await arxiv.search("machine learning", max_results=10)
    
    # In Datenbank speichern
    metadata_db.batch_insert_papers(papers)
    
    # Later: PDFs herunterladen und speichern
    await arxiv.close()

asyncio.run(search_and_store())
```

#### 3. Deduplication

```python
from harvester.deduplication import deduplicate_papers

# Laden aller Papers
records = metadata_db.list_papers(limit=10000)

# Deduplizieren
unique, decisions = deduplicate_papers(records)
print(f"Unique: {len(unique)}, Dropped: {len(decisions)}")

# Decisions in Log speichern
for decision in decisions:
    metadata_db.log_dedup(
        decision.keep["id"],
        decision.dropped[0]["id"],
        decision.reason
    )
```

### Datenbankschema (DuckDB)

#### `papers` Table

```
id              VARCHAR (PRIMARY KEY)
source          VARCHAR (arxiv, semantic_scholar, openalex, ...)
source_id       VARCHAR (eindeutige ID aus der Quelle)
title           VARCHAR
abstract        VARCHAR
authors         JSON (Array von Author-Namen)
year            INTEGER
doi             VARCHAR (nullable)
pdf_url         VARCHAR (nullable)
landing_page_url VARCHAR
has_full_text   BOOLEAN
version         INTEGER (für arXiv-Versionierung)
added_timestamp TIMESTAMP
updated_timestamp TIMESTAMP
```

#### `dedup_log` Table

```
id              INTEGER (PRIMARY KEY)
kept_id         VARCHAR (welche Paper behalten wurde)
dropped_id      VARCHAR (welche Paper gelöscht wurde)
reason          VARCHAR (same_doi | same_title)
timestamp       TIMESTAMP
```

### Struktur der Module

```
harvester/
  ├── arxiv_client.py                  # arXiv API Wrapper
  ├── semantic_scholar_client.py       # Semantic Scholar API Wrapper
  ├── openalex_client.py               # OpenAlex API Wrapper
  ├── unpaywall_client.py              # Unpaywall API Wrapper
  ├── papers_with_code_client.py       # Papers with Code API (optional)
  └── deduplication.py                 # DOI + Title-based Dedup

storage/
  ├── file_manager.py                  # Lokale PDF-Verwaltung
  └── metadata_db.py                   # DuckDB Metadata Layer

config.yaml                             # Konfiguration (API Keys, Pfade)
requirements.txt                        # Python Dependencies
```

### API Rate Limits (beachtet)

| API | Limit | Implementiert |
|---|---|---|
| arXiv | 3 req/s (1 request all 3s) | ✅ |
| Semantic Scholar | 100 req/s (mit API Key) | ✅ |
| OpenAlex | 100,000 req/day | ✅ |
| Unpaywall | 100,000 req/day | ✅ |
| Papers with Code | Variabel | ✅ |

### Fehlerbehandlung

Alle Clients implementieren:
- HTTP Status Code Handling (Retry bei 429, 503)
- Timeouts (30s default)
- Graceful Degradation bei fehlenden APIs

### Nächste Schritte (Phase 2+)

1. **Phase 2**: Flacher Citation-Graph (Kuzu + Citation-Edges)
2. **Phase 3**: PDF-Parsing + LLM-Extraktion (Marker, Qwen)
3. **Phase 4**: Query-Engine + Chat-UI
4. **Phase 5**: Quality Assurance + Automation

### Lizenz

MIT
