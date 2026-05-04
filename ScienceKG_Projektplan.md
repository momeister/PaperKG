# ScienceKG — Projektplan

> Vollständig lokales, privacy-preserving System zur automatisierten wissenschaftlichen Paper-Analyse, Knowledge-Graph-Konstruktion und LLM-gestützten Forschungsassistenz.

---

## Vision

Ein System das:
1. Automatisch Paper zu einem Thema harvested und verarbeitet
2. Einen persistenten, wachsenden Knowledge Graph aufbaut der über Projekte hinweg akkumuliert
3. Cross-Domain-Verbindungen zwischen thematisch entfernten Papern erkennt
4. Lokal, kostenlos und ohne Cloud-Abhängigkeit läuft
5. Als intelligenter Forschungsassistent querybar ist

---

## Tech-Stack

### Core Infrastructure
| Komponente | Tool | Begründung |
|---|---|---|
| Knowledge Graph DB | **Kuzu** | Lokal, embedded, Cypher-kompatibel, HNSW-Index, bereits bekannt |
| Vektor-Index | **hnswlib** (via Kuzu) | Bereits in Infrastruktur vorhanden |
| Relationale Metadaten | **DuckDB** | Schnelle lokale SQL-Queries, Parquet-Support, kein Server nötig |
| Job Queue / Scheduler | **Celery + Redis** | Async Batch-Verarbeitung, nachts laufen lassen |
| API Backend | **FastAPI** | Leichtgewichtig, async, Python-nativ |
| Frontend / UI | **Streamlit** (v1) → custom React (v2) | Streamlit für schnellen Start, später ersetzen |

### PDF Processing
| Komponente | Tool | Begründung |
|---|---|---|
| Standard-Parser | **Marker** | Schnell, gutes Markdown-Output, Zweispalten-aware |
| Formel-intensiv | **Nougat** (Meta) | LaTeX-Output für Formeln, als Fallback |
| Multimodale Seiten | **Gemma 4 26B via Ollama** | Bilder, Diagramme, komplexe Layouts |
| Tabellen | **Table Transformer** (Microsoft) | Strukturiertes JSON aus PDF-Tabellen |

### LLM Stack
| Aufgabe | Modell | Begründung |
|---|---|---|
| Entity-Extraktion, Zusammenfassung | **Qwen3.5-35B-A3B Q3_K_M** via Ollama | Schnell, multilingual, gut genug |
| Cross-Domain-Reasoning, Hypothesen | **DeepSeek-R1-Distill-Qwen-32B** via Ollama | Wissenschaftliches Reasoning |
| Multimodale Analyse | **Gemma 4 26B** via Ollama | Bilder und Plots aus Paper |
| Embeddings | **bge-m3** (HuggingFace, lokal) | 100+ Sprachen, keine API |
| Übersetzung | **Qwen3.5 direkt** | Versteht 30+ Sprachen nativ, kein separates Tool |

### Paper Harvesting APIs
| Quelle | Was | Kosten |
|---|---|---|
| **arXiv API** | Volltext-PDFs, Preprints | Kostenlos |
| **Semantic Scholar API** | Metadaten, Citations, Embeddings | Kostenlos (100 req/s mit Key) |
| **OpenAlex API** | Konzept-Taxonomie, Author-Graph | Kostenlos |
| **Papers with Code API** | Paper → GitHub-Code-Links | Kostenlos |
| **Unpaywall API** | Open-Access-Version von Paywalled Papern | Kostenlos |

### Ontologie / Normalisierung
| Komponente | Tool |
|---|---|
| Basis-Vokabular | **OpenAlex Concepts** (~65K Konzepte, hierarchisch) |
| Entity Linking | Embedding-Similarity gegen OpenAlex + eigener wachsender Vokabular-Layer |

---

## KG-Schema (Kuzu)

### Node-Types

