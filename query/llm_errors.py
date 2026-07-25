"""Einordnung von LLM-Fehlern in verstaendliche Kategorien.

Der LLM-Pfad verliert die Fehlerursache auf dem Weg nach oben: ``LLMRouter``
verpackt jeden HTTP-Fehler in einen generischen ``RuntimeError``, die Extraktion
faengt in ``_llm_calls.py`` *jede* Exception ab und macht daraus den Freitext
``"LLM call failed: …"``, und die Qualitaetspruefung fasst das am Ende zu
"LLM extraction failed for every extraction call" zusammen. Ein aufgebrauchtes
Kontingent war damit nicht mehr von einem Parser-Fehler zu unterscheiden — der
Nutzer sah nur, dass "irgendwas" schiefging, und der Batch feuerte munter
weitere 400 Anfragen gegen dieselbe Wand.

Dieses Modul war urspruenglich privat in ``query/research_tree.py`` (nur die
Tiefenanalyse nutzte es). Hier liegt es fuer alle Aufrufer — insbesondere fuer
die Batch-Extraktion, die bei ``quota``/``rate_limit``/``auth`` abbrechen
statt weiterlaufen sollte.
"""
from __future__ import annotations

__all__ = ["classify_llm_error", "ERROR_KIND_PREFIX", "tag_error", "parse_tagged_error", "is_provider_limit"]

#: Fehlerarten, bei denen jeder weitere Aufruf ebenfalls scheitern wird.
#: Ein Batch soll hier stoppen statt hunderte Male dieselbe Absage einzusammeln.
_TERMINAL_KINDS = frozenset({"quota", "rate_limit", "auth"})


def classify_llm_error(error: str) -> tuple[str, str]:
    """Bilde einen rohen LLM-Fehlertext auf ``(kind, deutscher Klartext)`` ab.

    ``kind`` ist eins von ``empty | quota | rate_limit | auth | context_length |
    connection | unknown`` und erlaubt der UI zu sagen *warum* etwas scheiterte.
    """
    e = (error or "").lower()
    if e in ("empty_synthesis", "empty_response") or "empty response" in e:
        return (
            "empty",
            "Das Modell lieferte eine leere Antwort (evtl. abgeschnitten oder überlastet). "
            "Erneut versuchen oder ein anderes Modell wählen.",
        )
    if any(k in e for k in ("insufficient_quota", "quota", "resource_exhausted", "billing", "credit", "exhausted")):
        return (
            "quota",
            "LLM-Kontingent/Guthaben aufgebraucht. Deine KI-Anfragen für diesen Anbieter sind "
            "vorerst erschöpft — warte (z.B. bis morgen) oder wechsle Provider/API-Key.",
        )
    if any(k in e for k in ("rate limit", "rate_limit", "ratelimit", "429", "too many requests")):
        return (
            "rate_limit",
            "Rate-Limit des LLM-Anbieters erreicht (HTTP 429). Zu viele Anfragen in kurzer Zeit — "
            "kurz warten und erneut versuchen, oder Tiefe/Zweige reduzieren.",
        )
    if any(k in e for k in ("401", "403", "unauthorized", "invalid api key", "invalid_api_key", "authentication", "api key", "permission")):
        return (
            "auth",
            "Authentifizierung fehlgeschlagen — API-Key fehlt oder ist ungültig. "
            "Prüfe den Key in .env / config.yaml.",
        )
    if any(k in e for k in ("context length", "maximum context", "context_length", "too many tokens", "reduce the length", "context window")):
        return (
            "context_length",
            "Anfrage überschreitet das Kontextfenster des Modells. Reduziere Tiefe/Zweige oder die "
            "Anzahl der einbezogenen Quellen.",
        )
    if any(k in e for k in ("timeout", "timed out", "connection", "connect", "refused", "max retries", "name or service", "unreachable", "econnrefused")):
        return (
            "connection",
            "Keine Verbindung zum LLM (Timeout/Connection). Läuft LM Studio bzw. der konfigurierte "
            "Anbieter und ist er erreichbar?",
        )
    return ("unknown", f"LLM-Aufruf fehlgeschlagen: {(error or '').strip()[:300]}")


ERROR_KIND_PREFIX = "[llm:"


def tag_error(kind: str, message: str) -> str:
    """Stelle einer Fehlermeldung ihre Art voran: ``"[llm:quota] …"``.

    So reist die Einordnung durch ``batch_job_items.error_message`` und
    ``extraction_results.error_message`` bis ins Frontend, ohne dass das
    DuckDB-Schema eine zusaetzliche Spalte braucht.
    """
    return f"{ERROR_KIND_PREFIX}{kind}] {message}"


def parse_tagged_error(message: str | None) -> tuple[str | None, str]:
    """Umkehrung von :func:`tag_error` — ``(kind, Restnachricht)``."""
    text = (message or "").strip()
    if not text.startswith(ERROR_KIND_PREFIX):
        return (None, text)
    closing = text.find("]")
    if closing == -1:
        return (None, text)
    kind = text[len(ERROR_KIND_PREFIX):closing].strip()
    return (kind or None, text[closing + 1:].strip())


def is_provider_limit(kind: str | None) -> bool:
    """Wuerde jeder weitere Aufruf ebenfalls scheitern? Dann Batch abbrechen."""
    return kind in _TERMINAL_KINDS
