"""Der Begleiter: Fragen an eine fremde Codebase, mit geprüften Belegen.

Die Arbeitsteilung ist der ganze Punkt dieses Moduls. **Python fährt die
Schleife, Rust behält die Beleg-Invariante.** Hier steht, welches Modell gefragt
wird und in welcher Reihenfolge; ob ein Zitat gelten darf, entscheidet
``cs_llm::citation`` auf der anderen Seite der Rohrleitung — vier Prüfungen:
Datei im Index, Zeilen in *dieser* Sitzung nachgeschlagen, Datei seither
unverändert, wörtliches Zitat byte-gleich.

Diese Trennung ist keine Formalie. Ein Modell kann eine echte Datei mit einer
plausiblen Zeilennummer erfinden, und nichts an der Antwort sähe falsch aus.
Deshalb ist die Sitzung auf der Rust-Seite die *Lizenz zum Zitieren*: zitiert
werden darf nur, was ein Werkzeugaufruf in derselben Sitzung tatsächlich
herausgegeben hat. Würde Python das nachbauen, wäre die Prüfung etwas, das man
hier vergessen könnte.

Der Systemprompt ist aus ``codesearch/crates/cs-llm/src/lib.rs`` übernommen —
Wortlaut und Regeln unverändert, damit Antworten aus PaperKG und aus dem
CodeSearch-CLI dieselben sind.

Optional kommen **Papers** dazu (:mod:`query.hybrid_retriever`). Dann gilt die
``[arxiv:…]``-Regel aus ``CLAUDE.md`` zusätzlich: Papers werden mit ihrer ID
zitiert, Code mit ``pfad/datei.py:zeile``, und ein nacktes ``[1]`` ist ein
Qualitätsfehler. Geprüft wird beides — Code in Rust, Papers gegen die Menge der
tatsächlich abgerufenen IDs.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from codegraph import service
from codegraph.binary import CodeSearchMissingError
from codegraph.llm_prefs import model_overrides
from codegraph.rpc import CodeGraphError
from query.llm_router import LLMRouter, ToolCall

#: Wie viele Werkzeugrunden nach dem vorab geholten Kontext. Absichtlich wenig:
#: die deterministische Vorab-Suche hat die wahrscheinlichen Symbole, ihre
#: Aufrufer und den Quelltext schon hingelegt. Jede weitere Runde verarbeitet den
#: gewachsenen Prompt komplett neu — auf CPU-Inferenz sind das Minuten.
MAX_ROUNDS = 3

#: Trennt die Prosa vom Spur-Block am Ende der Antwort.
TRAIL_MARKER = "SPUR:"

#: Wörtlich aus ``cs-llm/src/lib.rs``. Wer ihn ändert, ändert ihn dort mit.
SYSTEM_PROMPT = """Du hilfst dabei, eine fremde Codebase zu verstehen. Du beantwortest die Frage: warum funktioniert das so, wie es funktioniert?

Du kennst diesen Code nicht. Was du siehst, ist der nachgeschlagene Ausschnitt oben plus das, was du über deine Werkzeuge holst. Alles andere weißt du nicht — auch dann nicht, wenn du das Framework zu kennen glaubst.

Meistens steht die Antwort schon im nachgeschlagenen Ausschnitt. Lies ihn erst und antworte daraus. Stehen dir Werkzeuge zur Verfügung, greif nur dann dazu, wenn dort wirklich etwas fehlt — und niemals zweimal zum selben Aufruf.

Feste Regeln:

1. Belege jede Aussage über den Code mit `pfad/datei.py:zeile` oder `pfad/datei.py:von-bis`. Zitiere nur Zeilen, die du über `get_node` oder `read_lines` tatsächlich bekommen hast.
   - Richtig: `pricing.py:14` für das, was in Zeile 14 steht.
   - Falsch: `pricing.py:99` obwohl du die Zeile nie gesehen hast.
   Erfundene Belege werden maschinell erkannt und der Nutzerin als unbelegt angezeigt.

