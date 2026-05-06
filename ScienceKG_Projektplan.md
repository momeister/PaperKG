# ScienceKG â€” Projektplan

> VollstÃ¤ndig lokales, privacy-preserving System zur automatisierten wissenschaftlichen Paper-Analyse, Knowledge-Graph-Konstruktion und LLM-gestÃ¼tzten Forschungsassistenz.

> Hinweis: Dieses Dokument beschreibt Zielarchitektur und Roadmap. Der aktuelle Implementierungsstand steht in `MEMORY.md` und `README.md`. Phase 1-3 sind lokal nutzbar; einzelne Planpunkte wie dauerhaft laufende Celery/Redis-Automation, vollwertige Spezialparser-Modellgewichte und ein live OpenAlex-Embedding-Index bleiben optionale bzw. zukuenftige Integrationen.

---

## Vision

Ein System das:
1. Automatisch Paper zu einem Thema harvested und verarbeitet
2. Einen persistenten, wachsenden Knowledge Graph aufbaut der Ã¼ber Projekte hinweg akkumuliert
3. Cross-Domain-Verbindungen zwischen thematisch entfernten Papern erkennt
4. Lokal, kostenlos und ohne Cloud-AbhÃ¤ngigkeit lÃ¤uft
5. Als intelligenter Forschungsassistent querybar ist

> Hinweis: Dieses Dokument beschreibt Zielarchitektur und Roadmap. Der aktuelle Implementierungsstand steht in `MEMORY.md` und `README.md`. Phase 1-3 sind lokal nutzbar; einzelne Planpunkte wie dauerhaft laufende Celery/Redis-Automation, vollwertige Spezialparser-Modellgewichte und ein live OpenAlex-Embedding-Index bleiben optionale bzw. zukuenftige Integrationen.

---

## Tech-Stack

### Core Infrastructure
| Komponente | Tool | BegrÃ¼ndung |
|---|---|---|
| Knowledge Graph DB | **Kuzu** | Lokal, embedded, Cypher-kompatibel, HNSW-Index, bereits bekannt |
| Vektor-Index | **hnswlib** (via Kuzu) | Bereits in Infrastruktur vorhanden |
| Relationale Metadaten | **DuckDB** | Schnelle lokale SQL-Queries, Parquet-Support, kein Server nÃ¶tig |
| Job Queue / Scheduler | **Celery + Redis** | Async Batch-Verarbeitung, nachts laufen lassen |
| API Backend | **FastAPI** | Leichtgewichtig, async, Python-nativ |
| Frontend / UI | **Streamlit** (v1) â†’ custom React (v2) | Streamlit fÃ¼r schnellen Start, spÃ¤ter ersetzen |

### PDF Processing
| Komponente | Tool | BegrÃ¼ndung |
|---|---|---|
| Standard-Parser | **Marker** | Schnell, gutes Markdown-Output, Zweispalten-aware |
| Formel-intensiv | **Nougat** (Meta) | LaTeX-Output fÃ¼r Formeln, als Fallback |
| Multimodale Seiten | **Gemma 4 26B via Ollama** | Bilder, Diagramme, komplexe Layouts |
| Tabellen | **Table Transformer** (Microsoft) | Strukturiertes JSON aus PDF-Tabellen |

### LLM Stack
| Aufgabe | Modell | BegrÃ¼ndung |
|---|---|---|
| Entity-Extraktion, Zusammenfassung | **Qwen3.6-35B-A3B Q4_K_M** via Ollama | Schnell, multilingual, gut genug |
| Cross-Domain-Reasoning, Hypothesen | **DeepSeek-R1-Distill-Qwen-32B** via Ollama | Wissenschaftliches Reasoning |
| Multimodale Analyse | **Gemma 4 26B** via Ollama | Bilder und Plots aus Paper |
| Embeddings | **bge-m3** (HuggingFace, lokal) | 100+ Sprachen, keine API |
| Ãœbersetzung | **Qwen3.5 direkt** | Versteht 30+ Sprachen nativ, kein separates Tool |

### Paper Harvesting APIs
| Quelle | Was | Kosten |
|---|---|---|
| **arXiv API** | Volltext-PDFs, Preprints | Kostenlos |
| **Semantic Scholar API** | Metadaten, Citations, Embeddings | Kostenlos (100 req/s mit Key) |
| **OpenAlex API** | Konzept-Taxonomie, Author-Graph | Kostenlos |
| **Papers with Code API** | Paper â†’ GitHub-Code-Links | Kostenlos |
| **Unpaywall API** | Open-Access-Version von Paywalled Papern | Kostenlos |

### Ontologie / Normalisierung
| Komponente | Tool |
|---|---|
| Basis-Vokabular | **OpenAlex Concepts** (~65K Konzepte, hierarchisch) |
| Entity Linking | Embedding-Similarity gegen OpenAlex + eigener wachsender Vokabular-Layer |