```
Paper {
    id: STRING (DOI oder arXiv-ID, Primary Key)
    title: STRING
    year: INT
    version: INT
    superseded_by: STRING (nullable)
    has_full_text: BOOL
    peer_reviewed: BOOL
    retracted: BOOL
    language_original: STRING
    citation_count: INT
    confidence_score: FLOAT
    obsolescence_score: FLOAT
    conflict_flag: BOOL
    embedding_model: STRING
    embedding_version: INT
    source: STRING (arxiv/semantic_scholar/etc)
    added_to_graph: TIMESTAMP
    last_updated: TIMESTAMP
}

Concept {
    id: STRING (OpenAlex ID oder custom)
    label: STRING (normalisiertes Label)
    aliases: STRING[] (alle bekannten Varianten)
    domain: STRING (physics/cs/biology/...)
    openAlex_id: STRING (nullable)
    custom: BOOL
    embedding: FLOAT[1024]
}

Author {
    id: STRING
    name: STRING
    orcid: STRING (nullable)
    affiliation: STRING
}

Method {
    id: STRING
    label: STRING
    domain: STRING
    description: STRING
    embedding: FLOAT[1024]
}

Repository {
    id: STRING
    url: STRING
    language: STRING
    stars: INT
}
```

### Edge-Types

```
CITES            (Paper → Paper)
HAS_CONCEPT      (Paper → Concept, weight: FLOAT)
HAS_METHOD       (Paper → Method, weight: FLOAT)
AUTHORED_BY      (Paper → Author)
IMPLEMENTS       (Paper → Repository)
SIMILAR_TO       (Paper → Paper, score: FLOAT, type: STRING)  
  # type: citation_overlap | embedding | method | cross_domain
CONFLICTS_WITH   (Paper → Paper, aspect: STRING)
SUPERSEDES       (Paper → Paper)
RELATED_CONCEPT  (Concept → Concept, relation: STRING)
  # relation: broader | narrower | synonym | cross_domain
```

---

## Architektur-Überblick

```
┌─────────────────────────────────────────────────────┐
│                    UI Layer                          │
│              Streamlit / React                       │
│    Search · Query · Graph-Viz · Project-View        │
└────────────────────┬────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────┐
│                  API Layer                           │
│                  FastAPI                             │
│   /search  /query  /harvest  /graph  /status        │
└──────┬─────────────┬──────────────┬─────────────────┘
       │             │              │
┌──────▼──────┐ ┌────▼─────┐ ┌────▼──────────────────┐
│  Harvester  │ │  Query   │ │   Extraction Pipeline  │
│  Module     │ │  Engine  │ │                        │
│             │ │          │ │  PDF → Marker/Nougat   │
│  arXiv      │ │  KG      │ │  → Chunker             │
│  S2         │ │  Query   │ │  → LLM Extraction      │
│  OpenAlex   │ │  (Cypher)│ │  → Entity Linking      │
│  PwC        │ │          │ │  → KG Write            │
│  Unpaywall  │ │  Vector  │ └────────────────────────┘
└─────────────┘ │  Search  │
                │  (bge-m3)│
                └────┬─────┘
                     │
┌────────────────────▼────────────────────────────────┐
│                 Storage Layer                        │
│                                                     │
│   Kuzu (KG)    DuckDB (Meta)    File System (PDFs) │
│   Project-KG + Global-KG                           │
└─────────────────────────────────────────────────────┘
```

### Project-KG vs Global-KG

- **Project-KG**: Themenspezifisch, schnell befüllt, isoliert querybar
- **Global-KG**: Akkumuliert alle Projects, Cross-Domain-Edges entstehen hier
- **Merge-Strategie**: Nach jedem Project-Harvest → Merge-Pass in Global-KG mit Deduplication und Entity-Linking

---

## Implementierungsplan

---

### Phase 1 — Fundament & Harvester
**Dauer: 1-2 Wochen**
**Ziel: Automatisches Paper-Download und strukturierte Ablage ohne LLM**

#### Was implementiert wird:
- `harvester/arxiv_client.py` — arXiv API Wrapper (Suche, Download, Versionserkennung)
- `harvester/semantic_scholar_client.py` — S2 API (Metadaten, Citations, Recommended Papers)
- `harvester/openalex_client.py` — Konzept-Taxonomie laden, Author-Daten
- `harvester/unpaywall_client.py` — Open-Access-Version für Paywalled Paper
- `harvester/papers_with_code_client.py` — Code-Repository-Links
- `harvester/deduplication.py` — DOI-Fingerprinting + Titel-Normalisierung
- `storage/file_manager.py` — PDF-Ablage, Versionsverwaltung
- `storage/metadata_db.py` — DuckDB Schema und CRUD