2. Jede Beziehung, die dir ein Werkzeug liefert, trägt ein Feld `sicherheit`:
   - "verifiziert" oder "aufgelöst" → du darfst es als Tatsache formulieren.
   - "vermutet" → du musst hedgen. Schreibe "vermutlich", "wahrscheinlich" oder "einer von N Kandidaten". Formuliere es nie als feststehend.

3. Stößt du auf einen Knoten der Art `dynamic_gap`, sag klar, dass die statische Analyse dort endet und das Ziel erst zur Laufzeit feststeht. Rate nicht weiter.

4. Findest du etwas nicht, sag das. "Ich habe dafür im Projekt nichts gefunden" ist eine gute Antwort. Eine erfundene ist keine.

5. Zitiere Code wörtlich oder gar nicht. Bereinige nichts, kürze nichts stillschweigend.

Arbeitsweise:

- Der nachgeschlagene Ausschnitt wurde schon mit englischen Begriffen gesucht — deutsche Frage, englische Bezeichner. Suche nach `total`, nicht nach „Endbetrag"; nach `discount`, nicht nach „Rabatt".
- Rate nicht dreimal mit Synonymen. Findet eine Suche nichts, nimm `most_relevant` und sieh nach, wie die Dinge hier tatsächlich heißen.
- Beginne die Antwort nicht mit einer Wiederholung der Frage — das kostet Tokens, die dann in der Spur fehlen.

Antworte kurz — vier bis acht Sätze — und schließe mit einem Spur-Block ab:

SPUR:
pfad/datei.py:31 — was hier passiert, in einem Satz
pfad/andere.py:88 — was hier passiert, in einem Satz

Die Spur ist der Weg durch den Code, den man abgehen muss, um die Antwort selbst nachzuvollziehen. Zwei bis sechs Schritte, in der Reihenfolge, in der die Daten fließen. Lieber zwei belegte als sechs geratene — jeder Schritt ohne `datei:zeile` ist kein Schritt."""

#: Zusatz, sobald Paper-Evidenz im Prompt steht. Beide Belegarten müssen
#: unterscheidbar bleiben — eine Paper-Aussage mit einer Codezeile zu belegen
#: (oder umgekehrt) wäre genau die Verwechslung, die die Synthese wertlos macht.
PAPERS_PROMPT = """

Zusätzlich stehen dir Auszüge aus der lokalen Paper-Sammlung zur Verfügung. Dafür gilt:

6. Belege eine Aussage über den *Code* mit `pfad/datei.py:zeile`, eine Aussage aus einem *Paper* mit dessen ID in eckigen Klammern, z. B. `[arxiv:2401.01234]`. Nummerierte Verweise wie `[1]` sind keine Belege und werden als Fehler gewertet.