> Hinweis: Dieses Dokument beschreibt Zielarchitektur und Roadmap. Der aktuelle Implementierungsstand steht in `MEMORY.md` und `README.md`. Phase 1-3 sind lokal nutzbar; einzelne Planpunkte wie dauerhaft laufende Celery/Redis-Automation, vollwertige Spezialparser-Modellgewichte und ein live OpenAlex-Embedding-Index bleiben optionale bzw. zukuenftige Integrationen.

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
CITES            (Paper â†’ Paper)
HAS_CONCEPT      (Paper â†’ Concept, weight: FLOAT)
HAS_METHOD       (Paper â†’ Method, weight: FLOAT)
AUTHORED_BY      (Paper â†’ Author)
IMPLEMENTS       (Paper â†’ Repository)
SIMILAR_TO       (Paper â†’ Paper, score: FLOAT, type: STRING)  
  # type: citation_overlap | embedding | method | cross_domain
CONFLICTS_WITH   (Paper â†’ Paper, aspect: STRING)
SUPERSEDES       (Paper â†’ Paper)
RELATED_CONCEPT  (Concept â†’ Concept, relation: STRING)
  # relation: broader | narrower | synonym | cross_domain
```

> Hinweis: Dieses Dokument beschreibt Zielarchitektur und Roadmap. Der aktuelle Implementierungsstand steht in `MEMORY.md` und `README.md`. Phase 1-3 sind lokal nutzbar; einzelne Planpunkte wie dauerhaft laufende Celery/Redis-Automation, vollwertige Spezialparser-Modellgewichte und ein live OpenAlex-Embedding-Index bleiben optionale bzw. zukuenftige Integrationen.

---

## Architektur-Ãœberblick

```
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚                    UI Layer                          â”‚
â”‚              Streamlit / React                       â”‚
â”‚    Search Â· Query Â· Graph-Viz Â· Project-View        â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                     â”‚
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â–¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚                  API Layer                           â”‚
â”‚                  FastAPI                             â”‚
â”‚   /search  /query  /harvest  /graph  /status        â”‚
â””â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
       â”‚             â”‚              â”‚
â”Œâ”€â”€â”€â”€â”€â”€â–¼â”€â”€â”€â”€â”€â”€â” â”Œâ”€â”€â”€â”€â–¼â”€â”€â”€â”€â”€â” â”Œâ”€â”€â”€â”€â–¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚  Harvester  â”‚ â”‚  Query   â”‚ â”‚   Extraction Pipeline  â”‚
â”‚  Module     â”‚ â”‚  Engine  â”‚ â”‚                        â”‚
â”‚             â”‚ â”‚          â”‚ â”‚  PDF â†’ Marker/Nougat   â”‚
â”‚  arXiv      â”‚ â”‚  KG      â”‚ â”‚  â†’ Chunker             â”‚
â”‚  S2         â”‚ â”‚  Query   â”‚ â”‚  â†’ LLM Extraction      â”‚
â”‚  OpenAlex   â”‚ â”‚  (Cypher)â”‚ â”‚  â†’ Entity Linking      â”‚
â”‚  PwC        â”‚ â”‚          â”‚ â”‚  â†’ KG Write            â”‚
â”‚  Unpaywall  â”‚ â”‚  Vector  â”‚ â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜ â”‚  Search  â”‚
                â”‚  (bge-m3)â”‚
                â””â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”˜
                     â”‚
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â–¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚                 Storage Layer                        â”‚
â”‚                                                     â”‚
â”‚   Kuzu (KG)    DuckDB (Meta)    File System (PDFs) â”‚
â”‚   Project-KG + Global-KG                           â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

### Project-KG vs Global-KG

- **Project-KG**: Themenspezifisch, schnell befÃ¼llt, isoliert querybar
- **Global-KG**: Akkumuliert alle Projects, Cross-Domain-Edges entstehen hier
- **Merge-Strategie**: Nach jedem Project-Harvest â†’ Merge-Pass in Global-KG mit Deduplication und Entity-Linking

> Hinweis: Dieses Dokument beschreibt Zielarchitektur und Roadmap. Der aktuelle Implementierungsstand steht in `MEMORY.md` und `README.md`. Phase 1-3 sind lokal nutzbar; einzelne Planpunkte wie dauerhaft laufende Celery/Redis-Automation, vollwertige Spezialparser-Modellgewichte und ein live OpenAlex-Embedding-Index bleiben optionale bzw. zukuenftige Integrationen.

---

## Implementierungsplan

> Hinweis: Dieses Dokument beschreibt Zielarchitektur und Roadmap. Der aktuelle Implementierungsstand steht in `MEMORY.md` und `README.md`. Phase 1-3 sind lokal nutzbar; einzelne Planpunkte wie dauerhaft laufende Celery/Redis-Automation, vollwertige Spezialparser-Modellgewichte und ein live OpenAlex-Embedding-Index bleiben optionale bzw. zukuenftige Integrationen.

---

### Phase 1 â€” Fundament & Harvester
**Dauer: 1-2 Wochen**
**Ziel: Automatisches Paper-Download und strukturierte Ablage ohne LLM**