#### Fähigkeiten nach Phase 1:
- Suche nach Thema → automatischer Download aller verfügbaren Paper
- Duplikat-Erkennung via DOI + normalisiertem Titel
- Paywalled Paper werden mit Abstract + Link gespeichert (Flag: `has_full_text: false`)
- Metadaten strukturiert in DuckDB
- arXiv-Versionsverwaltung (immer neueste Version, alte erhalten)
- "Bubble Extension": Semantic Scholar Recommended Papers API lädt automatisch verwandte Paper aus anderen Themenfeldern

#### Technische Details:
- Async HTTP mit `httpx`
- Rate-Limiting eingebaut (S2: 100 req/s mit Key, arXiv: 3 req/s)
- Job-Queue via Celery für Overnight-Runs
- JSONL als Zwischenspeicher vor KG-Einpflegung

---

### Phase 2 — Flacher Citation-Graph
**Dauer: 1 Woche**
**Ziel: Struktureller KG ohne LLM — schon hier entstehen nützliche Cluster**

#### Was implementiert wird:
- `graph/kuzu_schema.py` — Kuzu-Schema initialisieren (alle Node/Edge-Types aus Schema oben)
- `graph/paper_ingestion.py` — Paper-Nodes + CITES-Edges aus Metadaten
- `graph/citation_analysis.py` — Co-Citation-Overlap berechnen (SIMILAR_TO Edges, type: citation_overlap)
- `graph/project_global_merge.py` — Merge-Logik Project → Global-KG
- `api/graph_endpoints.py` — Erste Query-Endpoints
- `ui/graph_visualization.py` — Streamlit + PyVis für KG-Visualisierung

#### Fähigkeiten nach Phase 2:
- Vollständiger Citation-Graph querybar
- Co-Citation-Cluster sichtbar (Themeninseln die man manuell nie gefunden hätte)
- "Welche Paper zitieren sowohl A als auch B?" → direkte Cypher-Query
- Visualisierung des Graphen im Browser
- Basis-Scores: `citation_count`, `obsolescence_score` (berechnet aus Alter + Citations)

#### Warum das schon nützlich ist:
Rein strukturell — kein LLM — findest du hier bereits Cross-Domain-Kandidaten über gemeinsame Referenzen. Zwei Paper aus komplett verschiedenen Feldern die 8 gemeinsame Referenzen haben, sind fast sicher konzeptuell verwandt.

---

### Phase 3 — PDF-Parsing & LLM-Extraktion
**Dauer: 2-3 Wochen**
**Ziel: Semantische Anreicherung des KG durch LLM-basierte Entity-Extraktion**

#### Was implementiert wird:
- `parsing/marker_parser.py` — Standard-PDF-Parser (Marker)
- `parsing/nougat_parser.py` — Fallback für Formel-intensive Paper
- `parsing/table_transformer.py` — Tabellen-Extraktion als strukturiertes JSON
- `parsing/vlm_parser.py` — Gemma 4 für Bilder/Diagramme (wenn Ollama-Bug gefixt)
- `parsing/parser_router.py` — Entscheidungslogik welcher Parser für welches Paper
- `extraction/entity_extractor.py` — Qwen3.5 extrahiert Konzepte, Methoden, Claims
- `extraction/entity_linker.py` — Matching gegen OpenAlex Concepts + eigenen Vokabular
- `extraction/vocabulary.py` — Wachsender "Duden": Standard-Labels + Alias-Management
- `extraction/embedding_engine.py` — bge-m3 Embedding-Generierung, Versions-Tracking
- `extraction/conflict_detector.py` — Widersprüche zwischen Paper-Claims erkennen
- `extraction/batch_processor.py` — Celery-Jobs für Overnight-Verarbeitung

#### LLM-Extraktions-Prompt-Strategie:
Ganzes Paper in Kontext (32k reicht für ~95% der Paper), strukturierter Output:
```json
{
  "concepts": [{"label": "...", "context": "...", "confidence": 0.9}],
  "methods": [{"label": "...", "domain": "...", "description": "..."}],
  "claims": [{"statement": "...", "evidence_type": "experimental|theoretical|review"}],
  "cross_domain_hints": ["könnte relevant für: ..."],
  "language_detected": "en"
}
```

#### Entity-Linking-Workflow:
1. LLM extrahiert freie Entity-Labels
2. bge-m3 embedded das Label
3. Similarity-Search gegen OpenAlex Concepts
4. Score > 0.85 → Standard-OpenAlex-Label verwenden
5. Score 0.65-0.85 → Kandidat vorschlagen, für Review flaggen
6. Score < 0.65 → Neuer Custom-Node im Vokabular

