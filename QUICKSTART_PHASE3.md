# How to Use Phase 3 - Quick Reference Guide

Phase 3 is the working extraction layer of ScienceKG. It is not a toy demo UI: you can choose a provider, choose a model, tune context size and output length, upload or download PDFs, and extract concepts, methods, and claims into a structured result.

What Phase 3 currently can do:
- Switch between Ollama, LM Studio, OpenAI, NVIDIA NIM, and custom OpenAI-compatible APIs.
- Choose the active model per provider.
- Tune temperature, top_p, context size, and max_tokens from the UI.
- Paste text, upload a PDF, or download a PDF from a URL and parse it locally.
- Save PDFs to `data/pdfs` so you can reuse them later.
- Run entity extraction, entity linking, vocabulary management, embeddings, and conflict detection.

What Phase 3 does not replace:
- The citation graph / graph visualization remains a Phase 2 feature and is still available in `ui/graph_visualization.py`.

## One-Line Start

```bash
python scripts/run_phase3.py
```

Then open:
- **UI**: http://localhost:8501
- **API**: http://localhost:8000/docs
- **Graph View (Phase 2)**: `streamlit run ui/graph_visualization.py`

---

## Configuration: Switch LLM Providers

Edit `config.yaml`:

```yaml
llm:
    default_provider: "ollama"  # Change this to: "lm_studio", "openai", "nvidia", or "custom_api"
    providers:
        ollama:
            models:
                - "qwen3.6-35b"
                - "llama3.1:8b"
```

Or pass as parameter:

```python
result = pipeline.process(
    paper_id,
    text,
    provider="lm_studio",  # Override the provider here
    overrides={"model": "gpt-4o"}  # Override the model here if needed
)
```

---

## Adjust LLM Settings

### In Code
```python
result = pipeline.process(
    paper_id,
    text,
    provider="ollama",
    overrides={
        "temperature": 0.1,      # Lower = more deterministic
        "top_p": 0.9,            # Nucleus sampling
        "max_tokens": 4096,      # Max output length
        "context_size": 131072,   # Max input context
        "model": "qwen3.6-35b"   # Exact model name for the provider
    }
)
```

### In UI
Use the sidebar controls under "⚙️ Configuration".

The sidebar now exposes:
- Provider selection
- Model selection for the chosen provider
- A refresh button that re-queries Ollama/LM Studio/OpenAI-compatible model lists
- Temperature
- Top P
- Context Size up to 262144
- Max Tokens up to 65536

### In config.yaml
```yaml
providers:
  ollama:
        models:
            - "qwen3.6-35b"
            - "llama3.1:8b"
    temperature: 0.2          # Default for this provider
    top_p: 0.95
    max_tokens: 2048
    context_size: 32768
```

Tip: for larger local models, increase `context_size` instead of only `max_tokens`. `context_size` controls how much source text the model can see; `max_tokens` controls how long the answer may be.

---

## Google Gemini API / Gemini 3.1 Flash Lite

The built-in `gemini` provider uses Google's OpenAI-compatible Gemini API endpoint:

```yaml
llm:
  providers:
    gemini:
      provider_type: "openai_compatible"
      base_url: "https://generativelanguage.googleapis.com/v1beta/openai"
      api_key_env: "GEMINI_API_KEY"
      model: "gemini-3.1-flash-lite"
      extra_options:
        force_response_format: true
        omit_extra_body: true
```

Store the key outside Git:

```bash
# PowerShell
$env:GEMINI_API_KEY="..."

# Or copy .env.example to .env and set:
GEMINI_API_KEY=...
```

After restarting Phase 3, choose `gemini` in the sidebar. `gemini-3.1-flash-lite` currently has a free Gemini Developer API tier in Google AI Studio, subject to Google's current limits and data-use terms for the free tier.

---

## NVIDIA NIM / Kimi K2.6

The built-in `nvidia` provider uses NVIDIA's OpenAI-compatible endpoint:

```yaml
llm:
  providers:
    nvidia:
      provider_type: "nvidia"
      base_url: "https://integrate.api.nvidia.com/v1"
      api_key_env: "NVIDIA_API_KEY"
      model: "moonshotai/kimi-k2.6"
```

Store the real key outside Git:

```bash
# PowerShell
$env:NVIDIA_API_KEY="nvapi-..."

# Or copy .env.example to .env and set:
NVIDIA_API_KEY=nvapi-...
# NGC_API_KEY=nvapi-... also works.
```

