from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class VocabularyEntry:
    """Single normalized vocabulary entry."""

    canonical_label: str
    aliases: list[str] = field(default_factory=list)
    openalx_id: str | None = None
    domain: str | None = None
    confidence: float = 1.0
    custom_metadata: dict[str, Any] = field(default_factory=dict)


class VocabularyManager:
    """
    Manages custom entity normalization and deduplication.
    Maps extracted concept labels to canonical forms using configured rules.
    """

    def __init__(self, entries: dict[str, VocabularyEntry] | None = None) -> None:
        """
        Initialize vocabulary.

        Args:
            entries: Pre-populated dict of {canonical_label -> VocabularyEntry}
        """
        self.entries = entries or {}
        self._label_to_canonical = {}
        self._update_mappings()

    def _update_mappings(self) -> None:
        """Rebuild canonical label mappings from entries."""
        self._label_to_canonical.clear()

        for canonical, entry in self.entries.items():
            self._label_to_canonical[canonical.lower()] = canonical

            for alias in entry.aliases:
                self._label_to_canonical[alias.lower()] = canonical

    def register(
        self,
        canonical_label: str,
        aliases: list[str] | None = None,
        openalx_id: str | None = None,
        domain: str | None = None,
        custom_metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Register new vocabulary entry.

        Args:
            canonical_label: Preferred form
            aliases: Alternative spellings/forms
            openalx_id: Link to OpenAlex Concept
            domain: Primary domain of application
            custom_metadata: Additional metadata
        """
        self.entries[canonical_label] = VocabularyEntry(
            canonical_label=canonical_label,
            aliases=aliases or [],
            openalx_id=openalx_id,
            domain=domain,
            custom_metadata=custom_metadata or {},
        )
        self._update_mappings()

    def normalize(self, label: str) -> str | None:
        """
        Normalize label to canonical form if known.

        Args:
            label: Input label to normalize

        Returns:
            Canonical form if found, else None
        """
        return self._label_to_canonical.get(label.lower())

    def get_entry(self, canonical_label: str) -> VocabularyEntry | None:
        """Get full entry for canonical label."""
        return self.entries.get(canonical_label)

    def suggest_canonical(self, label: str) -> str | None:
        """
        Suggest canonical form for label using fuzzy matching.
        Currently returns exact match only; can be extended with edit distance.

        Args:
            label: Input label

        Returns:
            Best canonical match or None
        """
        return self.normalize(label)

    def merge_entries(
        self,
        source_canonical: str,
        target_canonical: str,
    ) -> bool:
        """
        Merge source entry into target (deduplicate).

        Args:
            source_canonical: Entry to merge from
            target_canonical: Entry to merge into

        Returns:
            True if merge successful
        """
        source = self.entries.get(source_canonical)
        target = self.entries.get(target_canonical)

        if not source or not target:
            return False

        # Move source aliases to target
        target.aliases.extend(source.aliases)
        target.aliases.append(source_canonical)

        # Remove source entry
        del self.entries[source_canonical]
        self._update_mappings()

        return True

    def to_dict(self) -> dict[str, Any]:
        """Export vocabulary as serializable dict."""
        return {
            canonical: {
                "aliases": entry.aliases,
                "openalx_id": entry.openalx_id,
                "domain": entry.domain,
                "confidence": entry.confidence,
                "custom_metadata": entry.custom_metadata,
            }
            for canonical, entry in self.entries.items()
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VocabularyManager:
        """Create vocabulary from dict."""
        entries = {}

        for canonical, entry_data in data.items():
            entries[canonical] = VocabularyEntry(
                canonical_label=canonical,
                aliases=entry_data.get("aliases", []),
                openalx_id=entry_data.get("openalx_id"),
                domain=entry_data.get("domain"),
                confidence=entry_data.get("confidence", 1.0),
                custom_metadata=entry_data.get("custom_metadata", {}),
            )

        return cls(entries)