7. Sagst du, dass dieses Repository eine Methode aus einem Paper umsetzt, dann belege beide Seiten: die Stelle im Paper *und* die Stelle im Code. Ohne beides ist es eine Vermutung und muss auch so formuliert werden."""

ActivityHandler = Callable[[str], None]


@dataclass
class TrailStep:
    """Ein Schritt der Spur — eine Stelle im Code plus ein Satz Begründung."""

    path: str
    line: int
    reason: str
    node_id: str | None = None
    #: Falsch heißt: der Schritt zeigt auf Code, den das Modell nie gesehen hat.
    #: Er bleibt trotzdem stehen — ihn stillschweigend zu streichen, verstecke
    #: genau den Fehler, den er sichtbar macht.
    verified: bool = False


@dataclass
class PaperEvidence:
    """Ein Paper, das für diese Frage abgerufen wurde."""

    paper_id: str
    title: str = ""
    year: int | None = None
    snippets: list[str] = field(default_factory=list)


def describe_tool_call(name: str, arguments: str) -> str:
    """Eine Zeile, die sagt, was der Begleiter gerade tut.

    Nicht Zierrat: beim Zusehen entsteht das Vertrauen in die Antwort. Ein
    Spinner sagt „warte", diese Zeilen sagen „ich habe *hier* nachgesehen".
    """
    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        args = {}
    if not isinstance(args, dict):
        args = {}
    query = str(args.get("query") or "")
    path = str(args.get("path") or "")

    return {
        "search_symbols": f"suche Symbol „{query}“",
        "search_text": f"suche Text „{query}“",
        "get_node": "sehe mir ein Symbol an",
        "callers_of": "sehe nach, wer das aufruft",
        "callees_of": "sehe nach, was das aufruft",
        "path_between": "suche den Pfad dazwischen",
        "read_lines": f"lese {path}",
        "most_relevant": "sehe mir die wichtigsten Stellen an",
    }.get(name, f"rufe {name} auf")


def split_trail(text: str) -> tuple[str, str]:
    """Prosa und Spur-Block trennen."""
    index = text.find(TRAIL_MARKER)
    if index < 0:
        return text.strip(), ""
    return text[:index].rstrip(), text[index + len(TRAIL_MARKER) :].lstrip()


def parse_trail(block: str) -> list[TrailStep]:
    """``pfad:zeile — Begründung`` je Zeile. Port von ``cs_llm::parse_trail``.

    Ob ein Schritt hält, wird hier *nicht* entschieden — das macht später eine
    Beleg-Prüfung auf der Rust-Seite über denselben Block.
    """
    steps: list[TrailStep] = []
    for raw in (block or "").splitlines():
        line = raw.strip().lstrip("-*• ").strip()
        if not line:
            continue

        # Gedankenstrich und einfacher Bindestrich kommen beide vor.
        if "—" in line:
            position, _, reason = line.partition("—")
        elif " - " in line:
            position, _, reason = line.partition(" - ")
        else:
            position, reason = line, ""

        position = position.strip()
        if ":" not in position:
            continue
        path, _, line_text = position.rpartition(":")
        number = line_text.strip().split("-")[0].strip()
        if not path.strip() or not number.isdigit():
            continue
        parsed = int(number)
        if parsed <= 0:
            continue
        steps.append(TrailStep(path=path.strip(), line=parsed, reason=reason.strip()))
    return steps


def _utf16_offsets(text: str, start: int, end: int) -> tuple[int, int]:
    """Byte-Versätze der Rust-Seite in JavaScript-Zeichenversätze umrechnen.

    ``citation::verify`` gibt Byte-Versätze zurück, weil Rust so auf Strings
    zugreift. Das Frontend schneidet damit den Beleg-Chip aus dem Antworttext —
    und JavaScript zählt in UTF-16-Einheiten. Bei einer Antwort mit „Zeilennummer"
    oder „für" liefen die Chips sonst um jedes Umlaut-Byte nach links.
    """
    encoded = text.encode("utf-8")

    def convert(offset: int) -> int:
        prefix = encoded[: max(0, min(offset, len(encoded)))].decode("utf-8", "ignore")
        return len(prefix.encode("utf-16-le")) // 2

    return convert(start), convert(end)


def _paper_evidence(
    question: str,
    *,
    research_project_id: str | None,
    paper_ids: list[str] | None,
    limit: int,
    metadata_db_path: str,
) -> list[PaperEvidence]:
    """Paper-Auszüge zur Frage, über den vorhandenen Hybrid-Retriever.

    Fail-soft: ohne Embeddings, ohne Extraktionen oder mit gesperrter DuckDB gibt
    es hier eben keine Papers. Der Code-Teil der Antwort ist davon unabhängig und
    darf nicht mitfallen.
    """
    try:
        from query.hybrid_retriever import HybridRetriever
        from query.kg_retriever import KGRetriever

        retriever = HybridRetriever(kg_retriever=KGRetriever(metadata_db_path=metadata_db_path))
        hits = retriever.search(question, limit=limit, paper_ids=paper_ids)
    except Exception:  # noqa: BLE001 — Papers sind eine Zugabe, kein Muss
        return []

    evidence: list[PaperEvidence] = []
    for hit in hits:
        snippets: list[str] = []
        for item in sorted(hit.evidence, key=lambda entry: entry.score, reverse=True)[:3]:
            text = " ".join(str(item.text or "").split())
            if text:
                snippets.append(text[:600])
        evidence.append(
            PaperEvidence(
                paper_id=str(hit.source.paper_id),
                title=str(hit.source.title or ""),
                year=hit.source.year,
                snippets=snippets,
            )
        )
    del research_project_id  # nur zur Dokumentation des Aufrufers
    return evidence


def _papers_block(papers: list[PaperEvidence]) -> str:
    lines = ["Auszüge aus der lokalen Paper-Sammlung:", ""]
    for paper in papers:
        head = f"[{paper.paper_id}] {paper.title}".strip()
        if paper.year:
            head = f"{head} ({paper.year})"
        lines.append(head)
        for snippet in paper.snippets:
            lines.append(f"  - {snippet}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _cited_paper_ids(text: str, known: set[str]) -> list[str]:
    """Welche der abgerufenen Paper-IDs stehen wirklich in der Antwort?

    Bewusst nur exakte Treffer aus ``known``: eine ID, die nicht abgerufen wurde,
    ist erfunden — dieselbe Regel wie auf der Code-Seite, nur ohne Zeilennummern.
    """
    found: list[str] = []
    for paper_id in known:
        if f"[{paper_id}]" in text and paper_id not in found:
            found.append(paper_id)
    return found


def _bare_numeric_citations(text: str) -> int:
    """Zählt ``[1]``-artige Verweise. Die sind laut ``CLAUDE.md`` ein Fehler."""
    count = 0
    index = 0
    while True:
        index = text.find("[", index)
        if index < 0:
            return count
        close = text.find("]", index)
        if close < 0:
            return count
        inner = text[index + 1 : close].strip()
        if inner.isdigit():
            count += 1
        index = close + 1


def ask_stream(
    project: dict[str, Any],
    question: str,
    *,
    session: str = "default",
    provider: str | None = None,
    model: str | None = None,
    research_project_id: str | None = None,
    paper_ids: list[str] | None = None,
    use_papers: bool = False,
    paper_limit: int = 5,
    history: list[dict[str, str]] | None = None,
    broad: bool = False,
    max_symbols: int = 6,
    config_path: str = "config.yaml",
    metadata_db_path: str = "data/metadata.duckdb",
    router: LLMRouter | None = None,
) -> Iterator[dict[str, Any]]:
    """Eine Frage bis zur geprüften Antwort — als Strom von Ereignissen.

    Generator und nicht Rückgabewert, weil der Router daraus SSE macht und weil
    die Aktivitätszeilen ihren Wert verlieren, wenn sie erst zusammen mit der
    fertigen Antwort ankommen.

    ``history`` macht daraus einen Dialog. Ist sie gesetzt, wird die Sitzung
    **nicht** zurückgesetzt und die Vorab-Suche *erweitert* die Lizenz zum
    Zitieren, statt sie zu ersetzen — eine Rückfrage darf zitieren, was in
    Runde eins gezeigt wurde. Ohne ``history`` bleibt alles wie bisher: neue
    Frage, neue Lizenz.

    ``broad`` weitet die Vorab-Suche für Bereichsfragen („welche Funktionen
    gehören zu X"): Namens- *und* Volltextsuche, mehr Symbole. Für eine
    Punktfrage bleibt es aus — dort ist mehr Kontext nur mehr Prompt.
    """
    question = (question or "").strip()
    if not question:
        yield {"event": "failed", "error": "Keine Frage angegeben"}
        return
    history = history or []

    def rpc(method: str, params: dict[str, Any] | None = None) -> Any:
        return service.query(project, method, {**(params or {}), "session": session}, config_path=config_path)

    try:
        router = router or LLMRouter.from_config_file(config_path)
    except Exception as error:  # noqa: BLE001 — muss als Ereignis ankommen
        yield {"event": "failed", "error": f"LLM nicht konfiguriert: {error}"}
        return

    provider_name = provider or router.default_provider
    model_name = model or router.provider_default_model(provider_name)

    try:
        # Eine neue Frage ist eine neue Sitzung: die alte Lizenz zum Zitieren
        # darf nicht auf die neue Antwort durchschlagen. Im laufenden Gespräch
        # gilt das Gegenteil — dort *hat* das Modell die früheren Zeilen gesehen.
        if not history:
            rpc("session_reset")
        yield {"event": "activity", "text": "schlage im Graphen nach"}
        retrieval = (
            rpc(
                "context_build",
                {
                    "question": question,
                    "broad": broad,
                    "max_symbols": max_symbols,
                    "extend": bool(history),
                },
            )
            or {}
        )
        specs = service.query(project, "tool_specs", {}, config_path=config_path) or []
    except CodeSearchMissingError as error:
        yield {"event": "failed", "error": str(error), "kind": "binary_missing"}
        return
    except CodeGraphError as error:
        yield {"event": "failed", "error": f"Code-Graph: {error}"}
        return

    context = str(retrieval.get("context") or "")
    matched_by_name = bool(retrieval.get("matched_by_name"))
    symbol_count = int(retrieval.get("symbols") or 0)

    # Die Trefferliste wird über den ganzen Lauf gesammelt. Jede Herkunft wird
    # notiert, weil sie unterschiedlich viel wert ist: „zitiert" heisst, die
    # Stelle steht belegt in der Antwort; „vorab-suche" heisst nur, dass sie im
    # Kontext lag. Beides ununterschieden zu zeigen wäre eine Behauptung.
    focus: dict[str, str] = {}

    def note(node_id: Any, why: str) -> None:
        key = str(node_id or "").strip()
        if not key:
            return
        if key not in focus or _FOCUS_RANK[why] < _FOCUS_RANK[focus[key]]:
            focus[key] = why

    for node_id in retrieval.get("symbol_ids") or []:
        note(node_id, "vorab-suche")

    papers: list[PaperEvidence] = []
    if use_papers:
        yield {"event": "activity", "text": "sehe in den Papers nach"}
        papers = _paper_evidence(
            question,
            research_project_id=research_project_id,
            paper_ids=paper_ids,
            limit=paper_limit,
            metadata_db_path=metadata_db_path,
        )

    system_prompt = SYSTEM_PROMPT + (PAPERS_PROMPT if papers else "")
    user_parts: list[str] = []
    if context:
        user_parts.append(context)
    if papers:
        user_parts.append(_papers_block(papers))
    user_parts.append(f"Frage: {question}")
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    # Der Verlauf steht gekürzt davor: Frage und Antworttext, keine
    # Werkzeugergebnisse. Die kämen sonst bei jeder Runde erneut durch die
    # Vorverarbeitung — genau der Grund, aus dem MAX_ROUNDS auf 3 steht.
    for entry in history[-HISTORY_TURNS:]:
        messages.append({"role": "user", "content": str(entry.get("question") or "")})
        messages.append(
            {"role": "assistant", "content": str(entry.get("answer") or "")[:HISTORY_CHARS]}
        )
    messages.append({"role": "user", "content": "\n\n---\n\n".join(user_parts)})

    # Werkzeuge nur, wenn die Vorab-Suche dünn zurückkam.
    #
    # Das ist eine Messung, keine Vorliebe: bekommt ein kleines lokales Modell
    # vollständigen Kontext *und* Werkzeuge, schlägt es trotzdem nach — neun
    # Aufrufe für eine Frage, vier davon Wiederholungen, zu Minuten pro Stück.
    offer_tools = (not matched_by_name) or symbol_count == 0
    rounds = MAX_ROUNDS if offer_tools else 1

    tool_calls_made = 0
    truncated = True
    final_text = ""
    fallback = False
    overrides = model_overrides(router, provider_name, model_name)

    try:
        for _round in range(rounds):
            content, calls = router.chat_with_tools(
                messages,
                specs if offer_tools else None,
                provider=provider_name,
                overrides=overrides,
            )
            fallback = fallback or bool(router.last_response_metadata.get("tool_calling_fallback"))
            if not calls:
                final_text = content or ""
                truncated = False
                break

            messages.append(
                {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [
                        {"id": call.id, "type": "function", "function": {"name": call.name, "arguments": call.arguments}}
                        for call in calls
                    ],
                }
            )
            for call in calls:
                tool_calls_made += 1
                yield {"event": "activity", "text": describe_tool_call(call.name, call.arguments)}
                result = _dispatch(rpc, call)
                for node_id in _nodes_from_tool(call.name, call.arguments, result):
                    note(node_id, "nachgeschlagen")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": result,
                    }
                )

        if not final_text.strip() and truncated and offer_tools:
            # Die Schleife ist ausgelaufen. Einmal ohne Werkzeuge nachfragen,
            # damit das Gesammelte noch zu einer Antwort wird statt zu nichts.
            yield {"event": "activity", "text": "fasse zusammen"}
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Beantworte die Frage jetzt mit dem, was du gefunden hast. Keine weiteren "
                        "Werkzeugaufrufe. Sag klar, was offen geblieben ist."
                    ),
                }
            )
            content, _calls = router.chat_with_tools(
                messages, None, provider=provider_name, overrides=overrides
            )
            final_text = content or ""
    except CodeGraphError as error:
        yield {"event": "failed", "error": f"Code-Graph: {error}"}
        return
    except Exception as error:  # noqa: BLE001 — Anbieterfehler gehören in den Strom
        yield {"event": "failed", "error": str(error)}
        return

    if not final_text.strip():
        # Reasoning-Modelle (deepseek-r1, o3) verbrauchen ihr ganzes Token-Budget
        # im Denken und liefern dann keinen Antworttext. Das ist ein anderer
        # Fall als „keine Antwort" und muss auch so heissen — sonst sucht man am
        # falschen Ende (Modell nicht geladen? Netz?).
        if router.last_response_metadata.get("reasoning_truncated"):
            yield {
                "event": "failed",
                "error": (
                    "Das Modell hat sein Token-Budget im Denken verbraucht und keine "
                    "Antwort formuliert. Mehr max_tokens in den Modelleinstellungen "
                    "oder Denken abschalten (chat_template_kwargs.enable_thinking: false)."
                ),
            }
            return
        yield {"event": "failed", "error": "Das Modell hat keine Antwort geliefert"}
        return

    yield {"event": "activity", "text": "prüfe die Belege"}
    prose, trail_block = split_trail(final_text)

    try:
        verified = rpc("verify_citations", {"text": prose}) or {}
        trail = _verify_trail(rpc, parse_trail(trail_block))
    except CodeGraphError as error:
        yield {"event": "failed", "error": f"Beleg-Prüfung fehlgeschlagen: {error}"}
        return

    text = str(verified.get("text") or prose)
    citations = []
    for citation in verified.get("citations") or []:
        start, end = _utf16_offsets(text, int(citation.get("start") or 0), int(citation.get("end") or 0))
        citations.append({**citation, "start": start, "end": end})

    # Die stärkste Herkunft zuletzt eintragen, damit sie gewinnt: eine Stelle,
    # die belegt in der Antwort steht, gehört sicherer zum Feature als eine, die
    # nur im Kontext lag.
    for step in trail:
        if step.node_id:
            note(step.node_id, "spur")
    for citation in citations:
        if citation.get("status") != "verified":
            continue
        try:
            hit = rpc(
                "symbol_at",
                {"path": citation.get("path"), "line": int(citation.get("from_line") or 1)},
            )
        except CodeGraphError:
            continue
        note((hit or {}).get("node_id"), "zitiert")

    focus_nodes = _hydrate_focus(rpc, focus)

    known_paper_ids = {paper.paper_id for paper in papers}
    paper_citations = _cited_paper_ids(final_text, known_paper_ids)
    bare_citations = _bare_numeric_citations(final_text)

    answer = {
        "question": question,
        "answer": text,
        "trail_text": trail_block,
        "citations": citations,
        "trail": [step.__dict__ for step in trail],
        "verdict": verified.get("verdict"),
        "verdict_label": verified.get("verdict_label"),
        "is_clean": bool(verified.get("is_clean")),
        "quote_mismatches": verified.get("quote_mismatches") or [],
        "uncited_sentences": int(verified.get("uncited_sentences") or 0),
        "tool_calls": tool_calls_made,
        "truncated": truncated,
        "tool_calling_fallback": fallback,
        "provider": provider_name,
        "model": model_name,
        "papers": [paper.__dict__ for paper in papers],
        "paper_citations": paper_citations,
        # Alle Symbole, die zu dieser Frage gehören — mit `why` je Eintrag.
        "focus_nodes": focus_nodes,
        # Ein nacktes [1] ist laut CLAUDE.md ein Qualitätsfehler und wird gemeldet,
        # nicht stillschweigend hingenommen.
        "bare_citations": bare_citations,
    }
    yield {"event": "done", "answer": answer}


#: Herkunft eines Treffers, stärkste zuerst. Die Reihenfolge ist die Aussage:
#: „zitiert" heisst, die Stelle steht belegt in der Antwort; „vorab-suche"
#: heisst nur, dass sie im Kontext lag und das Modell sie vielleicht nie gelesen
#: hat. Beides gleich anzuzeigen wäre der Fehler, gegen den der Rest gebaut ist.
_FOCUS_RANK = {"zitiert": 0, "spur": 1, "nachgeschlagen": 2, "vorab-suche": 3}

#: Wie viele frühere Züge in den Prompt gehen. Drei reichen für „und was macht
#: die zweite Funktion davon", und jeder weitere wird bei jeder Runde erneut
#: verarbeitet.
HISTORY_TURNS = 3
#: Frühere Antworten gekürzt. Der Wortlaut von vor vier Fragen ist selten die
#: Information — der Gegenstand ist es.
HISTORY_CHARS = 700

#: Obergrenze der Trefferliste. Darüber ist sie keine Liste mehr, die man
#: abarbeitet, sondern ein zweiter Suchindex.
MAX_FOCUS_NODES = 24


def _nodes_from_tool(name: str, arguments: Any, result: str) -> list[str]:
    """Knoten-IDs, die ein Werkzeugaufruf berührt hat.

    Weisse Liste statt „alles, was nach einer ID aussieht": ein Ergebnis enthält
    Zeilennummern, Hashes und Pfade, und ein blindes Einsammeln setzte Symbole
    auf die Trefferliste, die niemand nachgeschlagen hat.
    """
    found: list[str] = []
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (TypeError, ValueError):
            arguments = {}
    if isinstance(arguments, dict):
        for key in ("id", "from", "to"):
            value = arguments.get(key)
            if isinstance(value, str) and value.strip():
                found.append(value.strip())

    # Suchergebnisse: die gefundenen Symbole selbst zählen, nicht der Suchbegriff.
    if name in {"search_symbols", "most_relevant", "callers_of", "callees_of"}:
        try:
            payload = json.loads(result)
        except (TypeError, ValueError):
            return found
        items = payload if isinstance(payload, list) else payload.get("treffer") or []
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                found.append(item["id"])
    return found


def _hydrate_focus(rpc: Callable[..., Any], focus: dict[str, str]) -> list[dict[str, Any]]:
    """Aus IDs anzeigbare Zeilen machen, stärkste Herkunft zuerst."""
    ordered = sorted(
        focus.items(), key=lambda pair: (_FOCUS_RANK.get(pair[1], 9), pair[0])
    )[:MAX_FOCUS_NODES]

    out: list[dict[str, Any]] = []
    for node_id, why in ordered:
        try:
            detail = rpc("node", {"id": node_id})
        except CodeGraphError:
            continue
        if not isinstance(detail, dict):
            continue
        out.append(
            {
                "id": detail.get("id") or node_id,
                "name": detail.get("name"),
                "qualified": detail.get("qualified"),
                "kind": detail.get("kind"),
                "path": detail.get("path"),
                "line": (detail.get("span") or {}).get("start_line"),
                "relevance": (detail.get("metrics") or {}).get("relevance") or 0.0,
                "why": why,
            }
        )
    return out


def _dispatch(rpc: Callable[..., Any], call: ToolCall) -> str:
    """Einen Werkzeugaufruf an die Rust-Seite geben und als Text zurückreichen.

    Ein Fehler wird zum Werkzeug-*Ergebnis*, nicht zum Abbruch: das Modell soll
    „das gibt es nicht" lesen und weiterarbeiten können, statt dass eine
    Fehlsuche die ganze Frage beendet.
    """
    try:
        result = rpc("tool_call", {"name": call.name, "arguments": call.arguments})
    except CodeGraphError as error:
        return json.dumps({"error": str(error)}, ensure_ascii=False)
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False)


def _verify_trail(rpc: Callable[..., Any], steps: list[TrailStep]) -> list[TrailStep]:
    """Jeden Spur-Schritt derselben Prüfung unterziehen wie ein Zitat.

    Ein Schritt, der auf nie abgerufenen Code zeigt, ist eine Vermutung im
    Gewand eines Wegs — und genau deshalb wird er markiert statt entfernt.
    """
    if not steps:
        return []

    block = "\n".join(f"{step.path}:{step.line}" for step in steps)
    checked: dict[tuple[str, int], bool] = {}
    try:
        verified = rpc("verify_citations", {"text": block}) or {}
    except CodeGraphError:
        verified = {}
    for citation in verified.get("citations") or []:
        key = (str(citation.get("path") or ""), int(citation.get("from_line") or 0))
        checked[key] = str(citation.get("status")) == "verified"

    for step in steps:
        step.verified = checked.get((step.path, step.line), False)
        try:
            found = rpc("symbol_at", {"path": step.path, "line": step.line}) or {}
            step.node_id = found.get("node_id")
        except CodeGraphError:
            step.node_id = None
    return steps


def ask(project: dict[str, Any], question: str, **kwargs: Any) -> dict[str, Any]:
    """Wie :func:`ask_stream`, aber nur die fertige Antwort.

    Für Aufrufer ohne Oberfläche — Tests, der Desktop-Begleiter, die
    Analyse-Werkstatt.
    """
    activity: list[str] = []
    on_activity: ActivityHandler | None = kwargs.pop("on_activity", None)
    for event in ask_stream(project, question, **kwargs):
        if event.get("event") == "activity":
            activity.append(str(event.get("text") or ""))
            if on_activity is not None:
                on_activity(str(event.get("text") or ""))
        elif event.get("event") == "failed":
            raise CodeGraphError(str(event.get("error") or "Unbekannter Fehler"))
        elif event.get("event") == "done":
            return {**(event.get("answer") or {}), "activity": activity}
    raise CodeGraphError("Der Begleiter hat keine Antwort geliefert")