In the Streamlit sidebar you can also paste the NVIDIA key into the password field. That key is kept only in the current Streamlit session and is not written to `config.yaml`.

To switch NVIDIA models, choose another configured model in the sidebar, click **Refresh models manually** after the key is set, or enter a model ID in **Custom NVIDIA model ID**.

If NVIDIA returns `403 Forbidden` with `Authorization failed`, the extraction did not reach the model. Rotate or regenerate the key and make sure the generated key includes both **NGC Catalog** and **Public API Endpoints**. The sidebar's **Test NVIDIA/NIM connection** button checks this before running a multi-call extraction.

For the self-hosted NIM path from build.nvidia.com (`nim=self-hosted`), use `nvidia_local_nim` instead of `nvidia`. The key is used by Docker/NIM to pull and cache the model; PaperKG calls your local OpenAI-compatible endpoint:

```yaml
llm:
  providers:
    nvidia_local_nim:
      provider_type: "nvidia"
      base_url: "http://localhost:8000/v1"
      api_key: null
      model: "moonshotai/kimi-k2.6"
```

The sidebar shows container controls when `nvidia_local_nim` is selected:

- `Docker login` authenticates Docker against `nvcr.io` using `NGC_API_KEY`/`NVIDIA_API_KEY` through stdin.
- `Pull image` downloads the configured image.
- `Start NIM` runs the container detached and exposes `http://localhost:8000/v1`.
- `Stop NIM` stops the local container.
- `Show NIM logs` displays recent startup and model-loading logs.

Default Kimi K2.6 self-hosted image:

```yaml
llm:
  nim_container:
    image: "nvcr.io/nim/moonshotai/kimi-k2.6:1.7.0-variant"
    container_name: "sciencekg-kimi-k2-6-nim"
    host_port: 8000
    cache_dir: "~/.cache/nim"
```

After the container is running, click **Test NVIDIA/NIM connection**. If your container exposes another port or model alias, update the sidebar container port/config or enter the served model ID in **Custom NVIDIA model ID**.

---

## Common Tasks

### Extract Entities from Text

```python
from extraction.entity_linker import ExtractionPipeline
from query.llm_router import LLMRouter

llm = LLMRouter.from_config_file("config.yaml")
pipeline = ExtractionPipeline(llm)

result = pipeline.process(
    paper_id="arxiv_2024_001",
    text="Your paper text here...",
    provider="ollama"
)

for concept in result.concepts:
    print(f"- {concept['label']} (confidence: {concept['confidence']:.1%})")
```

### Extract from PDF File

```python
from parsing.marker_parser import MarkerParser
from extraction.entity_linker import ExtractionPipeline
from query.llm_router import LLMRouter
from storage.file_manager import FileManager

# Parse PDF
parser = MarkerParser()
parsed = parser.parse("/path/to/paper.pdf", "paper_id")

# Optional: save the PDF locally for reuse
file_manager = FileManager("data/pdfs")
saved_path = file_manager.save_pdf("paper_id", open("/path/to/paper.pdf", "rb").read())

# Extract entities
llm = LLMRouter.from_config_file("config.yaml")
pipeline = ExtractionPipeline(llm)
result = pipeline.process("paper_id", parsed.text)
```

### Manage Custom Vocabulary

```python
from extraction.vocabulary import VocabularyManager

vocab = VocabularyManager()

# Register custom term
vocab.register(
    "Neural Network",
    aliases=["NN", "neural net"],
    openalx_id="C123",
    domain="Machine Learning"
)

# Normalize terms
canonical = vocab.normalize("NN")  # → "Neural Network"

# Save for later use
import json
with open("vocabulary.json", "w") as f:
    json.dump(vocab.to_dict(), f)
```

### Detect Conflicts Between Claims

```python
from extraction.conflict_detector import ConflictDetector
from query.llm_router import LLMRouter

llm = LLMRouter.from_config_file("config.yaml")
detector = ConflictDetector(llm)

# Analyze two claims
analysis = detector.analyze_claim_pair(
    "Climate is warming",
    "Temperature is decreasing",
    provider="ollama"
)

print(f"Type: {analysis.conflict_type}")  # "contradictory"
print(f"Confidence: {analysis.confidence:.0%}")

# Find contradictions in batch
claims = ["Claim A", "Claim B", "Claim C"]
all_analyses = detector.analyze_claims_batch(claims)
contradictions = detector.find_contradictions(all_analyses, threshold=0.7)
```

