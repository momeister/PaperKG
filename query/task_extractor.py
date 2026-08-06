"""Task-Spec-Extraktion für den Task-Focused Mode.

Aus einer URL (z. B. Kaggle-Competition-Seite), einer PDF oder einem Freitext
extrahiert das LLM einen strukturierten Task-Spec:

    {
      "title": "...",
      "objective": "...",
      "evaluation": "...",
      "datasets": [{"name": "...", "install_url": "...", "size": "...", "license": "..."}],
      "timeline": {"start": "...", "end": "..."},
      "rules": ["..."],
      "constraints": ["..."],
      "suggested_directions": [{"label": "...", "rationale": "...", "keywords": ["..."]}]
    }

Das LLM geht **immer** über ``LLMRouter`` (siehe AGENTS.md). URL-Fetches nutzen
``httpx`` und werden vorab über ``harvester.url_guard.is_safe_public_url``
geprüft (SSRF-Schutz). PDFs laufen über den lokalen ``MarkerParser``
(``parsing/marker_parser.py``), der pdfplumber/pypdf in einem Schutzhülle-Prozess
ausführt — niemals ``multiprocessing`` direkt.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

from harvester.url_guard import is_safe_public_url
from parsing.marker_parser import MarkerParser
from query.llm_router import LLMRouter
from research.sanitize import FULL_TEXT_MAX_LEN, sanitize_web_text

#: Obergrenze für den Rohtext, der dem LLM vorgelegt wird (Tokens ~ chars/4).
_SOURCE_TEXT_MAX_LEN = 24000

#: Obergrenze für den im Task-Spec mitgespeicherten Rohtext (verifikation im
#: Frontend, nicht volles Archiv — task_json-Column sonst unnötig groß).
_SOURCE_RAW_TEXT_PERSIST_MAX_LEN = 8000

_EXTRACTOR_SYSTEM = (
    "You are a competition analyst and research engineer. Given the raw text of a "
    "hackathon/competition page, ticket, PDF brief, or free-form task description, "
    "extract a structured task specification that a researcher can act on. "
    "Respond ONLY with JSON (no markdown, no commentary)."
)

_EXTRACTOR_SCHEMA = (
    'Return JSON of the form: {"title": "...", "objective": "...", '
    '"background": "...", "evaluation": "...", '
    '"datasets": [{"name": "...", "install_url": "...", '
    '"size": "...", "license": "..."}], "timeline": {"start": "...", "end": "..."}, '
    '"deadline": "...", "methodology": ["..."], '
    '"rules": ["..."], "constraints": ["..."], '
    '"inclusion_criteria": ["..."], "exclusion_criteria": ["..."], '
    '"suggested_directions": ['
    '{"label": "...", "rationale": "...", "keywords": ["..."]}], '
    '"extra_sections": [{"label": "...", "body": "..."}]}.\n'
    "- objective: what must be built/predicted/answered (1-3 sentences).\n"
    "- background: the context/motivation for the task — why it exists, what "
    "problem it addresses, the domain (1-3 sentences). Empty string if not "
    "evident from the source.\n"
    "- evaluation: metric, leaderboard, judging criteria — quote if explicit.\n"
    "- datasets: every dataset mentioned with its install/download URL if given "
    "(Kaggle competition data, external links). Leave install_url null if absent.\n"
    "- timeline: start/end dates if stated, else empty strings.\n"
    "- deadline: the final submission deadline as a single string (ISO date or "
    "human-readable as stated in the source). Empty string if no deadline is "
    "given. Do NOT infer or fabricate a deadline — only extract what the source "
    "explicitly states.\n"
    "- methodology: suggested or required methods/approaches (e.g. supervised "
    "classification, retrieval-augmented generation, BERT fine-tuning). List each "
    "as a short phrase. Empty list if none are stated.\n"
    "- rules: eligibility, submission format, team size, allowed resources.\n"
    "- constraints: compute, license, privacy, data-use restrictions.\n"
    "- inclusion_criteria: acceptance criteria — what must be true/satisfied for a "
    "submission to count (e.g. metric thresholds, required components).\n"
    "- exclusion_criteria: what disqualifies a submission (forbidden methods, "
    "leakage, missing deliverables).\n"
    "- suggested_directions: 3-6 concrete, distinct technical approaches a "
    "researcher could pursue — diverse, not all the mainstream path. Each with a "
    "short rationale and 2-5 keywords for literature search.\n"
    "- extra_sections: capture EVERY distinct section on the page that does not "
    "fit a dedicated field above. Common examples: Prizes, Score, Submission "
    "File, Code Requirements, Efficiency Prize, Timeline/Schedule details, "
    "Eligibility, Judging, Resources, FAQs, Host/Sponsors, Evaluation Detail, "
    "Dataset Description, Baseline, Starter Code. Each entry: {label = the "
    "section heading as on the page, body = the section content (1-5 sentences, "
    "quote key numbers/dates/URLs)}. Be thorough — do not silently drop content. "
    "Do not duplicate content already placed in a dedicated field.\n"
    "If the source does not describe a concrete task/competition/problem, still "
    'return the JSON shape with empty strings/lists and set "title" to a short '
    "descriptive label of what the source actually is."
)


def extract_task_spec(
    llm_router: LLMRouter,
    source_kind: str,
    source_url: str | None = None,
    source_text: str | None = None,
    source_pdf_path: str | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Extract a normalized TaskSpec from one of three input kinds.

    ``source_kind`` ∈ {'url', 'pdf', 'text'}. For 'url' the page is fetched
    server-side (SSRF-guarded) and sanitized; for 'pdf' the file is parsed via
    ``MarkerParser``; for 'text' the raw string is used directly. The LLM then
    produces the structured spec.

    Returns the normalized TaskSpec dict (see ``normalize_task_spec``). On any
    fetch/parse failure the spec is returned with an ``error`` field and empty
    fields — the caller (router) decides on the HTTP status.
    """
    raw_text, resolved_url, resolved_pdf_path, raw_html = _gather_source(
        source_kind=source_kind,
        source_url=source_url,
        source_text=source_text,
        source_pdf_path=source_pdf_path,
    )
    if not raw_text.strip():
        # Bei JS-SPAs (Kaggle etc.) ist der sanierte Text leer, aber die
        # meta-tags liefern wenigstens Titel + Beschreibung. Wir versuchen
        # trotzdem eine sinnvolle Fehlermeldung + einen besseren Titel als
        # die URL-Slug (sonst heißen alle Tasks "overview").
        html_title = _extract_html_title(raw_html) if raw_html else ""
        return {
            "title": html_title
            or _fallback_title(source_kind, source_url, source_text),
            "objective": "",
            "background": "",
            "evaluation": "",
            "datasets": [],
            "timeline": {"start": "", "end": ""},
            "deadline": "",
            "methodology": [],
            "rules": [],
            "constraints": [],
            "inclusion_criteria": [],
            "exclusion_criteria": [],
            "suggested_directions": [],
            "extra_sections": [],
            "source_raw_text": "",
            "error": (
                "Keine verwertbare Quelle extrahiert (leerer Text). "
                "Mögliche Ursache: JavaScript-generierte Seite (Kaggle, "
                "Hackathon-Host) — nur Meta-Tags gefunden, kein Body-Text."
                if source_kind == "url"
                else "Keine verwertbare Quelle extrahiert (leerer Text)."
            ),
        }

    excerpt = raw_text[:_SOURCE_TEXT_MAX_LEN]
    messages = [
        {"role": "system", "content": _EXTRACTOR_SYSTEM + " " + _EXTRACTOR_SCHEMA},
        {"role": "user", "content": f"Source text:\n{excerpt}"},
    ]
    overrides: dict[str, Any] = {"temperature": 0.2, "max_tokens": 4000}
    if model:
        overrides["model"] = model
    try:
        payload = llm_router.chat_json(messages, provider=provider, overrides=overrides)
    except Exception as exc:  # noqa: BLE001 — surface as structured error
        spec = _empty_spec()
        spec["error"] = f"LLM-Extraktion fehlgeschlagen: {exc}"
        spec["title"] = _fallback_title(source_kind, source_url, source_text)
        return spec

    spec = normalize_task_spec(payload)
    spec["source_url"] = resolved_url
    spec["source_pdf_path"] = resolved_pdf_path
    # Rohtext verifikationshalber mitführen — gekürzt auf 8 KB, damit die
    # task_json-Column nicht aufgebläht wird. Frontend zeigt ihn als einklappbare
    # Quelle. Volltext bleibt nur transient im Speicher.
    if raw_text.strip():
        spec["source_raw_text"] = raw_text[:_SOURCE_RAW_TEXT_PERSIST_MAX_LEN]
    if not spec.get("title"):
        # Bevorzugt den <title>/og:title aus dem Roh-HTML (für JS-SPAs, wo
        # der LLM nur die Meta-Beschreibung sieht), sonst die URL-Slug.
        html_title = _extract_html_title(raw_html) if raw_html else ""
        spec["title"] = html_title or _fallback_title(
            source_kind, source_url, source_text
        )
    return spec