#### Was implementiert wird:
- `harvester/arxiv_client.py` â€” arXiv API Wrapper (Suche, Download, Versionserkennung)
- `harvester/semantic_scholar_client.py` â€” S2 API (Metadaten, Citations, Recommended Papers)
- `harvester/openalex_client.py` â€” Konzept-Taxonomie laden, Author-Daten
- `harvester/unpaywall_client.py` â€” Open-Access-Version fÃ¼r Paywalled Paper
- `harvester/papers_with_code_client.py` â€” Code-Repository-Links
- `harvester/deduplication.py` â€” DOI-Fingerprinting + Titel-Normalisierung
- `storage/file_manager.py` â€” PDF-Ablage, Versionsverwaltung
- `storage/metadata_db.py` â€” DuckDB Schema und CRUD

#### FÃ¤higkeiten nach Phase 1:
- Suche nach Thema â†’ automatischer Download aller verfÃ¼gbaren Paper
- Duplikat-Erkennung via DOI + normalisiertem Titel
- Paywalled Paper werden mit Abstract + Link gespeichert (Flag: `has_full_text: false`)
- Metadaten strukturiert in DuckDB
- arXiv-Versionsverwaltung (immer neueste Version, alte erhalten)
- "Bubble Extension": Semantic Scholar Recommended Papers API lÃ¤dt automatisch verwandte Paper aus anderen Themenfeldern

#### Technische Details:
- Async HTTP mit `httpx`
- Rate-Limiting eingebaut (S2: 100 req/s mit Key, arXiv: 3 req/s)
- Job-Queue via Celery fÃ¼r Overnight-Runs
- JSONL als Zwischenspeicher vor KG-Einpflegung

#### âœ… Abnahme-Kriterien (Go/No-Go fÃ¼r Phase 2)

| Kriterium | Test | Erwartetes Ergebnis |
|---|---|---|
| Harvester lÃ¤uft | `python scripts/try_phase1.py "transformer attention" --max-results 20 --full-phase1` | 20 PDFs in `data/pdfs/`, JSONL in `data/` |
| Duplikat-Erkennung | Dasselbe Paper zweimal harvesten | Zweiter Lauf: `SKIP (duplicate)` im Log, kein doppelter Eintrag in DuckDB |
| Paywalled Paper | Paper mit DOI ohne arXiv-Version suchen | Eintrag in DuckDB mit `has_full_text=false`, Abstract vorhanden, PDF-Link gesetzt |
| Versionsverwaltung | arXiv-Paper mit v1 und v2 harvesten | Neueste Version aktiv, alte mit `superseded_by` gesetzt |
| Bubble Extension | Harvest mit `bubble_extension=true` | ZusÃ¤tzliche Paper aus verwandten Feldern im Log sichtbar |
| Metadaten vollstÃ¤ndig | DuckDB-Query: `SELECT * FROM papers WHERE year IS NULL` | 0 Ergebnisse |
| Rate-Limiting | 50 Paper in einem Run harvesten | Kein HTTP 429 Error von arXiv oder S2 |

**Was du konkret siehst:**
- `data/pdfs/` enthÃ¤lt heruntergeladene PDFs mit strukturierten Dateinamen (`{doi_hash}_{year}.pdf`)
- DuckDB-Tabelle `papers` mit korrekten Metadaten querybar
- Celery-Worker lÃ¤uft, Logs zeigen Job-Status (SUCCESS/FAILURE, keine Zombies)

> Hinweis: Dieses Dokument beschreibt Zielarchitektur und Roadmap. Der aktuelle Implementierungsstand steht in `MEMORY.md` und `README.md`. Phase 1-3 sind lokal nutzbar; einzelne Planpunkte wie dauerhaft laufende Celery/Redis-Automation, vollwertige Spezialparser-Modellgewichte und ein live OpenAlex-Embedding-Index bleiben optionale bzw. zukuenftige Integrationen.

---

### Phase 2 â€” Flacher Citation-Graph
**Dauer: 1 Woche**
**Ziel: Struktureller KG ohne LLM â€” schon hier entstehen nÃ¼tzliche Cluster**

#### Was implementiert wird:
- `graph/kuzu_schema.py` â€” Kuzu-Schema initialisieren (alle Node/Edge-Types aus Schema oben)
- `graph/paper_ingestion.py` â€” Paper-Nodes + CITES-Edges aus Metadaten
- `graph/citation_analysis.py` â€” Co-Citation-Overlap berechnen (SIMILAR_TO Edges, type: citation_overlap)
- `graph/project_global_merge.py` â€” Merge-Logik Project â†’ Global-KG
- `api/graph_endpoints.py` â€” Erste Query-Endpoints
- `ui/graph_visualization.py` â€” Streamlit + PyVis fÃ¼r KG-Visualisierung

#### FÃ¤higkeiten nach Phase 2:
- VollstÃ¤ndiger Citation-Graph querybar
- Co-Citation-Cluster sichtbar (Themeninseln die man manuell nie gefunden hÃ¤tte)
- "Welche Paper zitieren sowohl A als auch B?" â†’ direkte Cypher-Query
- Visualisierung des Graphen im Browser
- Basis-Scores: `citation_count`, `obsolescence_score` (berechnet aus Alter + Citations)

