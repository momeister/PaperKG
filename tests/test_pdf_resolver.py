from __future__ import annotations

from pathlib import Path

from quality.pdf_resolver import BenchmarkPdfResolver


def test_pdf_resolver_prefers_existing_local_pdf(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper-p1.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    resolver = BenchmarkPdfResolver(pdf_base_dir=str(tmp_path))
    result = resolver.resolve(paper_id="paper-p1", title="Local Fixture", download_missing=False)

    assert result.pdf_path == str(pdf_path)
    assert result.provenance["source"] == "local"
    assert result.warnings == []


def test_pdf_resolver_downloads_mocked_open_pdf_with_provenance(monkeypatch, tmp_path: Path) -> None:
    resolver = BenchmarkPdfResolver(pdf_base_dir=str(tmp_path))

    monkeypatch.setattr(
        resolver,
        "_candidate_urls",
        lambda **kwargs: (
            [
                {
                    "source": "arxiv",
                    "pdf_url": "https://arxiv.org/pdf/2501.00001.pdf",
                    "landing_url": "https://arxiv.org/abs/2501.00001",
                    "license": "CC-BY",
                }
            ],
            [],
        ),
    )
    monkeypatch.setattr(resolver, "_download_pdf", lambda url: (b"%PDF-1.4\nfixture", "application/pdf"))
    monkeypatch.setattr(resolver, "_throttle", lambda source: None)

    result = resolver.resolve(paper_id="arxiv:2501.00001", title="Open Fixture", download_missing=True)

    assert result.pdf_path is not None
    assert Path(result.pdf_path).exists()
    assert result.provenance["source"] == "arxiv"
    assert result.provenance["landing_url"] == "https://arxiv.org/abs/2501.00001"
    assert result.provenance["license"] == "CC-BY"
    assert len(result.provenance["sha256"]) == 64