def _gather_source(
    source_kind: str,
    source_url: str | None,
    source_text: str | None,
    source_pdf_path: str | None,
) -> tuple[str, str | None, str | None, str]:
    """Resolve the raw source text.

    Returns ``(text, resolved_url, resolved_pdf_path, raw_html)``. ``raw_html``
    is the unparsed HTML for URL sources (used for meta-tag title fallback);
    empty string for text/pdf sources.
    """
    kind = (source_kind or "text").strip().lower()
    if kind == "text":
        return str(source_text or "").strip(), None, None, ""
    if kind == "url":
        url = (source_url or "").strip()
        if not url:
            return "", None, None, ""
        text, raw_html = _fetch_url_text(url)
        return text, url, None, raw_html
    if kind == "pdf":
        path = (source_pdf_path or "").strip()
        if not path:
            return "", None, None, ""
        text = _parse_pdf_text(path)
        return text, None, path, ""
    # Unknown kind → treat as text
    return str(source_text or "").strip(), None, None, ""


_META_DESC_RE = re.compile(
    r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']*)["\']',
    re.IGNORECASE | re.DOTALL,
)
_META_OG_DESC_RE = re.compile(
    r'<meta\s+property=["\']og:description["\']\s+content=["\']([^"\']*)["\']',
    re.IGNORECASE | re.DOTALL,
)
_META_OG_TITLE_RE = re.compile(
    r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']*)["\']',
    re.IGNORECASE | re.DOTALL,
)
_HTML_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _extract_meta_fallback(raw_html: str) -> str:
    """Build a fallback text from <meta description>/<title> tags for JS-SPAs.

    Many competition pages (Kaggle, Hackathon-Hosts) are client-side rendered:
    the ``<body>`` is just ``<div id="root"></div>`` and the actual content
    lives in ``<meta name="description">`` / ``<meta property="og:*">`` / ``<title>``.
    ``sanitize_web_text`` strips everything and returns empty. This helper
    salvages the meta tags so the LLM has at least *something* to extract from.
    """
    if not raw_html:
        return ""
    parts: list[str] = []
    for pattern in (_META_OG_TITLE_RE, _HTML_TITLE_RE):
        m = pattern.search(raw_html)
        if m and m.group(1).strip():
            parts.append(m.group(1).strip())
            break
    for pattern in (_META_DESC_RE, _META_OG_DESC_RE):
        m = pattern.search(raw_html)
        if m and m.group(1).strip():
            parts.append(m.group(1).strip())
            break
    return "\n\n".join(parts)


