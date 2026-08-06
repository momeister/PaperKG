"""``datei:zeile`` in Terminal-Ausgabe finden.

Portierung von ``cs_pty::find_positions`` — PaperKG hat sein eigenes Terminal
(``src-tauri/src/terminal.rs``), aber nicht die Erkennung. Das ist die Naht
zwischen Kommandozeile und Graph: ein fehlgeschlagener Test oder ein Stacktrace
wird zu etwas, das man anklicken kann, statt zu etwas, das man abtippt.

Bewusst ohne reguläre Ausdrücke, wie das Original. Eine Regex, die
``crates/cs-core/src/lib.rs:120:9`` fängt, fängt auch ``14:30`` und
``Verhältnis 3:1``; die Ablehnungen sind hier der eigentliche Inhalt und stehen
darum als Bedingungen da, wo man sie liest.
"""

from __future__ import annotations

from typing import NamedTuple

#: Zeichen, die zu einem Pfad-Token gehören dürfen. Alles andere wird an den
#: Rändern abgeschnitten (Klammern, Kommas, Anführungszeichen, Pfeile).
_TOKEN_CHARS = set("/._:-")


class Position(NamedTuple):
    path: str
    line: int


def _is_token_char(char: str) -> bool:
    return char.isalnum() or char in _TOKEN_CHARS


def _trim(token: str) -> str:
    """Nicht-Pfad-Zeichen an beiden Rändern abschneiden (`-->`, `(`, `,`, `"`)."""
    start, end = 0, len(token)
    while start < end and not _is_token_char(token[start]):
        start += 1
    while end > start and not _is_token_char(token[end - 1]):
        end -= 1
    return token[start:end]


def find_positions(line: str) -> list[Position]:
    """Alle plausiblen ``pfad:zeile``-Stellen einer Ausgabezeile, ohne Duplikate."""
    found: list[Position] = []

    def add(path: str, number: int) -> None:
        position = Position(path, number)
        if position not in found:
            found.append(position)

    # Python-Traceback: File "src/app.py", line 42, in handler
    head, sep, rest = line.partition('File "')
    del head
    if sep:
        path, closing, tail = rest.partition('"')
        if closing and path:
            _, marker, number = tail.partition("line ")
            if marker:
                digits = ""
                for char in number:
                    if not char.isdigit():
                        break
                    digits += char
                if digits and int(digits) > 0:
                    add(path, int(digits))

    # Allgemein: pfad:zeile oder pfad:zeile:spalte, irgendwo in der Zeile.
    for raw in line.split():
        token = _trim(raw)
        parts = token.split(":")
        if len(parts) < 2:
            continue
        path, number = parts[0], parts[1]
        # Ein Pfad hat eine Endung. Ohne diese Bedingung wird aus "14:30 Uhr"
        # eine Datei namens "14" und aus "Verhältnis 3:1" eine namens "3".
        if not path or "." not in path or path.endswith("."):
            continue
        if not number.isdigit():
            continue
        parsed = int(number)
        if parsed <= 0:
            continue
        add(path, parsed)

    return found


def find_positions_in_text(text: str, *, limit: int = 8) -> list[Position]:
    """Über mehrere Zeilen, gedeckelt.

    Der Deckel steht bei acht wie in CodeSearchs Terminal-Schublade: mehr
    Sprungmarken, als in eine Leiste passen, sind keine Hilfe mehr, sondern eine
    zweite Ausgabe, durch die man sich lesen muss.
    """
    found: list[Position] = []
    for line in (text or "").splitlines():
        for position in find_positions(line):
            if position not in found:
                found.append(position)
                if len(found) >= limit:
                    return found
    return found
