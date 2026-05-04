# ScienceKG — Scientific Knowledge Graph System

**Vollständig lokales, privacy-preserving System zur automatisierten wissenschaftlichen Paper-Analyse, Knowledge-Graph-Konstruktion und LLM-gestützten Forschungsassistenz.**

> **📖 Dokumentationsstruktur**: Siehe [DOCUMENTATION.md](DOCUMENTATION.md) für Übersicht. Für Phase 3 Quick-Start: [QUICKSTART_PHASE3.md](QUICKSTART_PHASE3.md)

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

---

## Phase 2 Nutzung

### 1) Graph aus Metadaten bauen (API)

Hinweis: `kuzu` ist derzeit nur für Python < 3.14 als Wheel verfügbar. Unter Python 3.14 laufen alle anderen Module, aber der echte Kuzu-Graph-Build benötigt ein Python-Env mit 3.13 oder älter.

One-command Runner (startet API + UI und triggert den Phase-2-Build):

```bash
python scripts/run_phase2.py
```

Nur API:

```bash
python scripts/run_phase2.py --api-only
```

Nur UI:

```bash
python scripts/run_phase2.py --ui-only --skip-build
```

```bash
uvicorn api.main:app --reload
```

Danach Build-Endpoint ausführen:

```bash
curl -X POST "http://127.0.0.1:8000/graph/phase2/build" -H "Content-Type: application/json" -d "{}"
```

Optionale Vorschau für Co-Citation-Similarity:

```bash
curl "http://127.0.0.1:8000/graph/co-citation?min_shared=2&min_score=0.25"
```

### 2) Graph visualisieren (Streamlit)

```bash
streamlit run ui/graph_visualization.py
```

### 3) Tests für Phase 1 + 2

```bash
python -m pytest -q
```

---

## Phase 3: PDF Parsing & LLM-gestützte Entityexttraktion

### Features Phase 3

- ✅ **Multi-Provider LLM Router**: Ollama, LM Studio, OpenAI, beliebige OpenAI-kompatible APIs
- ✅ **Flexible LLM-Konfiguration**: Temperatur, Top-P, Max Tokens, Context Size pro Provider
- ✅ **Intelligente PDF-Parsing**: Automatische Parser-Auswahl basierend auf PDF-Charakteristiken
- ✅ **Entity Extraction**: LLM-basierte Konzept-, Methoden-, und Claims-Extraktion
- ✅ **Entity Linking**: Automatische Verknüpfung zu OpenAlex Concepts
- ✅ **Vocabulary Management**: Custom Entity-Normalisierung und Deduplication
- ✅ **Embedding Generation**: BGE-M3 Embeddings für semantic similarity
- ✅ **Conflict Detection**: LLM-basierte Widerspruchserkennung zwischen Claims
- ✅ **Batch Processing**: Verarbeitung mehrerer Papers mit Fehlerhandling
- ✅ **Phase 1 Harvest Tab**: Topic-Suche und PDF-Downloads direkt im Streamlit-Frontend
- ✅ **Live Model Discovery**: Ollama- und OpenAI-kompatible Modelllisten können im UI aktualisiert werden

Die Graphansicht bleibt bewusst in Phase 2 (`ui/graph_visualization.py`), während Phase 3 sich auf Parsing, Modellwahl, Kontextsteuerung und Entity Extraction konzentriert.

### LLM-Konfiguration

Phase 3 wird über `config.yaml` konfiguriert:

```yaml
llm:
  default_provider: "ollama"
  
  providers:
    ollama:
      provider_type: "ollama"
      base_url: "http://localhost:11434"
      model: "qwen3.6-35b"
      temperature: 0.2
      top_p: 0.95
      max_tokens: 2048
      context_size: 32768
      repeat_penalty: 1.05
    
    lm_studio:
      provider_type: "openai_compatible"
      base_url: "http://localhost:1234/v1"
      model: "qwen3.6-35b"
      temperature: 0.2
      top_p: 0.95
      max_tokens: 2048
    
    openai:
      provider_type: "openai_compatible"
      base_url: "https://api.openai.com/v1"
      api_key_env: "OPENAI_API_KEY"
      model: "gpt-4o"
      temperature: 0.3
      max_tokens: 4096
      context_size: 128000
```

**Umgebungsvariablen**: API Keys können über `api_key_env: "ENV_VAR_NAME"` aus der Umgebung geladen werden.

### Verwendung Phase 3

#### One-Command Launcher