#### Warum das schon nÃ¼tzlich ist:
Rein strukturell â€” kein LLM â€” findest du hier bereits Cross-Domain-Kandidaten Ã¼ber gemeinsame Referenzen. Zwei Paper aus komplett verschiedenen Feldern die 8 gemeinsame Referenzen haben, sind fast sicher konzeptuell verwandt.

#### âœ… Abnahme-Kriterien (Go/No-Go fÃ¼r Phase 3)

| Kriterium | Test | Erwartetes Ergebnis |
|---|---|---|
| Schema korrekt initialisiert | Kuzu Ã¶ffnen, alle Node/Edge-Types abfragen | Alle 5 Node-Types und 9 Edge-Types vorhanden |
| Paper-Ingestion | 50 Paper aus Phase 1 ingestieren | 50 Paper-Nodes in Kuzu, CITES-Edges aus Metadaten |
| Citation-Query | `MATCH (p1:Paper)-[:CITES]->(p2:Paper) RETURN count(*)` | Zahl > 0, realistisch mehrere Hundert Edges |
| Co-Citation-Cluster | `MATCH (p1)-[:SIMILAR_TO {type:'citation_overlap'}]->(p2) RETURN p1.title, p2.title, s.score LIMIT 10` | Mindestens einige Paare mit Score > 0 |
| Obsolescence-Score | `MATCH (p:Paper) WHERE p.obsolescence_score IS NULL RETURN count(p)` | 0 â€” alle Paper haben einen Score |
| Graph-Visualisierung | Browser Ã¶ffnen, Streamlit starten | Graph mit Nodes und Edges renderbar, interaktiv navigierbar |
| Projectâ†’Global Merge | Projekt-KG mergen | Global-KG enthÃ¤lt alle Nodes des Projekts, keine Duplikate |

**Was du konkret siehst:**
- Browser zeigt interaktiven Graphen mit Paper-Nodes und Citation-Edges
- Cluster von thematisch verwandten Papern sind visuell erkennbar
- Erste Co-Citation-Paare sichtbar â€” Paper die sich gegenseitig nicht kennen aber gemeinsame Referenzen haben

> Hinweis: Dieses Dokument beschreibt Zielarchitektur und Roadmap. Der aktuelle Implementierungsstand steht in `MEMORY.md` und `README.md`. Phase 1-3 sind lokal nutzbar; einzelne Planpunkte wie dauerhaft laufende Celery/Redis-Automation, vollwertige Spezialparser-Modellgewichte und ein live OpenAlex-Embedding-Index bleiben optionale bzw. zukuenftige Integrationen.

---

### Phase 3 â€” PDF-Parsing & LLM-Extraktion
**Dauer: 2-3 Wochen**
**Ziel: Semantische Anreicherung des KG durch LLM-basierte Entity-Extraktion**

#### Was implementiert wird:
- `parsing/marker_parser.py` â€” Standard-PDF-Parser (Marker)
- `parsing/nougat_parser.py` â€” Fallback fÃ¼r Formel-intensive Paper
- `parsing/table_transformer.py` â€” Tabellen-Extraktion als strukturiertes JSON
- `parsing/vlm_parser.py` â€” Gemma 4 fÃ¼r Bilder/Diagramme (wenn Ollama-Bug gefixt)
- `parsing/parser_router.py` â€” Entscheidungslogik welcher Parser fÃ¼r welches Paper
- `extraction/entity_extractor.py` â€” Qwen3.5 extrahiert Konzepte, Methoden, Claims
- `extraction/entity_linker.py` â€” Matching gegen OpenAlex Concepts + eigenen Vokabular
- `extraction/vocabulary.py` â€” Wachsender "Duden": Standard-Labels + Alias-Management
- `extraction/embedding_engine.py` â€” bge-m3 Embedding-Generierung, Versions-Tracking
- `extraction/conflict_detector.py` â€” WidersprÃ¼che zwischen Paper-Claims erkennen
- `extraction/batch_processor.py` â€” Celery-Jobs fÃ¼r Overnight-Verarbeitung

#### LLM-Extraktions-Prompt-Strategie:
Ganzes Paper in Kontext (32k reicht fÃ¼r ~95% der Paper), strukturierter Output:
```json
{
  "concepts": [{"label": "...", "context": "...", "confidence": 0.9}],
  "methods": [{"label": "...", "domain": "...", "description": "..."}],
  "claims": [{"statement": "...", "evidence_type": "experimental|theoretical|review"}],
  "cross_domain_hints": ["kÃ¶nnte relevant fÃ¼r: ..."],
  "language_detected": "en"
}
```

#### Entity-Linking-Workflow:
1. LLM extrahiert freie Entity-Labels
2. bge-m3 embedded das Label
3. Similarity-Search gegen OpenAlex Concepts
4. Score > 0.85 â†’ Standard-OpenAlex-Label verwenden
5. Score 0.65-0.85 â†’ Kandidat vorschlagen, fÃ¼r Review flaggen
6. Score < 0.65 â†’ Neuer Custom-Node im Vokabular

