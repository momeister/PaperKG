from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import BinaryIO


class FileManager:
    """
    Manages local PDF storage with versioning support.
    """

    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def safe_storage_id(
        paper_id: str,
        display_name: str | None = None,
        source: str | None = None,
    ) -> str:
        """
        Build a readable filesystem-safe identifier.
        """
        clean_parts = []
        for index, part in enumerate([source, display_name, paper_id]):
            text = str(part or "").strip()
            if not text:
                continue
            colon_replacement = " " if index == 1 else "_"
            text = text.replace("/", "_").replace("\\", "_").replace(":", colon_replacement)
            text = re.sub(r"\s+", "-", text)
            text = re.sub(r"[^A-Za-z0-9._-]+", "-", text)
            text = re.sub(r"-{2,}", "-", text).strip("._-")
            if text:
                clean_parts.append(text)
        raw = "__".join(clean_parts)
        return (raw[:160].strip("._-") or "document").lower()

    def get_storage_path(
        self,
        paper_id: str,
        version: int | None = None,
        display_name: str | None = None,
        source: str | None = None,
    ) -> Path:
        """
        Return the local filesystem path for a paper.
        Format: base_dir/{source}/{paper_id}/{paper_id}_v{version}.pdf
        """
        if version is None:
            version = 1
        safe_id = self.safe_storage_id(paper_id, display_name=display_name, source=source)
        path = self.base_dir / safe_id / f"{safe_id}_v{version}.pdf"
        return path

    def save_pdf(
        self,
        paper_id: str,
        content: bytes,
        version: int | None = None,
        display_name: str | None = None,
        source: str | None = None,
    ) -> Path:
        """
        Save PDF content to disk.
        """
        path = self.get_storage_path(paper_id, version, display_name=display_name, source=source)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def load_pdf(self, paper_id: str, version: int | None = None) -> bytes | None:
        """
        Load PDF content from disk. Return None if file doesn't exist.
        """
        path = self.get_storage_path(paper_id, version)
        if not path.exists():
            return None
        return path.read_bytes()

    def exists(self, paper_id: str, version: int | None = None) -> bool:
        """
        Check if a paper PDF exists on disk.
        """
        path = self.get_storage_path(paper_id, version)
        return path.exists()

    def delete(self, paper_id: str, version: int | None = None) -> bool:
        """
        Delete a paper PDF. Return True if deleted, False if not found.
        """
        path = self.get_storage_path(paper_id, version)
        if path.exists():
            path.unlink()
            # Clean up empty directories
            try:
                path.parent.rmdir()
            except OSError:
                pass
            return True
        return False

    def list_papers(self) -> list[tuple[str, int]]:
        """
        List all stored papers as (paper_id, version) tuples.
        """
        papers = []
        for paper_dir in self.base_dir.iterdir():
            if paper_dir.is_dir():
                for pdf_file in paper_dir.glob("*.pdf"):
                    # Extract version from filename: {safe_id}_v{version}.pdf
                    parts = pdf_file.stem.rsplit("_v", 1)
                    if len(parts) == 2:
                        paper_id = paper_dir.name
                        try:
                            version = int(parts[1])
                            papers.append((paper_id, version))
                        except ValueError:
                            pass
        return papers

    def get_size_bytes(self, paper_id: str, version: int | None = None) -> int:
        """
        Get file size in bytes. Return 0 if not found.
        """
        path = self.get_storage_path(paper_id, version)
        if path.exists():
            return path.stat().st_size
        return 0
