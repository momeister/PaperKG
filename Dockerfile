# Backend image for the ScienceKG / PaperKG product API (FastAPI).
#
# Python is pinned to 3.12 on purpose: the Kuzu graph library only ships wheels for
# Python < 3.14 (see requirements.txt). 3.12 has wheels for every dependency.
FROM python:3.12-slim AS base

# Optionally bake a LaTeX toolchain in for the Tiefenanalyse PDF export. It is OFF by
# default to keep the image small — without it the export endpoint gracefully returns a
# ZIP of .tex/.bib/figures instead of a compiled PDF. Build with
#   docker build --build-arg INSTALL_LATEX=true .
ARG INSTALL_LATEX=false

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Build tools are needed for the occasional source-only wheel; cleaned up afterwards.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && if [ "$INSTALL_LATEX" = "true" ]; then \
        apt-get install -y --no-install-recommends \
            latexmk texlive-latex-base texlive-latex-extra \
            texlive-fonts-recommended texlive-bibtex-extra ; \
    fi \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first so the layer caches across code changes.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Application code.
COPY . .

# Runtime data lives here and is expected to be a mounted volume (see docker-compose.yml).
RUN mkdir -p data logs

EXPOSE 8000

# Probe the FastAPI health endpoint without needing curl in the image.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)"

# Bind to 0.0.0.0 inside the container; docker-compose only publishes it to 127.0.0.1 on
# the host because the API has no built-in authentication (set SCIENCEKG_API_TOKEN to add it).
CMD ["uvicorn", "api.product_main:app", "--host", "0.0.0.0", "--port", "8000"]