def _extract_html_title(raw_html: str) -> str:
    """Best-effort page title from raw HTML (og:title preferred, then <title>)."""
    if not raw_html:
        return ""
    for pattern in (_META_OG_TITLE_RE, _HTML_TITLE_RE):
        m = pattern.search(raw_html)
        if m and m.group(1).strip():
            return m.group(1).strip()[:200]
    return ""


def _fetch_url_text(url: str) -> tuple[str, str]:
    """SSRF-guarded synchronous URL fetch.

    Returns ``(sanitized_text, raw_html)``. The sanitized text is empty on
    fetch failure or when the page is a JS-SPA with no static body content;
    in the latter case the caller can use ``raw_html`` to salvage meta tags
    via :func:`_extract_meta_fallback`.
    """
    if not is_safe_public_url(url):
        return "", ""
    try:
        with httpx.Client(
            timeout=30.0,
            headers={"User-Agent": "ScienceKG/TaskFocused (local)"},
            follow_redirects=True,
        ) as client:
            response = client.get(url)
            response.raise_for_status()
    except Exception:  # noqa: BLE001
        return "", ""
    raw_html = response.text or ""
    clean, _flags = sanitize_web_text(raw_html, max_len=FULL_TEXT_MAX_LEN)
    if not clean:
        # JS-SPA: body ist leer, aber meta-tags enthalten oft eine Beschreibung.
        clean = _extract_meta_fallback(raw_html)
    if not clean or len(clean) < _SPA_HEADLESS_FALLBACK_MIN_LEN:
        # Seite ist client-side gerendert (Kaggle, Hackathon-Hosts): der
        # statische HTML-Body ist leer bzw. liefert nur kurze Meta-Tags. Der
        # eigentliche Inhalt (Ziel, Hintergrund, Evaluation, Timeline, Regeln,
        # Daten) wird erst zur Laufzeit via XHR nachgeladen — ``httpx`` sieht
        # davon nichts. Optionaler Headless-Render via Playwright/Chromium
        # holt die gerenderte Seite; ist Playwright nicht installiert, bleibt
        # der Meta-Fallback bestehen (mit einer Warnung im Specc).
        rendered = _headless_render(url)
        if rendered:
            clean = rendered
    return clean, raw_html