```bash
# Startet API (Port 8000) + UI (Port 8501)
python scripts/run_phase3.py

# Nur API
python scripts/run_phase3.py --api-only

# Nur UI
python scripts/run_phase3.py --ui-only

# Custom API Port
python scripts/run_phase3.py --api-port 9000
```

#### Programmatische Nutzung

```python
from query.llm_router import LLMRouter
from extraction.entity_extractor import EntityExtractor
from extraction.entity_linker import ExtractionPipeline

# Initialisierung
llm_router = LLMRouter.from_config_file("config.yaml")
pipeline = ExtractionPipeline(llm_router)

# Entity Extraction mit Provider-Auswahl
paper_text = "Das Paper Text..."
result = pipeline.process(
    paper_id="arxiv_2024_001",
    text=paper_text,
    provider="ollama",  # oder "lm_studio", "openai", etc.
    overrides={
        "temperature": 0.1,  # Für diese Extraktion: niedrigere Temperatur
        "max_tokens": 4096
    },
    link_concepts=True  # Automatische OpenAlex-Verknüpfung
)

print(f"Concepts: {len(result.concepts)}")
print(f"Methods: {len(result.methods)}")
print(f"Claims: {len(result.claims)}")
```

#### Parser Auswahl

Das System wählt automatisch den besten Parser:

```python
from parsing.parser_router import ParserRouter, ParserType

router = ParserRouter()

# Automatische Parser-Auswahl basierend auf PDF-Inhalt
parsed = router.parse("/path/to/paper.pdf", "paper_id")
# Nougat für formeln-schwere Papers
# Table Transformer für tabellen-lastige Papers
# VLM für diagramm-intensive Papers
# Fallback zu Marker

# Oder explizit:
parsed = router.parse(
    "/path/to/paper.pdf",
    "paper_id",
    force_parser=ParserType.NOUGAT
)
```

#### Batch Processing

```python
from extraction.batch_processor import BatchProcessor

processor = BatchProcessor(llm_router, parser_router)

# Mehrere Papers verarbeiten
status = processor.process_papers(
    paper_ids=["arxiv_001", "arxiv_002", "arxiv_003"],
    pdf_paths={
        "arxiv_001": "/path/to/paper1.pdf",
        "arxiv_002": "/path/to/paper2.pdf",
        "arxiv_003": "/path/to/paper3.pdf",
    },
    llm_provider="ollama",
    llm_overrides={"temperature": 0.1}
)

print(f"Job {status.job_id}: {status.papers_processed}/{status.papers_total} complete")
```

#### Vocabulary Management

```python
from extraction.vocabulary import VocabularyManager

vocab = VocabularyManager()

# Entry registrieren
vocab.register(
    "Neural Network",
    aliases=["NN", "neural net"],
    openalx_id="C123",
    domain="Machine Learning"
)

# Normalisieren
canonical = vocab.normalize("neural net")  # → "Neural Network"

# Deduplication
vocab.merge_entries("Deep Learning", "Neural Network")

# Export/Import
data = vocab.to_dict()
vocab2 = VocabularyManager.from_dict(data)
```

#### Conflict Detection

```python
from extraction.conflict_detector import ConflictDetector

detector = ConflictDetector(llm_router)

# Zwei Claims analysieren
analysis = detector.analyze_claim_pair(
    "Climate change is accelerating",
    "Climate change is slowing",
    provider="ollama"
)

print(f"Type: {analysis.conflict_type}")  # "contradictory"
print(f"Confidence: {analysis.confidence}")  # 0.95

# Mehrere Claims analysieren
claims = ["Claim A", "Claim B", "Claim C"]
analyses = detector.analyze_claims_batch(claims)

# Widersprüche filtern
contradictions = detector.find_contradictions(analyses, confidence_threshold=0.7)
```

### Phase 3 API-Endpunkte

| Methode | Endpoint | Beschreibung |
|---|---|---|
| GET | `/health` | Health check |
| GET | `/extraction/providers` | Verfügbare LLM-Provider |
| POST | `/extraction/extract` | Entity Extraction aus Text |
| POST | `/extraction/batch` | Batch-Job starten |
| GET | `/extraction/batch/{job_id}` | Job-Status abrufen |
| GET | `/extraction/jobs` | Alle Jobs auflisten |

### Tests

Phase 3 Tests (26 Tests):

```bash
python -m pytest tests/test_phase3_extraction.py -v
```

Alle Tests (39 gesamt):

```bash
python -m pytest -v
```

---

### Lizenz

MIT
