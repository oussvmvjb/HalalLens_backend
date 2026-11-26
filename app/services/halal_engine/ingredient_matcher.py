"""
Ingredient matcher with exact, alias, translation, E/INS number, and fuzzy tiers.

SAFETY CONTRACT
---------------
* Fuzzy matching is never used to override a haram classification — it is only
  applied when the exact/alias/translation tiers all miss.
* Fuzzy threshold is set conservatively (≥ 0.82) and only for tokens longer
  than 4 characters to avoid false positives on short abbreviations.
* match_type is always reported so callers can apply appropriate confidence
  adjustments.
"""

from __future__ import annotations

import logging
from difflib import SequenceMatcher

from app.services.halal_engine.models import IngredientRecord, NormalizedIngredient
from app.services.halal_engine.normalizer import normalize_token

logger = logging.getLogger(__name__)

_FUZZY_THRESHOLD = 0.82
_FUZZY_MIN_TOKEN_LEN = 4


def _fuzzy_ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


class IngredientMatcher:
    """Matches normalized ingredients against database-backed IngredientRecord entries."""

    def __init__(self, records: list[IngredientRecord]) -> None:
        self._by_normalized: dict[str, IngredientRecord] = {}
        self._by_e_number: dict[str, IngredientRecord] = {}
        self._by_ins_number: dict[str, IngredientRecord] = {}
        self._by_alias: dict[str, IngredientRecord] = {}
        self._by_translation: dict[str, IngredientRecord] = {}

        for record in records:
            norm_key = normalize_token(record.normalized_name or record.canonical_name)
            if norm_key:
                self._by_normalized.setdefault(norm_key, record)
            canon_key = normalize_token(record.canonical_name)
            if canon_key:
                self._by_normalized.setdefault(canon_key, record)

            if record.e_number:
                self._by_e_number.setdefault(record.e_number.strip().upper(), record)
            if record.ins_number:
                self._by_ins_number.setdefault(record.ins_number.strip().upper(), record)

            for alias in record.aliases:
                alias_key = normalize_token(alias)
                if alias_key:
                    self._by_alias.setdefault(alias_key, record)

            for translated in record.multilingual_names:
                trans_key = normalize_token(translated)
                if trans_key:
                    self._by_translation.setdefault(trans_key, record)

    def match(
        self, normalized: NormalizedIngredient
    ) -> tuple[IngredientRecord | None, str | None]:
        """Return (matched_record, match_type) or (None, None) when unmatched.

        Tiers (in order):
          1. E-number exact match
          2. INS-number exact match
          3. Normalized-name exact match
          4. Alias exact match
          5. Translation exact match
          6. Fuzzy match (conservative, long tokens only, never haram-only decisions)
        """
        # Tier 1-5: exact/deterministic
        if normalized.e_number and normalized.e_number in self._by_e_number:
            return self._by_e_number[normalized.e_number], "e_number"
        if normalized.ins_number and normalized.ins_number in self._by_ins_number:
            return self._by_ins_number[normalized.ins_number], "ins_number"
        if normalized.normalized_name in self._by_normalized:
            return self._by_normalized[normalized.normalized_name], "exact"
        if normalized.normalized_name in self._by_alias:
            return self._by_alias[normalized.normalized_name], "alias"
        if normalized.normalized_name in self._by_translation:
            return self._by_translation[normalized.normalized_name], "translation"

        # Tier 6: conservative fuzzy (skip short tokens to avoid false positives)
        query = normalized.normalized_name
        if len(query) >= _FUZZY_MIN_TOKEN_LEN:
            best_ratio = 0.0
            best_record: IngredientRecord | None = None
            # Build a combined candidate pool from all indexes
            candidates: dict[str, IngredientRecord] = {}
            candidates.update(self._by_normalized)
            candidates.update(self._by_alias)
            candidates.update(self._by_translation)
            for candidate_key, record in candidates.items():
                if len(candidate_key) < _FUZZY_MIN_TOKEN_LEN:
                    continue
                ratio = _fuzzy_ratio(query, candidate_key)
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_record = record

            if best_ratio >= _FUZZY_THRESHOLD and best_record is not None:
                logger.debug(
                    "Fuzzy match: %r → %r (ratio=%.3f)",
                    query,
                    best_record.canonical_name,
                    best_ratio,
                )
                return best_record, "fuzzy"

        return None, None
