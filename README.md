# ScienceKG â€” Scientific Knowledge Graph System

**VollstÃ¤ndig lokales, privacy-preserving System zur automatisierten wissenschaftlichen Paper-Analyse, Knowledge-Graph-Konstruktion und LLM-gestÃ¼tzten Forschungsassistenz.**

> Dokumentation: `README.md` ist die Hauptuebersicht. `QUICKSTART_PHASE3.md` ist die praktische Phase-3-Anleitung. `ScienceKG_Projektplan.md` beschreibt die Roadmap, `MEMORY.md` den aktuellen Implementierungsstand.

## Phase 1: Harvester & Storage Foundation

Diese Phase implementiert das automatisierte Paper-Harvesting und strukturierte lokale Speicherung.

### Features nach Phase 1

- âœ… Automatisches Paper-Download von arXiv, Semantic Scholar, OpenAlex, Unpaywall
- âœ… Duplikat-Erkennung via DOI-Fingerprinting + normalisiertem Titel
- âœ… Versionsverwaltung (arXiv-Versionierungen korrekt erfasst)
- âœ… Strukturierte Metadaten in DuckDB
- âœ… Lokale PDF-Ablage mit Versionierung
- âœ… Cross-Source-Linking (dieselbe Paper aus mehreren APIs)
- âœ… Rate-Limiting fÃ¼r alle APIs
- âœ… Async-First Architecture (schnelle Batch-Verarbeitung)

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

FÃ¼r einen vollstÃ¤ndigen Phase-1-Durchlauf (alle Harvester + Dedup + Storage + optional Download):

```bash
python scripts/try_phase1.py "machine learning" --max-results 10 --full-phase1
python scripts/try_phase1.py "machine learning" --max-results 10 --full-phase1 --download
```

Das Script:
- fragt arXiv nach Papers ab
- kann zusÃ¤tzlich Semantic Scholar, OpenAlex, PapersWithCode und Unpaywall prÃ¼fen
- dedupliziert die Treffer
- speichert die Metadaten in `data/metadata.duckdb`
- zeigt dir die ersten Ergebnisse inkl. Speicherpfad
- zeigt am Ende eine Phase-1-Zusammenfassung der einzelnen Komponenten

### Tests

Automatisierte Tests fÃ¼r Phase 1 laufen mit `pytest`:

```bash
pytest -q
```

Die Suite prÃ¼ft u. a.:
- Deduplication (DOI + Titel-Normalisierung)
- FileManager (save/load/list/delete)
- MetadataDB (Insert, Query, dedup_log, leere Placeholder-DB)
- Demo-Flow mit gemockten Clients (vollstÃ¤ndiger `--full-phase1`-Pfad)

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
version         INTEGER (fÃ¼r arXiv-Versionierung)
added_timestamp TIMESTAMP
updated_timestamp TIMESTAMP
```

#### `dedup_log` Table

```
id              INTEGER (PRIMARY KEY)
kept_id         VARCHAR (welche Paper behalten wurde)
dropped_id      VARCHAR (welche Paper gelÃ¶scht wurde)
reason          VARCHAR (same_doi | same_title)
timestamp       TIMESTAMP
```

### Struktur der Module

```
harvester/
  â”œâ”€â”€ arxiv_client.py                  # arXiv API Wrapper
  â”œâ”€â”€ semantic_scholar_client.py       # Semantic Scholar API Wrapper
  â”œâ”€â”€ openalex_client.py               # OpenAlex API Wrapper
  â”œâ”€â”€ unpaywall_client.py              # Unpaywall API Wrapper
  â”œâ”€â”€ papers_with_code_client.py       # Papers with Code API (optional)
  â””â”€â”€ deduplication.py                 # DOI + Title-based Dedup

storage/
  â”œâ”€â”€ file_manager.py                  # Lokale PDF-Verwaltung
  â””â”€â”€ metadata_db.py                   # DuckDB Metadata Layer

