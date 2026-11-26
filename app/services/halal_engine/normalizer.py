import re
import unicodedata

from app.services.halal_engine.models import NormalizedIngredient

_E_NUMBER_RE = re.compile(r"\bE[\s\-]?(\d{3,4}[a-z]?)\b", re.IGNORECASE)
_INS_NUMBER_RE = re.compile(r"\bINS[\s\-]?(\d{3,4}[a-z]?)\b", re.IGNORECASE)
_PERCENTAGE_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*%")
_WHITESPACE_RE = re.compile(r"\s+")
_APOSTROPHE_RE = re.compile(r"[’‘`´]")
_EDGE_PUNCT_RE = re.compile(r"^[\s\-:：;；,，、.。！？•|*()\[\]]+|[\s\-:：;；,，、.。！？•|*()\[\]]+$")

_ALWAYS_SPLIT_CHARS = frozenset({";", "；", "\n"})
_COMMA_SPLIT_CHARS = frozenset({",", "，", "、", "،"})


def _strip_accents(text: str) -> str:
    """Strip combining diacritical marks (French accents, Arabic tashkeel) safely."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def normalize_token(text: str) -> str:
    """Normalize an ingredient string for deterministic FR/EN/AR matching."""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = _APOSTROPHE_RE.sub("'", normalized)
    normalized = _strip_accents(normalized)
    normalized = unicodedata.normalize("NFKC", normalized).casefold()
    normalized = _PERCENTAGE_RE.sub(" ", normalized)
    normalized = _EDGE_PUNCT_RE.sub("", normalized)
    normalized = _WHITESPACE_RE.sub(" ", normalized).strip()
    return normalized


def extract_e_number(text: str) -> str | None:
    match = _E_NUMBER_RE.search(unicodedata.normalize("NFKC", text))
    if match is None:
        return None
    return f"E{match.group(1).upper()}"


def extract_ins_number(text: str) -> str | None:
    match = _INS_NUMBER_RE.search(unicodedata.normalize("NFKC", text))
    if match is None:
        return None
    return f"INS{match.group(1).upper()}"


def _extract_and_strip_parentheticals(text: str) -> tuple[str, list[str]]:
    """Remove top-level parenthetical/bracketed groups (supporting nesting) and return qualifiers."""
    outside_chars: list[str] = []
    qualifiers: list[str] = []
    current_qual: list[str] = []
    depth = 0

    for ch in text:
        if ch in "([":
            if depth == 0:
                current_qual = []
            else:
                current_qual.append(ch)
            depth += 1
        elif ch in ")]":
            if depth > 0:
                depth -= 1
                if depth == 0:
                    q = "".join(current_qual).strip()
                    if q:
                        qualifiers.append(q)
                    outside_chars.append(" ")
                else:
                    current_qual.append(ch)
            else:
                outside_chars.append(ch)
        else:
            if depth > 0:
                current_qual.append(ch)
            else:
                outside_chars.append(ch)

    if depth > 0 and current_qual:
        q = "".join(current_qual).strip()
        if q:
            qualifiers.append(q)

    return "".join(outside_chars), qualifiers


def _split_ingredient_text(text: str) -> list[str]:
    """Split ingredient text while preserving parentheses, nested groups, and decimal commas."""
    items: list[str] = []
    current: list[str] = []
    depth = 0
    length = len(text)

    for idx, ch in enumerate(text):
        if ch in "([":
            depth += 1
            current.append(ch)
        elif ch in ")]":
            if depth > 0:
                depth -= 1
            current.append(ch)
        elif depth == 0 and ch in _ALWAYS_SPLIT_CHARS:
            token = "".join(current).strip()
            if token:
                items.append(token)
            current = []
        elif depth == 0 and ch in _COMMA_SPLIT_CHARS:
            prev_ch = text[idx - 1] if idx > 0 else ""
            next_ch = text[idx + 1] if idx + 1 < length else ""
            # Preserve European/French decimal commas such as "2,2%"
            if ch == "," and prev_ch.isdigit() and next_ch.isdigit():
                current.append(ch)
            else:
                token = "".join(current).strip()
                if token:
                    items.append(token)
                current = []
        else:
            current.append(ch)

    trailing = "".join(current).strip()
    if trailing:
        items.append(trailing)
    return items


def normalize_ingredient(raw_text: str) -> NormalizedIngredient | None:
    """Normalize a single ingredient entry and extract E/INS numbers and qualifiers."""
    cleaned_raw = unicodedata.normalize("NFKC", raw_text).strip()
    cleaned_raw = re.sub(r"^[\s\-:：;；,，、.。！？•|*]+|[\s\-:：;；,，、.。！？•|*]+$", "", cleaned_raw).strip()
    if not cleaned_raw:
        return None

    e_number = extract_e_number(cleaned_raw)
    ins_number = extract_ins_number(cleaned_raw)

    without_parens, qualifiers = _extract_and_strip_parentheticals(cleaned_raw)
    source_qualifier = "; ".join(qualifiers) if qualifiers else None

    normalized_name = normalize_token(without_parens)
    if not normalized_name:
        normalized_name = normalize_token(cleaned_raw)

    if not normalized_name:
        return None

    return NormalizedIngredient(
        original_text=cleaned_raw,
        normalized_name=normalized_name,
        e_number=e_number,
        ins_number=ins_number,
        source_qualifier=source_qualifier,
    )


def split_and_normalize_ingredients(
    *,
    ingredients_text: str | None = None,
    ingredients_list: list[str] | None = None,
) -> list[NormalizedIngredient]:
    """Split raw ingredients text and/or list into deduplicated normalized entries."""
    raw_items: list[str] = []
    if ingredients_list:
        raw_items.extend(item for item in ingredients_list if isinstance(item, str))
    if ingredients_text:
        raw_items.extend(_split_ingredient_text(ingredients_text))

    results: list[NormalizedIngredient] = []
    seen: set[str] = set()
    for raw in raw_items:
        item = normalize_ingredient(raw)
        if item is None:
            continue
        dedupe_key = f"{item.normalized_name}|{item.e_number or ''}|{item.ins_number or ''}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        results.append(item)
    return results