#### FÃ¤higkeiten nach Phase 3:
- VollstÃ¤ndig angereicherte Paper-Nodes mit Konzepten, Methoden, Claims
- Method-Embedding-Similarity: Papers mit identischer Methodik erkennbar
- Cross-Domain-Edges entstehen automatisch (SIMILAR_TO, type: method | embedding)
- Conflict-Detection zwischen Paper-Claims
- Multilinguale Paper werden nativ durch Qwen3.5 verarbeitet
- Vokabular wÃ¤chst mit jedem Harvest-Run

#### âœ… Abnahme-Kriterien (Go/No-Go fÃ¼r Phase 3)

| Kriterium | Test | Erwartetes Ergebnis |
|---|---|---|
| Marker (Standard-Parser) | 3 verschiedene Paper parsen (einspaltig, zweispaltig, tabellenlastig) | Lesbares Markdown, keine Zeichensalat-Zeilen, Zweispalten nicht gemischt |
| Nougat (Formel-Fallback) | Formel-intensives Paper durch Nougat | Valides LaTeX fÃ¼r Formeln im Output |
| Parser-Router | Paper mit >30% Formelanteil einlesen | Log zeigt automatische Nougat-Wahl mit BegrÃ¼ndung |
| LLM-Extraktion inhaltlich korrekt | Paper das du kennst manuell prÃ¼fen: `python -m extraction.entity_extractor --paper-id <id> --verbose` | 3-25 Concepts, keine Halluzinationen, Confidence variiert (nicht alle identisch) |
| Entity-Linking â€” sicherer Match | Paper mit "gradient descent" verarbeiten | Mappt auf OpenAlex-Standard-Label, kein neuer Node |
| Entity-Linking â€” kein Match | Sehr spezifisches Nischen-Konzept | Custom-Node mit Review-Flag im Vocabulary |
| Alias-Management | Zwei Paper mit gleichem Konzept, unterschiedlicher Benennung | Beide zeigen auf denselben Concept-Node |
| Embedding-Konsistenz | Python-Test (siehe unten) | Gleicher Text â†’ Cosine > 0.99; unverwandter Text â†’ Cosine < 0.5 |
| Embedding-Metadaten vollstÃ¤ndig | `MATCH (p:Paper) WHERE p.embedding_model IS NULL RETURN count(p)` | 0 |
| Cross-Domain-Edges vorhanden | `MATCH (p1)-[s:SIMILAR_TO]->(p2) WHERE s.type IN ['method','cross_domain'] RETURN count(*)` | Zahl > 0 nach 50+ verarbeiteten Papern |
| Conflict-Detection aktiv | `MATCH (p:Paper) WHERE p.conflict_flag = true RETURN count(p)` | Zahl > 0 bei 50+ Papern |
| Batch stabil | 30 Paper Overnight-Run: `python -m extraction.batch_processor --limit 30` | Alle 30 SUCCESS in Celery-Log, RAM stabil, keine Zombies |
| Vocabulary-Stats | `python -m extraction.vocabulary --stats` | Zeigt: X OpenAlex-gemappte, Y Custom, Z pending Review |

**Embedding-Konsistenztest:**
```python
from extraction.embedding_engine import EmbeddingEngine
from numpy import dot
from numpy.linalg import norm
e = EmbeddingEngine()
v1 = e.embed("neural network")
v2 = e.embed("neural network")
v3 = e.embed("cooking recipes")
print(dot(v1,v2)/(norm(v1)*norm(v2)))  # muss > 0.99 sein
print(dot(v1,v3)/(norm(v1)*norm(v3)))  # muss < 0.5 sein
```

**Was du konkret siehst:**
- Streamlit zeigt eine Sidebar mit Provider-, Modell- und Generations-Einstellungen
- Die Tabs `Extract`, `Vocabulary`, `Batch`, `Harvest` und `History` liefern jeweils eigene Ausgaben
- KG-Nodes haben jetzt Konzept- und Methoden-Verbindungen, nicht nur Citations
- Extract-Ergebnisse zeigen Concepts, Methods, Claims, Cross-Domain-Hints und OpenAlex-IDs
- Vocabulary-Datei wÃ¤chst mit normalisierten Labels und Aliases
- Cross-Domain-Edges im Graphen zwischen thematisch entfernten Papern sichtbar
- Conflict-Flags auf widersprÃ¼chlichen Papern gesetzt

> Hinweis: Dieses Dokument beschreibt Zielarchitektur und Roadmap. Der aktuelle Implementierungsstand steht in `MEMORY.md` und `README.md`. Phase 1-3 sind lokal nutzbar; einzelne Planpunkte wie dauerhaft laufende Celery/Redis-Automation, vollwertige Spezialparser-Modellgewichte und ein live OpenAlex-Embedding-Index bleiben optionale bzw. zukuenftige Integrationen.

---

