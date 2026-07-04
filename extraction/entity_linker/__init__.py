"""Entity linking package.

Split out of the former single-file ``extraction/entity_linker.py``. The public
API is unchanged: import the same names from ``extraction.entity_linker``.
"""
from __future__ import annotations

from extraction.entity_linker.strategies import (
    ConceptLinkageStrategy,
    OpenAlexLinkageStrategy,
    _coerce_float,
)
from extraction.entity_linker.linker import EntityLinker
from extraction.entity_linker.relation_extractor import ControlledRelationExtractor
from extraction.entity_linker.pipeline import ExtractionPipeline

__all__ = [
    "ConceptLinkageStrategy",
    "OpenAlexLinkageStrategy",
    "EntityLinker",
    "ControlledRelationExtractor",
    "ExtractionPipeline",
    # Re-exported for backward compatibility with the former single-file module.
    "_coerce_float",
]
