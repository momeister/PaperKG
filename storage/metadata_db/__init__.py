"""DuckDB metadata storage, composed from domain mixins.

Split out of the former single-file ``storage/metadata_db.py``. Public API is
unchanged: ``from storage.metadata_db import MetadataDB``.
"""
from __future__ import annotations

from storage.metadata_db.base import MetadataDBBase
from storage.metadata_db.schema import SchemaMixin
from storage.metadata_db.papers import PapersMixin
from storage.metadata_db.extraction import ExtractionMixin
from storage.metadata_db.batch import BatchMixin
from storage.metadata_db.embeddings import EmbeddingsMixin
from storage.metadata_db.notes import NotesMixin
from storage.metadata_db.grey_sources import GreySourcesMixin
from storage.metadata_db.sessions import SessionsMixin
from storage.metadata_db.code_projects import CodeProjectsMixin
from storage.metadata_db.analysis import AnalysisMixin
from storage.metadata_db.datasets import DatasetsMixin
from storage.metadata_db.pdf_annotations import PdfAnnotationsMixin
from storage.metadata_db.benchmark import BenchmarkMixin
from storage.metadata_db.companion import CompanionMixin
from storage.metadata_db.project_scope import ProjectScopeMixin


class MetadataDB(SchemaMixin, PapersMixin, ExtractionMixin, BatchMixin, EmbeddingsMixin, NotesMixin, GreySourcesMixin, SessionsMixin, CodeProjectsMixin, AnalysisMixin, DatasetsMixin, PdfAnnotationsMixin, BenchmarkMixin, CompanionMixin, ProjectScopeMixin, MetadataDBBase):
    """Full DuckDB metadata store (see domain mixins for grouped methods)."""


__all__ = ["MetadataDB"]
