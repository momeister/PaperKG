from __future__ import annotations

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

    def get_storage_path(self, paper_id: str, version: int | None = None) -> Path:
        """
        Return the local filesystem path for a paper.
        Format: base_dir/{source}/{paper_id}/{paper_id}_v{version}.pdf
        """
        if version is None:
            version = 1
        # Normalize paper_id for filesystem
        safe_id = paper_id.replace("/", "_").replace(":", "_")
        path = self.base_dir / safe_id / f"{safe_id}_v{version}.pdf"
        return path

    def save_pdf(self, paper_id: str, content: bytes, version: int | None = None) -> Path:
        """
        Save PDF content to disk.
        """
        path = self.get_storage_path(paper_id, version)
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