### Phase 4 â€” Query-Interface & LLM-Assistent
**Dauer: 1-2 Wochen**
**Ziel: Nutzbarer Forschungsassistent der KG + LLM kombiniert**

#### Was implementiert wird:
- `query/kg_retriever.py` â€” Cypher-Query-Generator aus natÃ¼rlichsprachigem Input
- `query/hybrid_retriever.py` â€” KG-Query + Vektor-Suche kombiniert
- `query/llm_router.py` â€” Routing: Qwen3.5 fÃ¼r Retrieval, R1 fÃ¼r Reasoning
- `query/grounded_responder.py` â€” LLM antwortet nur auf Basis von KG-Fakten
- `query/hypothesis_generator.py` â€” R1 generiert Forschungshypothesen aus Cross-Domain-Edges
- `ui/chat_interface.py` â€” Streamlit Chat-UI
- `ui/paper_detail.py` â€” Paper-Detailansicht mit KG-Nachbarschaft
- `ui/project_manager.py` â€” Projekte anlegen, Paper zuordnen, KG-Merge starten

#### FÃ¤higkeiten nach Phase 4:
- NatÃ¼rlichsprachige Fragen gegen den KG ("Was sind die wichtigsten Methoden in diesem Feld?")
- Grounded Answers: Jede Aussage mit Paper-Referenz belegt
- Cross-Domain-Discovery: "Welche Methoden aus anderen Feldern kÃ¶nnten hier anwendbar sein?"
- Hypothesen-Generierung via R1 auf Basis von Cross-Domain-Edges
- Paper-Empfehlungen: "Welche Paper sollte ich noch lesen?"
- Projekt-Management: Mehrere parallele Themenfelder

#### âœ… Abnahme-Kriterien (Go/No-Go fÃ¼r Phase 5)

| Kriterium | Test | Erwartetes Ergebnis |
|---|---|---|
| KG-Retrieval funktioniert | NatÃ¼rlichsprachige Frage stellen: "Welche Paper behandeln Attention-Mechanismen?" | Mindestens 3 relevante Paper zurÃ¼ckgegeben, Cypher-Query im Log sichtbar |
| Grounded Answers | Jede Antwort prÃ¼fen | Jede Aussage hat mindestens eine Paper-ID als Quelle â€” keine unbelegten Behauptungen |
| Halluzinations-Check | Frage stellen Ã¼ber ein Thema das nicht im KG ist | System sagt "kein Treffer im KG" statt zu erfinden |
| LLM-Routing | Einfache Retrieval-Frage vs. komplexe Reasoning-Frage stellen | Log zeigt: Retrieval â†’ Qwen3.5, Reasoning â†’ R1 |
| Hypothesen-Generierung | "Welche Cross-Domain-Verbindungen gibt es?" anfragen | R1 liefert konkrete Hypothesen mit Quellenangaben, keine vagen Aussagen |
| Chat-UI nutzbar | Streamlit Ã¶ffnen, 5 Fragen stellen | Alle 5 Antworten kommen zurÃ¼ck, UI hÃ¤ngt sich nicht auf, Antwortzeit < 60s |
| Paper-Detailansicht | Ein Paper anklicken | Zeigt: Metadaten, Konzepte, Methoden, KG-Nachbarn (zitiert/zitiert von/Ã¤hnlich) |
| Projekt-Management | Neues Projekt anlegen, Paper zuordnen, KG-Merge starten | Projekt erscheint in Liste, nach Merge sind Paper im Global-KG |
| Paper-Empfehlungen | "Welche Paper sollte ich noch lesen?" | Empfehlungen basieren auf KG-Nachbarschaft, nicht auf LLM-Trainingsdaten |

**Was du konkret siehst:**
- Chat-Interface im Browser, Fragen in natÃ¼rlicher Sprache stellbar
- Jede Antwort zeigt Paper-Quellen (Titel + DOI/arXiv-Link)
- Paper-Detailseite zeigt den KG-Kontext: was zitiert es, was zitiert es, welche Konzepte teilt es
- Hypothesen-Panel zeigt Cross-Domain-VorschlÃ¤ge mit BegrÃ¼ndung

> Hinweis: Dieses Dokument beschreibt Zielarchitektur und Roadmap. Der aktuelle Implementierungsstand steht in `MEMORY.md` und `README.md`. Phase 1-3 sind lokal nutzbar; einzelne Planpunkte wie dauerhaft laufende Celery/Redis-Automation, vollwertige Spezialparser-Modellgewichte und ein live OpenAlex-Embedding-Index bleiben optionale bzw. zukuenftige Integrationen.

---

### Phase 5 â€” QualitÃ¤t & Automatisierung
**Dauer: 1 Woche**
**Ziel: Wartbarkeit, QualitÃ¤tsmessung, automatische Updates**

