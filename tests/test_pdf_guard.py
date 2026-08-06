"""Tests für parsing/pdf_guard.py — die Schutzhülle ums PDF-Parsen.

Hintergrund: eine einzelne PDF-Seite mit riesiger Vektor-Grafik lässt pdfplumber
*und* pypdf unbegrenzt Speicher allokieren, ohne je fertig zu werden. Ungeschützt
zieht das den Backend-Prozess per OOM mit; nach aussen sah das so aus, als sei die
Extraktion kaputt (die batch_jobs-Zeile fehlte, jeder Poll bekam 404).
"""

from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path

import pytest

from parsing import pdf_guard
from parsing.marker_parser import MarkerParser


def _minimal_pdf(text: str = "Hallo Wissenschaft") -> bytes:
    """Kleinstmögliches gültiges 1-Seiten-PDF mit etwas Text."""
    stream = f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    return bytes(out)


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "sample.pdf"
    path.write_bytes(_minimal_pdf())
    return path


def test_guarded_parse_matches_direct_parse(sample_pdf: Path) -> None:
    """Der Wächter darf das Ergebnis nicht verändern — nur schützen."""
    direct = MarkerParser().parse_direct(str(sample_pdf), "paper-1")
    guarded = MarkerParser().parse(str(sample_pdf), "paper-1")

    assert guarded.text == direct.text
    assert guarded.page_count == direct.page_count
    assert guarded.paper_id == "paper-1"
    assert not guarded.meta.get("guard_aborted")


def test_guard_disabled_falls_back_to_direct(
    sample_pdf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCIENCEKG_PDF_GUARD", "0")
    monkeypatch.setattr(pdf_guard, "_config_cache", None)

    result = MarkerParser().parse(str(sample_pdf), "paper-2")

    assert result.text == MarkerParser().parse_direct(str(sample_pdf), "paper-2").text


def test_child_flag_prevents_recursive_spawn(
    sample_pdf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Im Kindprozess darf `parse` nicht erneut einen Kindprozess starten."""
    monkeypatch.setenv(pdf_guard._CHILD_ENV_FLAG, "1")

    def _explode(*args, **kwargs):  # pragma: no cover - darf nie laufen
        raise AssertionError("Kindprozess hat einen weiteren Kindprozess gestartet")

    monkeypatch.setattr(pdf_guard, "_spawn_child", _explode)

    assert MarkerParser().parse(str(sample_pdf), "paper-3").text


def test_pages_from_progress_last_entry_per_page_wins(tmp_path: Path) -> None:
    """Faellt pdfplumber aus und pypdf parst dieselben Seiten neu, zaehlt der neuere Text."""
    progress = tmp_path / "pages.jsonl"
    progress.write_text(
        "\n".join(
            [
                json.dumps({"page": 0, "text": "pdfplumber-0"}),
                json.dumps({"page": 1, "text": "pdfplumber-1"}),
                json.dumps({"page": 0, "text": "pypdf-0"}),
                json.dumps({"page": 1, "text": "pypdf-1"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert pdf_guard._pages_from_progress(str(progress)) == ["pypdf-0", "pypdf-1"]


def test_pages_from_progress_survives_truncated_line(tmp_path: Path) -> None:
    """Wird das Kind mitten im Schreiben gekillt, bleibt die letzte Zeile halb — der Rest zaehlt trotzdem."""
    progress = tmp_path / "pages.jsonl"
    progress.write_text(
        json.dumps({"page": 0, "text": "vollstaendig"}) + "\n" + '{"page": 1, "te',
        encoding="utf-8",
    )

    assert pdf_guard._pages_from_progress(str(progress)) == ["vollstaendig"]


def test_pages_from_progress_missing_file(tmp_path: Path) -> None:
    assert pdf_guard._pages_from_progress(str(tmp_path / "nope.jsonl")) == []


def test_memory_limit_scales_with_free_ram(monkeypatch: pytest.MonkeyPatch) -> None:
    """Das Budget richtet sich nach freiem RAM, bleibt aber in den konfigurierten Grenzen."""
    monkeypatch.setattr(pdf_guard, "_config_cache", None)
    monkeypatch.setattr(pdf_guard, "available_memory_bytes", lambda: 20 * 1024**3)
    generous = pdf_guard.memory_limit_bytes()

    monkeypatch.setattr(pdf_guard, "_config_cache", None)
    monkeypatch.setattr(pdf_guard, "available_memory_bytes", lambda: 2 * 1024**3)
    modest = pdf_guard.memory_limit_bytes()

    assert modest < generous
    config = pdf_guard._load_config()
    assert modest >= config["memory_min_mb"] * 1024 * 1024
    assert generous <= config["memory_max_mb"] * 1024 * 1024


def test_memory_limit_falls_back_when_ram_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pdf_guard, "_config_cache", None)
    monkeypatch.setattr(pdf_guard, "available_memory_bytes", lambda: None)

    assert (
        pdf_guard.memory_limit_bytes()
        == int(pdf_guard._load_config()["memory_min_mb"]) * 1024 * 1024
    )


def test_env_overrides_beat_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCIENCEKG_PDF_TIMEOUT", "900")
    monkeypatch.setattr(pdf_guard, "_config_cache", None)

    assert pdf_guard._load_config()["pdf_timeout_seconds"] == 900.0


def test_partial_result_when_child_is_killed(
    sample_pdf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wird das Kind abgeschossen, kommen die bereits fertigen Seiten zurueck."""
    monkeypatch.setattr(pdf_guard, "_config_cache", None)

    real_spawn = pdf_guard._spawn_child

    def _slow_child(file_path, paper_id, progress_path, result_path, memory_cap):
        # Kind, das zwei Seiten meldet und dann haengt — wie die Vektor-Grafik-Seite.
        import subprocess
        import sys

        script = textwrap.dedent(
            f"""
            import json, time
            with open({progress_path!r}, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({{"page": 0, "text": "Seite eins"}}) + "\\n")
                fh.write(json.dumps({{"page": 1, "text": "Seite zwei"}}) + "\\n")
                fh.flush()
            time.sleep(300)
            """
        )
        return subprocess.Popen([sys.executable, "-c", script], stderr=subprocess.PIPE)

    monkeypatch.setattr(pdf_guard, "_spawn_child", _slow_child)
    monkeypatch.setenv("SCIENCEKG_PDF_TIMEOUT", "60")
    monkeypatch.setattr(pdf_guard, "_config_cache", None)
    config = pdf_guard._load_config()
    config["pdf_timeout_seconds"] = 2.0
    config["poll_interval_seconds"] = 0.1

    result = MarkerParser().parse(str(sample_pdf), "paper-4")

    assert result.meta["guard_aborted"] is True
    assert result.meta["guard_pages_recovered"] == 2
    assert "Seite eins" in result.text
    assert "Seite zwei" in result.text
    assert pdf_guard._spawn_child is not real_spawn


def test_child_module_is_importable_standalone() -> None:
    """`python -m parsing.pdf_child` muss ohne das __main__ des Elternprozesses laufen."""
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "parsing.pdf_child"],
        capture_output=True,
        cwd=str(Path(__file__).resolve().parent.parent),
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)},
    )

    assert proc.returncode == 2
    assert b"usage:" in proc.stderr