### Batch Process Multiple Papers

```python
from extraction.batch_processor import BatchProcessor
from parsing.parser_router import ParserRouter
from query.llm_router import LLMRouter

llm = LLMRouter.from_config_file("config.yaml")
parser_router = ParserRouter()
processor = BatchProcessor(llm, parser_router)

status = processor.process_papers(
    paper_ids=["paper_1", "paper_2", "paper_3"],
    pdf_paths={
        "paper_1": "/path/to/paper1.pdf",
        "paper_2": "/path/to/paper2.pdf",
        "paper_3": "/path/to/paper3.pdf",
    },
    llm_provider="ollama",
    llm_overrides={"temperature": 0.1}
)

print(f"Processed: {status.papers_processed}/{status.papers_total}")
```

---

## API Usage (curl examples)

### Extract Entities

```bash
curl -X POST "http://localhost:8000/extraction/extract" \
  -H "Content-Type: application/json" \
  -d '{
    "paper_id": "test_001",
    "text": "Neural networks are powerful machine learning models...",
    "provider": "ollama",
    "temperature": 0.1,
    "max_tokens": 2048
  }'
```

### List Available Providers

```bash
curl "http://localhost:8000/extraction/providers"
```

### Start Batch Job

```bash
curl -X POST "http://localhost:8000/extraction/batch" \
  -H "Content-Type: application/json" \
  -d '{
    "paper_ids": ["p1", "p2"],
    "pdf_paths": {"p1": "/path/p1.pdf", "p2": "/path/p2.pdf"},
    "provider": "ollama"
  }'
```

### Get Job Status

```bash
curl "http://localhost:8000/extraction/batch/job_id_here"
```

---

## PDF Workflow In The UI

If you want to work end-to-end in the browser:

1. Pick a provider and model in the sidebar.
2. Adjust context size and max tokens.
3. Choose one of three input modes:
    - Paste text if you already have plain text.
    - Upload PDF if you already downloaded the file.
    - PDF URL if you want the app to fetch the PDF for you.
4. The app stores the PDF in `data/pdfs` and offers a download button for uploaded files.
5. Press `Extract Entities` and review concepts, methods, and claims.

If a PDF download fails, the usual causes are a bad URL, an HTML landing page instead of the PDF file, or a provider timeout.

---

## Troubleshooting

### LLM Not Responding

**Problem**: Getting "LLM timeout" errors

**Solution**:
1. Check Ollama/LM Studio is running
2. Increase timeout in `config.yaml`: `timeout_seconds: 300`
3. Reduce max_tokens in settings

### Parser Not Detecting Type Correctly

**Problem**: Using wrong parser for PDF type

**Solution**:
```python
from parsing.parser_router import ParserType

# Force specific parser
parsed = parser_router.parse(
    "/path/to/paper.pdf",
    "paper_id",
    force_parser=ParserType.NOUGAT
)
```

### Out of Memory on Large Batch

**Problem**: Batch processing failing

**Solution**:
```python
# Process in smaller chunks
for i in range(0, len(paper_ids), 10):
    chunk = paper_ids[i:i+10]
    status = processor.process_papers(chunk, pdf_paths)
```

---

## Performance Tips

1. **Use lower temperature for consistency**: 0.1-0.2 instead of 1.0
2. **Reduce max_tokens if you don't need long outputs**: 1024 instead of 4096
3. **Enable embeddings only when needed**: Use `embed_concepts=False` by default
4. **Use local models** (Ollama) for privacy and speed over cloud APIs

---

## Environment Variables

Set these in your shell or `.env` file:

```bash
# For OpenAI API
export OPENAI_API_KEY="sk-..."

# For NVIDIA NIM / build.nvidia.com
export NVIDIA_API_KEY="nvapi-..."

# For custom APIs
export CUSTOM_API_KEY="your_key_here"

# Logging
export LOG_LEVEL="INFO"  # DEBUG, INFO, WARNING, ERROR
```

---

## What's Next?

- **Phase 4**: Query engine for semantic search
- **Phase 5**: Quality assurance and automated workflows

Current status: ✅ Phase 3 Complete - All 39 tests passing
