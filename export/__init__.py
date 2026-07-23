"""Export a Tiefenanalyse synthesis to a thesis-/paper-style LaTeX PDF (or .tex/.zip).

Public API:
    build_export(...)   -> ExportResult   (orchestrator)
    ExportOptions, ExportResult           (request options / return type)
"""
from __future__ import annotations

from export.builder import ExportOptions, ExportResult, aggregate_sources, build_export

__all__ = ["build_export", "aggregate_sources", "ExportOptions", "ExportResult"]