config.yaml                             # Konfiguration (API Keys, Pfade)
requirements.txt                        # Python Dependencies
```

### API Rate Limits (beachtet)

| API | Limit | Implementiert |
|---|---|---|
| arXiv | 3 req/s (1 request all 3s) | âœ… |
| Semantic Scholar | 100 req/s (mit API Key) | âœ… |
| OpenAlex | 100,000 req/day | âœ… |
| Unpaywall | 100,000 req/day | âœ… |
| Papers with Code | Variabel | âœ… |

### Fehlerbehandlung

Alle Clients implementieren:
- HTTP Status Code Handling (Retry bei 429, 503)
- Timeouts (30s default)
- Graceful Degradation bei fehlenden APIs

### Aktueller Phasenstatus

1. **Phase 1**: Implementiert - Harvester, Deduplication, DuckDB-Metadaten und lokale PDF-Ablage
2. **Phase 2**: Implementiert - lokaler Kuzu-Citation-Graph, Co-Citation-Similarity und Graph-UI
3. **Phase 3**: Implementiert - PDF-Parsing, LLM-Extraktion, Entity Linking, Batch-Verarbeitung und Streamlit-UI
4. **Phase 4**: Implementiert - lokaler Query-Assistent mit KG/Hybrid-Retrieval, grounded Answers, Hypothesen, API und UIs
5. **Phase 5**: Noch nicht als Produktionsphase implementiert - einzelne Qualitaets- und Wartungsmodule existieren als Vorarbeit

---

## Phase 2 Nutzung

### 1) Graph aus Metadaten bauen (API)

Hinweis: `kuzu` ist derzeit nur fÃ¼r Python < 3.14 als Wheel verfÃ¼gbar. Unter Python 3.14 laufen alle anderen Module, aber der echte Kuzu-Graph-Build benÃ¶tigt ein Python-Env mit 3.13 oder Ã¤lter.

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

Danach Build-Endpoint ausfÃ¼hren:

```bash
curl -X POST "http://127.0.0.1:8000/graph/phase2/build" -H "Content-Type: application/json" -d "{}"
```

Optionale Vorschau fÃ¼r Co-Citation-Similarity:

```bash
curl "http://127.0.0.1:8000/graph/co-citation?min_shared=2&min_score=0.25"
```

### 2) Graph visualisieren (Streamlit)

```bash
streamlit run ui/graph_visualization.py
```

### 3) Tests fÃ¼r Phase 1 + 2

```bash
python -m pytest -q
```

---

## Phase 3: PDF Parsing & LLM-gestÃ¼tzte Entityexttraktion

### Features Phase 3

- âœ… **Multi-Provider LLM Router**: Ollama, LM Studio, OpenAI, beliebige OpenAI-kompatible APIs
- âœ… **Flexible LLM-Konfiguration**: Temperatur, Top-P, Max Tokens, Context Size pro Provider
- âœ… **Intelligente PDF-Parsing**: Automatische Parser-Auswahl basierend auf PDF-Charakteristiken
- âœ… **Entity Extraction**: LLM-basierte Konzept-, Methoden-, und Claims-Extraktion
- âœ… **Entity Linking**: Automatische VerknÃ¼pfung zu OpenAlex Concepts
- âœ… **Vocabulary Management**: Custom Entity-Normalisierung und Deduplication
- âœ… **Embedding Generation**: BGE-M3 Embeddings fÃ¼r semantic similarity
- âœ… **Conflict Detection**: LLM-basierte Widerspruchserkennung zwischen Claims
- âœ… **Batch Processing**: Verarbeitung mehrerer Papers mit Fehlerhandling
- âœ… **Phase 1 Harvest Tab**: Topic-Suche und PDF-Downloads direkt im Streamlit-Frontend
- âœ… **Live Model Discovery**: Ollama- und OpenAI-kompatible Modelllisten kÃ¶nnen im UI aktualisiert werden

Die Graphansicht bleibt bewusst in Phase 2 (`ui/graph_visualization.py`), wÃ¤hrend Phase 3 sich auf Parsing, Modellwahl, Kontextsteuerung und Entity Extraction konzentriert.

### LLM-Konfiguration

Phase 3 wird Ã¼ber `config.yaml` konfiguriert:

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

    gemini:
      provider_type: "openai_compatible"
      base_url: "https://generativelanguage.googleapis.com/v1beta/openai"
      api_key_env: "GEMINI_API_KEY"
      model: "gemini-3.1-flash-lite"
      temperature: 0.05
      top_p: 0.8
      max_tokens: 16384
      context_size: 1048576
      extra_options:
        force_response_format: true
        omit_extra_body: true

    nvidia:
      provider_type: "nvidia"
      base_url: "https://integrate.api.nvidia.com/v1"
      api_key_env: "NVIDIA_API_KEY"
      model: "moonshotai/kimi-k2.6"
      temperature: 0.05
      top_p: 0.8
      max_tokens: 16384
      context_size: 256000
```

**Umgebungsvariablen**: API Keys kÃ¶nnen Ã¼ber `api_key_env: "ENV_VAR_NAME"` aus der Umgebung geladen werden.
For Google Gemini, create a Gemini API key in Google AI Studio and set `GEMINI_API_KEY` in your shell or local `.env`. The built-in `gemini` provider uses Google's OpenAI-compatible endpoint and defaults to `gemini-3.1-flash-lite`.
FÃ¼r NVIDIA NIM bleibt der echte Key in `NVIDIA_API_KEY`, einer lokalen `.env` oder im Streamlit-Passwortfeld der laufenden Session; `config.yaml` enthÃ¤lt nur den Namen der Variable.
`NGC_API_KEY` is also accepted for the hosted NVIDIA provider because NVIDIA docs and self-hosted NIM deploy commands commonly use that name.
For build.nvidia.com self-hosted NIM, use the `nvidia_local_nim` provider. It calls `http://localhost:8000/v1` by default and does not send your NGC key to localhost; the key is only needed by Docker/NIM to pull and cache the model.
When `nvidia_local_nim` is selected, the Phase 3 sidebar can run the local Docker flow: Docker login to `nvcr.io`, pull `nvcr.io/nim/moonshotai/kimi-k2.6:1.7.0-variant`, start/stop the NIM container, and show recent logs.

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
        "temperature": 0.1,  # FÃ¼r diese Extraktion: niedrigere Temperatur
        "max_tokens": 4096
    },
    link_concepts=True  # Automatische OpenAlex-VerknÃ¼pfung
)