#: Ab dieser Länge des statischen (sanierten/meta) Textes verzichten wir auf
#: Headless-Rendering — der statische HTML-Body enthielt dann schon
#: Substanz. 300 Zeichen ist eine konservative Schwelle: Meta-Tags
#: (og:title + description) einer JS-SPA liegen typischerweise darunter
#: (~150 Zeichen), eine echte Server-gerenderte Seite deutlich darüber.
_SPA_HEADLESS_FALLBACK_MIN_LEN = 300


def _headless_render(url: str) -> str:
    """Optional render a JS-SPA via Playwright/Chromium. Empty if unavailable.

    Only invoked when the static fetch produced no usable text (JS-SPA). The
    function is defensive: any failure (Playwright missing, browser launch
    failed, page load timeout, navigation error) returns an empty string so
    the caller falls back to the meta-tag text. SSRF-guard already happened
    upstream (``is_safe_public_url``); the browser is pointed only at the
    *same* URL that passed the static fetch.

    Opt-out: ``SCIENCEKG_DISABLE_HEADLESS_RENDER=1`` skips entirely.
    """
    if os.getenv("SCIENCEKG_DISABLE_HEADLESS_RENDER", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return ""
    try:  # noqa: BLE001 — Playwright ist optional
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
    except Exception:
        return ""
    # Kaggle-Competition? Dann öffentliche Sub-Tabs in einer Session crawlen
    # (Overview/Rules/Timeline/Discussion/Code). Auth-gated Tabs (Data/
    # Leaderboard) werden als Hinweis markiert — sie brauchen Web-Cookie-Auth,
    # die KAGGLE_* API-Creds liefern keine Web-Session (siehe .env.example).
    kaggle_match = re.match(r"(https?://www\.kaggle\.com/competitions/[\w-]+)", url)
    if kaggle_match:
        return _render_kaggle_tabs(sync_playwright, kaggle_match.group(1))
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                # networkidle wartet bis keine XHRs mehr laufen — Kaggle lädt
                # die Competition-Daten damit nach. ~10s Decke; längere Warte
                # wäre für den Nutzer spürbar.
                page.goto(url, wait_until="networkidle", timeout=15000)
                # document.body.innerText liefert den sichtbaren Text ohne
                # Script/Style-Content. Nachbereitung via sanitize_web_text.
                inner = page.evaluate("() => document.body.innerText || ''")
            finally:
                browser.close()
    except Exception:  # noqa: BLE001 — fail-soft, caller nutzt Meta-Fallback
        return ""
    if not isinstance(inner, str) or not inner.strip():
        return ""
    clean, _flags = sanitize_web_text(inner, max_len=FULL_TEXT_MAX_LEN)
    return clean


# Öffentliche Kaggle-Competition-Tabs. Diese sind ohne Login sichtbar. Die
# Reihenfolge folgt der Kaggle-Top-Navigation. ``label`` ist der Header im
# extrahierten Text, ``path`` der Sub-Pfad hinter der Competition-URL.
_KAGGLE_PUBLIC_TABS: list[tuple[str, str]] = [
    ("Overview", "/overview"),
    ("Rules", "/rules"),
    ("Timeline", "/timeline"),
    ("Discussion", "/discussion"),
    ("Code", "/code"),
]

# Auth-gated Tabs — werden im extrahierten Text als Hinweis aufgeführt statt
# leer gerendert zu werden. Verhindert dass das LLM Placeholder-Bodies wie
# "Tab available but no content provided in source" generiert.
_KAGGLE_AUTH_GATED_TABS: list[tuple[str, str]] = [
    ("Data", "/data"),
    ("Leaderboard", "/leaderboard"),
]

_AUTH_GATED_HINT = "(auth-gated — Inhalt nicht extrahiert, Login erforderlich)"

# Text, der im UI-Feld erscheint, wenn das LLM einen Placeholder-Body erzeugt
# hat (z. B. "Tab available but no content provided in source"). Wir ersetzen
# ihn durch diesen Hinweis, damit der Nutzer sieht: Tab existiert, aber nicht
# extrahierbar ohne Auth.
_AUTH_GATED_PLACEHOLDER = (
    "Tab-Inhalt nicht extrahiert (auth-gated — Login erforderlich)."
)

# Heuristik: LLM-generierte Placeholder-Bodies. Gematched wird case-insensitiv
# auf Teilstrings, die typischerweise auftreten, wenn der Tab in der Navigation
# sichtbar war, aber kein Body im Quelltext stand. Bewusst eng gehalten, damit
# echte Inhalte nicht fälschlich als Placeholder klassifiziert werden.
_PLACEHOLDER_MARKERS = (
    "no content provided in source",
    "tab available but no content",
    "content not available in source",
    "no content provided",
    "inhalt nicht verfügbar",
    "kein inhalt vorhanden",
)


def _is_placeholder_body(body: str) -> bool:
    """Erkennt LLM-generierte Placeholder-Bodies wie 'Tab available but no
    content provided in source'. Case-insensitive Teilstring-Suche."""
    lowered = body.lower().strip()
    if not lowered:
        return True
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def _render_kaggle_tabs(sync_playwright, base_url: str) -> str:
    """Crawle öffentliche Kaggle-Competition-Tabs in einer Browser-Session.

    ``base_url`` ist die Competition-Root ohne Sub-Pfad (z. B.
    ``https://www.kaggle.com/competitions/rsna-knee-abnormality-detection``).
    Auth-gated Tabs (Data/Leaderboard) werden als Hinweis eingefügt, nicht
    leer gerendert. Fallback bei jedem Fehler: leerer String → Meta-Fallback.
    """
    sections: list[str] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                # Overview zuerst — das ist die submitted URL und etabliert
                # die Session. networkidle wartet auf die SPA-Hydratation.
                first = True
                for label, path in _KAGGLE_PUBLIC_TABS:
                    tab_url = base_url + path
                    try:
                        if first:
                            page.goto(tab_url, wait_until="networkidle", timeout=15000)
                            first = False
                        else:
                            # Nachfolgende Navigations; kürzeres Timeout, da
                            # die Session steht und nur die Tab-Route hydratisiert.
                            page.goto(tab_url, wait_until="networkidle", timeout=12000)
                        inner = page.evaluate("() => document.body.innerText || ''")
                    except Exception:  # noqa: BLE001 — einzelner Tab darf scheitern
                        inner = ""
                    if isinstance(inner, str) and inner.strip():
                        sections.append(f"## {label}\n\n{inner}")
                    else:
                        sections.append(f"## {label}\n\n{_AUTH_GATED_HINT}")
            finally:
                browser.close()
    except Exception:  # noqa: BLE001 — Browser-Launch-Fail → Meta-Fallback
        return ""
    # Auth-gated Tabs immer als Hinweis anhängen, damit das LLM die
    # Tab-Struktur sieht und keinen Placeholder-Body erfindet.
    for label, _path in _KAGGLE_AUTH_GATED_TABS:
        sections.append(f"## {label}\n\n{_AUTH_GATED_HINT}")
    combined = "\n\n".join(sections)
    if not combined.strip():
        return ""
    clean, _flags = sanitize_web_text(combined, max_len=FULL_TEXT_MAX_LEN)
    return clean


def _parse_pdf_text(path: str) -> str:
    """Parse a local PDF via the guarded MarkerParser. Returns text (empty on failure)."""
    try:
        parsed = MarkerParser().parse(path, paper_id="task_ingest")
        return str(parsed.text or "")
    except Exception:  # noqa: BLE001
        return ""


def _empty_spec() -> dict[str, Any]:
    return {
        "title": "",
        "objective": "",
        "background": "",
        "evaluation": "",
        "datasets": [],
        "timeline": {"start": "", "end": ""},
        "deadline": "",
        "methodology": [],
        "rules": [],
        "constraints": [],
        "inclusion_criteria": [],
        "exclusion_criteria": [],
        "suggested_directions": [],
        "extra_sections": [],
        "source_raw_text": "",
    }


def _fallback_title(
    source_kind: str, source_url: str | None, source_text: str | None
) -> str:
    if source_kind == "url" and source_url:
        # Letzte Pfadkomponente, Menschen-lesbar gemacht.
        slug = source_url.rstrip("/").rsplit("/", 1)[-1]
        slug = re.sub(r"[-_]+", " ", slug).strip()
        return slug[:120] or "Aufgabe"
    if source_text:
        first_line = (
            str(source_text).strip().splitlines()[0] if source_text.strip() else ""
        )
        return (first_line or "Aufgabe")[:120]
    return "Aufgabe"


def normalize_task_spec(payload: Any) -> dict[str, Any]:
    """Coerce a raw LLM payload into the canonical TaskSpec shape."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            payload = {}
    if not isinstance(payload, dict):
        payload = {}

    def _str(value: Any) -> str:
        return str(value or "").strip()

    raw_datasets = payload.get("datasets") or []
    datasets: list[dict[str, Any]] = []
    if isinstance(raw_datasets, list):
        for item in raw_datasets:
            if isinstance(item, dict):
                datasets.append(
                    {
                        "name": _str(item.get("name")),
                        "install_url": _str(item.get("install_url")) or None,
                        "size": _str(item.get("size")) or None,
                        "license": _str(item.get("license")) or None,
                    }
                )
            elif isinstance(item, str) and item.strip():
                datasets.append(
                    {
                        "name": item.strip(),
                        "install_url": None,
                        "size": None,
                        "license": None,
                    }
                )

    raw_timeline = payload.get("timeline") or {}
    timeline = {
        "start": (
            _str(raw_timeline.get("start")) if isinstance(raw_timeline, dict) else ""
        ),
        "end": _str(raw_timeline.get("end")) if isinstance(raw_timeline, dict) else "",
    }

    raw_directions = payload.get("suggested_directions") or []
    directions: list[dict[str, Any]] = []
    if isinstance(raw_directions, list):
        for item in raw_directions:
            if isinstance(item, dict):
                keywords_raw = item.get("keywords") or []
                keywords = (
                    [str(k).strip() for k in keywords_raw if str(k).strip()]
                    if isinstance(keywords_raw, list)
                    else []
                )
                directions.append(
                    {
                        "label": _str(item.get("label")),
                        "rationale": _str(item.get("rationale")),
                        "keywords": keywords[:8],
                    }
                )
            elif isinstance(item, str) and item.strip():
                directions.append(
                    {"label": item.strip(), "rationale": "", "keywords": []}
                )

    # extra_sections: dynamische Catch-All-Sections (Prizes, Score, Submission
    # File, Code-Requirements, Efficiency Prize, …). Jede Section: {label, body}.
    # Leere Labels/Bodies werden verworfen, max 20 Sections.
    raw_extra = payload.get("extra_sections") or []
    extra_sections: list[dict[str, str]] = []
    if isinstance(raw_extra, list):
        for item in raw_extra:
            if isinstance(item, dict):
                label = _str(item.get("label"))
                body = _str(item.get("body"))
                if label and body:
                    # LLM-generierte Placeholder-Bodies (entsteht, wenn ein Tab
                    # in der Navigation sichtbar war, aber kein Inhalt im
                    # Quelltext stand — typisch für auth-gated Kaggle-Tabs).
                    # Statt hart zu verwerfen, als auth-gated markieren, damit
                    # der Nutzer sieht dass der Tab existiert.
                    if _is_placeholder_body(body):
                        body = _AUTH_GATED_PLACEHOLDER
                    extra_sections.append({"label": label, "body": body})
            elif isinstance(item, str) and item.strip():
                # Bare-String-Eintrag → als Body mit leerem Label verwerfen
                # (label ist erforderlich für die UI).
                pass

    return {
        "title": _str(payload.get("title")),
        "objective": _str(payload.get("objective")),
        "background": _str(payload.get("background")),
        "evaluation": _str(payload.get("evaluation")),
        "datasets": datasets,
        "timeline": timeline,
        "deadline": _str(payload.get("deadline")),
        "methodology": [_str(m) for m in (payload.get("methodology") or []) if _str(m)],
        "rules": [_str(r) for r in (payload.get("rules") or []) if _str(r)],
        "constraints": [_str(c) for c in (payload.get("constraints") or []) if _str(c)],
        "inclusion_criteria": [
            _str(c) for c in (payload.get("inclusion_criteria") or []) if _str(c)
        ],
        "exclusion_criteria": [
            _str(c) for c in (payload.get("exclusion_criteria") or []) if _str(c)
        ],
        "suggested_directions": directions[:8],
        "extra_sections": extra_sections[:20],
        # Rohtext passthrough — wird beim Extrahieren von extract_task_spec
        # gesetzt, beim PATCH-edit vom Frontend mitgeführt. Wird nicht vom LLM
        # generiert, deshalb ``or ""``-Fallback.
        "source_raw_text": _str(payload.get("source_raw_text")),
    }