#### Was implementiert wird:
- `quality/benchmark.py` â€” 20 manuell annotierte Paper als Ground Truth, automatischer QualitÃ¤tstest
- `quality/retraction_checker.py` â€” Abgleich gegen Retraction Watch
- `quality/obsolescence_updater.py` â€” Periodische Neuberechnung von Scores
- `scheduler/nightly_jobs.py` â€” Celery Beat: neue Paper harvesten, KG aktualisieren, Scores neu berechnen
- `maintenance/embedding_reindex.py` â€” Reindex-Job bei Embedding-Modell-Wechsel
- `maintenance/kg_vacuum.py` â€” Doppelte Edges entfernen, Konsistenz prÃ¼fen

#### FÃ¤higkeiten nach Phase 5:
- Nightly-Run lÃ¤uft vollautomatisch: neue Paper harvesten, verarbeiten, in KG einpflegen
- Retracted Paper werden automatisch geflaggt
- Obsolescence-Scores werden periodisch neu berechnet
- Embedding-Reindex bei Modellwechsel ohne manuellen Eingriff
- Benchmark-Report zeigt objektive QualitÃ¤tskennzahlen

#### âœ… Abnahme-Kriterien (System vollstÃ¤ndig)

| Kriterium | Test | Erwartetes Ergebnis |
|---|---|---|
| Nightly-Job lÃ¤uft durch | Celery Beat starten, Ã¼ber Nacht laufen lassen | Morgens: neue Paper im KG, Logs zeigen alle Jobs als SUCCESS |
| Retraction-Check | Bekanntes retracted Paper in den KG einpflegen | Node hat `retracted=true`, erscheint in Queries geflaggt |
| Benchmark-Report | `python -m quality.benchmark --run` | Report mit Precision/Recall fÃ¼r Entity-Extraktion gegen Ground Truth |
| KG-Vacuum | `python -m maintenance.kg_vacuum` | Report: X doppelte Edges entfernt, Y inkonsistente Nodes gefunden |
| Embedding-Reindex | `embedding_version` in config.yaml erhÃ¶hen, Reindex starten | Alle Paper-Nodes haben neue `embedding_version`, Similarity-Suche funktioniert korrekt |
| Obsolescence-Update | Updater manuell starten | Scores haben sich fÃ¼r Ã¤ltere, wenig zitierte Paper erhÃ¶ht |

**Was du konkret siehst:**
- Dashboard zeigt KG-Gesundheitsmetriken: Paper total, davon retracted, davon obsolet, Konflikte
- Nightly-Logs zeigen was automatisch passiert ist
- Benchmark-Report gibt dir eine ehrliche Zahl wie gut die Extraktion ist

> Hinweis: Dieses Dokument beschreibt Zielarchitektur und Roadmap. Der aktuelle Implementierungsstand steht in `MEMORY.md` und `README.md`. Phase 1-3 sind lokal nutzbar; einzelne Planpunkte wie dauerhaft laufende Celery/Redis-Automation, vollwertige Spezialparser-Modellgewichte und ein live OpenAlex-Embedding-Index bleiben optionale bzw. zukuenftige Integrationen.

---

## Projektstruktur (Verzeichnisse)

```
sciencekg/
â”œâ”€â”€ harvester/
â”‚   â”œâ”€â”€ arxiv_client.py
â”‚   â”œâ”€â”€ semantic_scholar_client.py
â”‚   â”œâ”€â”€ openalex_client.py
â”‚   â”œâ”€â”€ unpaywall_client.py
â”‚   â”œâ”€â”€ papers_with_code_client.py
â”‚   â””â”€â”€ deduplication.py
â”œâ”€â”€ parsing/
â”‚   â”œâ”€â”€ marker_parser.py
â”‚   â”œâ”€â”€ nougat_parser.py
â”‚   â”œâ”€â”€ table_transformer.py
â”‚   â”œâ”€â”€ vlm_parser.py
â”‚   â””â”€â”€ parser_router.py
â”œâ”€â”€ extraction/
â”‚   â”œâ”€â”€ entity_extractor.py
â”‚   â”œâ”€â”€ entity_linker.py
â”‚   â”œâ”€â”€ vocabulary.py
â”‚   â”œâ”€â”€ embedding_engine.py
â”‚   â”œâ”€â”€ conflict_detector.py
â”‚   â””â”€â”€ batch_processor.py
â”œâ”€â”€ graph/
â”‚   â”œâ”€â”€ kuzu_schema.py
â”‚   â”œâ”€â”€ paper_ingestion.py
â”‚   â”œâ”€â”€ citation_analysis.py
â”‚   â””â”€â”€ project_global_merge.py
â”œâ”€â”€ query/
â”‚   â”œâ”€â”€ kg_retriever.py
â”‚   â”œâ”€â”€ hybrid_retriever.py
â”‚   â”œâ”€â”€ llm_router.py
â”‚   â”œâ”€â”€ grounded_responder.py
â”‚   â””â”€â”€ hypothesis_generator.py
â”œâ”€â”€ api/
â”‚   â””â”€â”€ main.py (FastAPI)
â”œâ”€â”€ ui/
â”‚   â”œâ”€â”€ chat_interface.py
â”‚   â”œâ”€â”€ graph_visualization.py
â”‚   â”œâ”€â”€ paper_detail.py
â”‚   â””â”€â”€ project_manager.py
â”œâ”€â”€ storage/
â”‚   â”œâ”€â”€ file_manager.py
â”‚   â””â”€â”€ metadata_db.py
â”œâ”€â”€ quality/
â”‚   â”œâ”€â”€ benchmark.py
â”‚   â”œâ”€â”€ retraction_checker.py
â”‚   â””â”€â”€ obsolescence_updater.py
â”œâ”€â”€ scheduler/
â”‚   â””â”€â”€ nightly_jobs.py
â”œâ”€â”€ maintenance/
â”‚   â”œâ”€â”€ embedding_reindex.py
â”‚   â””â”€â”€ kg_vacuum.py
â”œâ”€â”€ data/
â”‚   â”œâ”€â”€ pdfs/                    # heruntergeladene Paper
â”‚   â”œâ”€â”€ graphs/
â”‚   â”‚   â”œâ”€â”€ project_kgs/         # ein Kuzu-DB pro Projekt
â”‚   â”‚   â””â”€â”€ global_kg/           # das Big Brain
â”‚   â”œâ”€â”€ metadata.duckdb
â”‚   â””â”€â”€ vocabulary.json          # der "Duden"
â”œâ”€â”€ config.yaml
â”œâ”€â”€ requirements.txt
â””â”€â”€ README.md
```