print(f"Concepts: {len(result.concepts)}")
print(f"Methods: {len(result.methods)}")
print(f"Claims: {len(result.claims)}")
```

#### Parser Auswahl

Das System wÃ¤hlt automatisch den besten Parser:

```python
from parsing.parser_router import ParserRouter, ParserType

router = ParserRouter()

# Automatische Parser-Auswahl basierend auf PDF-Inhalt
parsed = router.parse("/path/to/paper.pdf", "paper_id")
# Nougat fÃ¼r formeln-schwere Papers
# Table Transformer fÃ¼r tabellen-lastige Papers
# VLM fÃ¼r diagramm-intensive Papers
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
canonical = vocab.normalize("neural net")  # â†’ "Neural Network"

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

# WidersprÃ¼che filtern
contradictions = detector.find_contradictions(analyses, confidence_threshold=0.7)
```

### Phase 3 API-Endpunkte

| Methode | Endpoint | Beschreibung |
|---|---|---|
| GET | `/health` | Health check |
| GET | `/extraction/providers` | VerfÃ¼gbare LLM-Provider |
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

## Phase 4: Query-Interface & lokaler Forschungsassistent

Phase 4 ist implementiert und baut auf den lokalen Phase-1/2/3-Daten auf. Der Assistent durchsucht die DuckDB-Metadaten, gespeicherte Extraktionsergebnisse und optional gespeicherte Entity-Embeddings; Kuzu-Cypher bleibt als expliziter Escape-Hatch nutzbar, aber die Kernfunktionen laufen auch ohne Kuzu-Wheel.

### Features Phase 4

- Implementiert: deterministisches KG-Retrieval ueber Paper-Metadaten, Konzepte, Methoden, Claims und Cross-Domain-Hints
- Implementiert: Hybrid-Retrieval mit gespeicherten Entity-Embeddings aus Phase 3
- Implementiert: grounded Answers, die nur lokale KG-Evidenz verwenden und Paper-IDs als Quellen ausgeben
- Implementiert: Hypothesen-Generierung aus Cross-Domain-Hints und geteilten Methoden/Konzepten
- Implementiert: FastAPI-Endpunkte fuer Suche, Antworten, Hypothesen, Paper-Details und Paper-Nachbarschaft
- Implementiert: Streamlit-UIs fuer Chat, Paper-Detailansicht und Projektverwaltung

### Phase 4 starten

One-command Runner fuer API und Chat-UI:

```bash
python scripts/run_phase4.py
```

Nur API:

```bash
python scripts/run_phase4.py --api-only
```

Nur UI:

```bash
python scripts/run_phase4.py --ui-only
```

Andere Phase-4-UI auswaehlen:

```bash
python scripts/run_phase4.py --ui chat
python scripts/run_phase4.py --ui paper
python scripts/run_phase4.py --ui projects
```

Ports anpassen:

```bash
python scripts/run_phase4.py --api-port 9000 --ui-port 8502
```

Standard-URLs:

- API: `http://127.0.0.1:8000`
- Chat-UI: `http://localhost:8501`

### Phase 4 API-Endpunkte

| Methode | Endpoint | Beschreibung |
|---|---|---|
| GET | `/health` | Health check und Provider-Uebersicht |
| POST | `/query/search` | Lokale KG-/Hybrid-Suche |
| POST | `/query/answer` | Grounded Answer mit Evidenz und Quellen |
| POST | `/query/hypotheses` | Sourced Cross-Domain-Hypothesen |
| GET | `/papers/{paper_id}` | Paper-Details inklusive letzter Extraktion |
| GET | `/papers/{paper_id}/neighborhood` | Zitate, cited-by und aehnliche Paper |

### Beispielabfragen

```powershell
curl -X POST "http://127.0.0.1:8000/query/search" `
  -H "Content-Type: application/json" `
  -d '{"query":"graph transformer","limit":5}'
```

```powershell
curl -X POST "http://127.0.0.1:8000/query/answer" `
  -H "Content-Type: application/json" `
  -d '{"question":"Welche Methoden werden fuer Graph Transformer genutzt?","limit":5}'
```

### Tests Phase 4

```bash
uv run pytest tests/test_phase4_query.py -q --tb=short --basetemp=.pytest-tmp-current/phase4
```

Zuletzt geprueft: `7 passed`.

---

### Lizenz

MIT