#### Fähigkeiten nach Phase 3:
- Vollständig angereicherte Paper-Nodes mit Konzepten, Methoden, Claims
- Method-Embedding-Similarity: Papers mit identischer Methodik erkennbar
- Cross-Domain-Edges entstehen automatisch (SIMILAR_TO, type: method | embedding)
- Conflict-Detection zwischen Paper-Claims
- Multilinguale Paper werden nativ durch Qwen3.5 verarbeitet
- Vokabular wächst mit jedem Harvest-Run

---

### Phase 4 — Query-Interface & LLM-Assistent
**Dauer: 1-2 Wochen**
**Ziel: Nutzbarer Forschungsassistent der KG + LLM kombiniert**

#### Was implementiert wird:
- `query/kg_retriever.py` — Cypher-Query-Generator aus natürlichsprachigem Input
- `query/hybrid_retriever.py` — KG-Query + Vektor-Suche kombiniert
- `query/llm_router.py` — Routing: Qwen3.5 für Retrieval, R1 für Reasoning
- `query/grounded_responder.py` — LLM antwortet nur auf Basis von KG-Fakten
- `query/hypothesis_generator.py` — R1 generiert Forschungshypothesen aus Cross-Domain-Edges
- `ui/chat_interface.py` — Streamlit Chat-UI
- `ui/paper_detail.py` — Paper-Detailansicht mit KG-Nachbarschaft
- `ui/project_manager.py` — Projekte anlegen, Paper zuordnen, KG-Merge starten

#### Fähigkeiten nach Phase 4:
- Natürlichsprachige Fragen gegen den KG ("Was sind die wichtigsten Methoden in diesem Feld?")
- Grounded Answers: Jede Aussage mit Paper-Referenz belegt
- Cross-Domain-Discovery: "Welche Methoden aus anderen Feldern könnten hier anwendbar sein?"
- Hypothesen-Generierung via R1 auf Basis von Cross-Domain-Edges
- Paper-Empfehlungen: "Welche Paper sollte ich noch lesen?"
- Projekt-Management: Mehrere parallele Themenfelder

---

### Phase 5 — Qualität & Automatisierung
**Dauer: 1 Woche**
**Ziel: Wartbarkeit, Qualitätsmessung, automatische Updates**

#### Was implementiert wird:
- `quality/benchmark.py` — 20 manuell annotierte Paper als Ground Truth, automatischer Qualitätstest
- `quality/retraction_checker.py` — Abgleich gegen Retraction Watch (Paper with Code API)
- `quality/obsolescence_updater.py` — Periodische Neuberechnung von Scores
- `scheduler/nightly_jobs.py` — Celery Beat: neue Paper harvesten, KG aktualisieren, Scores neu berechnen
- `maintenance/embedding_reindex.py` — Reindex-Job bei Embedding-Modell-Wechsel
- `maintenance/kg_vacuum.py` — Doppelte Edges entfernen, Konsistenz prüfen

---

## Projektstruktur (Verzeichnisse)

```
sciencekg/
├── harvester/
│   ├── arxiv_client.py
│   ├── semantic_scholar_client.py
│   ├── openalex_client.py
│   ├── unpaywall_client.py
│   ├── papers_with_code_client.py
│   └── deduplication.py
├── parsing/
│   ├── marker_parser.py
│   ├── nougat_parser.py
│   ├── table_transformer.py
│   ├── vlm_parser.py
│   └── parser_router.py
├── extraction/
│   ├── entity_extractor.py
│   ├── entity_linker.py
│   ├── vocabulary.py
│   ├── embedding_engine.py
│   ├── conflict_detector.py
│   └── batch_processor.py
├── graph/
│   ├── kuzu_schema.py
│   ├── paper_ingestion.py
│   ├── citation_analysis.py
│   └── project_global_merge.py
├── query/
│   ├── kg_retriever.py
│   ├── hybrid_retriever.py
│   ├── llm_router.py
│   ├── grounded_responder.py
│   └── hypothesis_generator.py
├── api/
│   └── main.py (FastAPI)
├── ui/
│   ├── chat_interface.py
│   ├── graph_visualization.py
│   ├── paper_detail.py
│   └── project_manager.py
├── storage/
│   ├── file_manager.py
│   └── metadata_db.py
├── quality/
│   ├── benchmark.py
│   ├── retraction_checker.py
│   └── obsolescence_updater.py
├── scheduler/
│   └── nightly_jobs.py
├── maintenance/
│   ├── embedding_reindex.py
│   └── kg_vacuum.py
├── data/
│   ├── pdfs/                    # heruntergeladene Paper
│   ├── graphs/
│   │   ├── project_kgs/         # ein Kuzu-DB pro Projekt
│   │   └── global_kg/           # das Big Brain
│   ├── metadata.duckdb
│   └── vocabulary.json          # der "Duden"
├── config.yaml
├── requirements.txt
└── README.md
```