> Hinweis: Dieses Dokument beschreibt Zielarchitektur und Roadmap. Der aktuelle Implementierungsstand steht in `MEMORY.md` und `README.md`. Phase 1-3 sind lokal nutzbar; einzelne Planpunkte wie dauerhaft laufende Celery/Redis-Automation, vollwertige Spezialparser-Modellgewichte und ein live OpenAlex-Embedding-Index bleiben optionale bzw. zukuenftige Integrationen.

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
  version: 1  # erhÃ¶hen bei Modellwechsel â†’ triggert Reindex

harvester:
  max_papers_per_search: 100
  bubble_extension: true
  bubble_extension_count: 30
  rate_limits:
    arxiv: 3
    semantic_scholar: 10

parsing:
  default: "marker"
  formula_threshold: 0.3  # ab X% Formeln â†’ Nougat
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

> Hinweis: Dieses Dokument beschreibt Zielarchitektur und Roadmap. Der aktuelle Implementierungsstand steht in `MEMORY.md` und `README.md`. Phase 1-3 sind lokal nutzbar; einzelne Planpunkte wie dauerhaft laufende Celery/Redis-Automation, vollwertige Spezialparser-Modellgewichte und ein live OpenAlex-Embedding-Index bleiben optionale bzw. zukuenftige Integrationen.

---

## Hardware-Nutzung (RTX 5070 Ti, 16GB VRAM, 64GB RAM)

Bsp.:
| Job | VRAM | Wann |
|---|---|---|
| Qwen3.6 Extraktion | ~12GB | Batch, nachts |
| bge-m3 Embedding | ~2GB | parallel zu allem |
| Nougat PDF-Parsing | ~4GB | Batch |
| R1 Reasoning | ~14GB | On-Demand, interaktiv |
| Gemma 4 Multimodal | ~12GB | Batch, nachts |

**Gleichzeitiger Betrieb**: Qwen3.5 + bge-m3 passen zusammen (14GB). R1 und Gemma 4 laufen exklusiv. Celery-Jobs berÃ¼cksichtigen das Ã¼ber Ressourcen-Locks.
**Sequenzieller Betrieb**: sicherer das alles auf dem Rechner lÃ¤uft

> Hinweis: Dieses Dokument beschreibt Zielarchitektur und Roadmap. Der aktuelle Implementierungsstand steht in `MEMORY.md` und `README.md`. Phase 1-3 sind lokal nutzbar; einzelne Planpunkte wie dauerhaft laufende Celery/Redis-Automation, vollwertige Spezialparser-Modellgewichte und ein live OpenAlex-Embedding-Index bleiben optionale bzw. zukuenftige Integrationen.

---

## Was dieses System explizit NICHT ist

- Kein Ersatz fÃ¼r kritisches wissenschaftliches Lesen â€” es ist ein Navigationswerkzeug
- Keine automatische Peer Review
- Kein System das beweist dass eine Cross-Domain-Verbindung valide ist â€” es schlÃ¤gt vor
- Keine Cloud-Komponente â€” wer Cloud will, verwendet NotebookLM

> Hinweis: Dieses Dokument beschreibt Zielarchitektur und Roadmap. Der aktuelle Implementierungsstand steht in `MEMORY.md` und `README.md`. Phase 1-3 sind lokal nutzbar; einzelne Planpunkte wie dauerhaft laufende Celery/Redis-Automation, vollwertige Spezialparser-Modellgewichte und ein live OpenAlex-Embedding-Index bleiben optionale bzw. zukuenftige Integrationen.

---

## Offene Entscheidungen (vor Implementierungsstart klÃ¤ren)
1. **Nightly-Sync-Frequenz**: TÃ¤glich? WÃ¶chentlich? AbhÃ¤ngig davon wie aktiv das Themenfeld ist.