---

## Konfiguration (config.yaml)

```yaml
ollama:
  base_url: "http://localhost:11434"
  extraction_model: "qwen3.5:35b-a3b"
  reasoning_model: "deepseek-r1:32b"
  multimodal_model: "gemma4:26b"

embedding:
  model: "BAAI/bge-m3"
  dimension: 1024
  version: 1  # erhöhen bei Modellwechsel → triggert Reindex

harvester:
  max_papers_per_search: 100
  bubble_extension: true
  bubble_extension_count: 30
  rate_limits:
    arxiv: 3
    semantic_scholar: 10

parsing:
  default: "marker"
  formula_threshold: 0.3  # ab X% Formeln → Nougat
  use_vlm_for_images: true

graph:
  project_kg_path: "data/graphs/project_kgs"
  global_kg_path: "data/graphs/global_kg"
  similarity_threshold: 0.75
  cross_domain_threshold: 0.65

quality:
  min_confidence_for_auto_accept: 0.85
  obsolescence_weight_age: 0.3
  obsolescence_weight_citations: 0.5
  obsolescence_weight_conflicts: 0.2
```

---

## Implementierungs-Reihenfolge für Claude Code

1. **Session 1-3**: Phase 1 — Harvester, DuckDB-Schema, Deduplication
2. **Session 4**: Phase 2 — Kuzu-Schema, Citation-Graph, erste Visualisierung
3. **Session 5-9**: Phase 3 — Parser-Stack, LLM-Extraktion, Entity-Linking, Vocabulary
4. **Session 10-12**: Phase 4 — Query-Engine, Chat-UI, Hypothesis-Generator
5. **Session 13-14**: Phase 5 — Quality, Scheduler, Maintenance

Jede Phase liefert ein funktionales Artefakt. Phase 1 allein ist schon nützlicher als manuelles Paper-Suchen.

---

## Hardware-Nutzung (RTX 5070 Ti, 16GB VRAM, 64GB RAM)

| Job | VRAM | Wann |
|---|---|---|
| Qwen3.5 Extraktion | ~12GB | Batch, nachts |
| bge-m3 Embedding | ~2GB | parallel zu allem |
| Nougat PDF-Parsing | ~4GB | Batch |
| R1 Reasoning | ~14GB | On-Demand, interaktiv |
| Gemma 4 Multimodal | ~12GB | Batch, nachts |

**Gleichzeitiger Betrieb**: Qwen3.5 + bge-m3 passen zusammen (14GB). R1 und Gemma 4 laufen exklusiv. Celery-Jobs berücksichtigen das über Ressourcen-Locks.

---

## Was dieses System explizit NICHT ist

- Kein Ersatz für kritisches wissenschaftliches Lesen — es ist ein Navigationswerkzeug
- Keine automatische Peer Review
- Kein System das beweist dass eine Cross-Domain-Verbindung valide ist — es schlägt vor
- Keine Cloud-Komponente — wer Cloud will, verwendet NotebookLM

---

## Offene Entscheidungen (vor Implementierungsstart klären)

1. **Kuzu vs. Neo4j**: Kuzu ist eingebettet und einfacher, Neo4j hat bessere Visualisierungs-Tooling. Empfehlung: Kuzu, da bereits bekannt und kein Server-Overhead.
2. **Streamlit vs. sofort React**: Streamlit für v1 ist 3x schneller zu bauen. Wenn das System sich bewährt, React-Rewrite in Phase 5+.
3. **Nightly-Sync-Frequenz**: Täglich? Wöchentlich? Abhängig davon wie aktiv das Themenfeld ist.
