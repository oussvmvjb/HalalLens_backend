"""
Robust ingredient extraction pipeline for HalalLens.

Handles multi-language OCR text (EN / FR / AR), nested parentheses,
compound ingredients, OCR digit/letter confusions and common noise.

Also handles recipe images where ingredient lines start with quantities
(e.g. "1 kg ground chicken", "1 tsp salt").

SAFETY CONTRACT
---------------
* Never silently change a potentially haram ingredient name to a safe-looking
  one without very high confidence.
* Never discard a token that starts with 'E' + digits (E-number additive).
* Ambiguous OCR corrections are flagged with needs_review=True.
"""

from __future__ import annotations

import logging
import re
import unicodedata

from app.schemas.ocr import OCRIngredientSuggestion, OCRTextRegion
from app.services.halal_engine.normalizer import _split_ingredient_text  # noqa: PLC2701

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ingredient section header detection
# ---------------------------------------------------------------------------

_INGREDIENTS_HEADER = re.compile(
    r"^\s*(?:ingredients?|ingrédients?|ingrédient|composition|配料表|配料|成分|成份|"
    r"المكونات|مكونات|مكوّنات)"
    r"\s*(?:[:：]\s*)?(.*)$",
    re.IGNORECASE,
)

# Signals that we have left the ingredient section
_SECTION_END = re.compile(
    r"(?:^|[\s,;:，。；：！？])(?:warnings?|cautions?|directions?|instructions?|storage|recycling|"
    r"product description|description(?: of the product)?|certifications?|certified|"
    r"avertissements?|précautions?|mode d'emploi|conseils d'utilisation|"
    r"description(?: du produit)?|certification|certificat|conservation|recyclage|"
    r"تحذيرات|تحذير|طريقة الاستعمال|طريقة الاستخدام|التخزين|إعادة التدوير|"
    r"وصف المنتج|شهادة|警告|注意事项|使用方法|保存方法|产品说明|产品介绍|回收信息|认证信息)\b",
    re.IGNORECASE,
)

# Lines that look like a recipe instruction (verb-first imperative sentences)
_RECIPE_LINE = re.compile(
    r"^\s*(?:preheat|prechauffez|heat|chauffer|mix|mélanger|stir|fold|bake|cuire|"
    r"cook|add|ajouter|combine|refrigerate|chill|serve|pour|place|spread|"
    r"preparation|préparation|method|méthode|how to|comment)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Quantity / recipe-line stripping for recipe images
# ---------------------------------------------------------------------------

# Measurement units (singular + plural, common abbreviations)
_UNITS_PATTERN = (
    r"kg|g|gram(?:me)?s?|kilogram(?:me)?s?|lb|lbs?|pound(?:s)?|oz|ounce(?:s)?"
    r"|ml|l|liter(?:s)?|litre(?:s)?|cl|dl"
    r"|tsp|tbsp|teaspoon(?:s)?|tablespoon(?:s)?|cup(?:s)?|pinch(?:es)?"
    r"|stick(?:s)?|bunch(?:es)?|handful(?:s)?|slice(?:s)?|piece(?:s)?|pack(?:age)?(?:s)?"
    r"|inch(?:es)?|cm|mm"
)

# Leading quantity: optional number (int/fraction/decimal/range) followed by optional unit
# Matches: "1 kg", "1/2 tsp", "180 ml", "2-3", "3 ", etc.
_LEADING_QUANTITY_RE = re.compile(
    r"^\s*(?:\d+(?:[.,/]\d+)?(?:\s*[-–]\s*\d+(?:[.,/]\d+)?)?\s*(?:"
    + _UNITS_PATTERN
    + r")?\s+)+",
    re.IGNORECASE,
)

# Trailing preparation descriptor suffixes — "diced small", "cut in half", etc.
# IMPORTANT: "cold" is only stripped when standalone (not part of "ice-cold water")
_TRAILING_DESC_RE = re.compile(
    r"\s*,?\s*(?:"
    r"diced(?:\s+\w+)?|chopped(?:\s+\w+)?|minced|sliced(?:\s+\w+)?|grated|shredded|crushed|"
    r"toasted|roasted|peeled|pitted|deveined|deseeded|squeezed|zested|melted|"
    r"softened|cut\s+\w+|at\s+room\s+temperature|small|large|medium|fine|coarse|"
    r"thin|thick|frozen|thawed|raw|"
    r"washed|rinsed|drained|patted\s+dry|packed"
    r")\s*$",
    re.IGNORECASE,
)

# Ice-cold prefix — "ice-cold water" → strip "ice-cold " prefix to get "water"
_ICE_COLD_PREFIX_RE = re.compile(r"^\s*ice[-\s]cold\s+", re.IGNORECASE)

# Preparation prefixes to strip when they appear before the ingredient name
_PREP_PREFIX_RE = re.compile(
    r"^\s*(?:freshly\s+|finely\s+|roughly\s+|coarsely\s+|lightly\s+|thinly\s+|"
    r"well[\s-]+)?(?:chopped|diced|minced|sliced|grated|shredded|crushed|"
    r"toasted|roasted|peeled|pitted|deveined|deseeded|squeezed|zested|melted|"
    r"softened)\s+",
    re.IGNORECASE,
)

# Tokens that are purely section headings, pure quantities, or standalone units.
# Standalone quantities: a number (possibly with fraction/range) optionally followed
# by a unit, with nothing else after.  Examples: "180 ml", "1 tsp", "1 kg", "8".
_PURE_QUANTITY_RE = re.compile(
    r"^\s*\d+(?:[.,/]\d+)?(?:\s*[-–]\s*\d+(?:[.,/]\d+)?)?\s*(?:"
    + _UNITS_PATTERN
    + r")?\s*$",
    re.IGNORECASE,
)

# Pure preparation-description tokens (standalone, not attached to an ingredient)
_PURE_PREP_DESC_RE = re.compile(
    r"^\s*(?:diced(?:\s+\w+)?|chopped(?:\s+\w+)?|minced|sliced(?:\s+\w+)?|grated|"
    r"shredded|crushed|toasted|roasted|peeled|pitted|deveined|deseeded|squeezed|"
    r"zested|melted|softened|cut\s+\w+|at\s+room\s+temperature|packed|drained|"
    r"rinsed|washed|thawed|frozen|raw|patted\s+dry)\s*(?:\w+)?\s*$",
    re.IGNORECASE,
)

# Section / structural headings
_SECTION_HEADING_RE = re.compile(
    r"^\s*(?:ingredients?|ingrédients?|composition|directions?|instructions?|"
    r"preparation|method|méthode|servings?|serves?|yield|makes?)\s*$",
    re.IGNORECASE,
)

# Recipe title heuristic: 2+ Title-Case words, no leading digit, no comma
# AND the last word is a known protein/dish noun => treat as title, not ingredient
_RECIPE_TITLE_PROTEIN_RE = re.compile(
    r"^\s*(?!.*\d)(?:[A-Z][a-zA-Z]+ ){2,}[A-Z][a-zA-Z]+\s*$"
)
_PROTEIN_NOUNS = frozenset({
    "sausage", "sausages", "burger", "burgers", "steak", "steaks", "roast",
    "meatball", "meatballs", "hotdog", "hotdogs", "nuggets", "nugget",
    "pizza", "pasta", "cake", "bread", "soup", "salad", "sauce", "loaf",
})


def _strip_quantity_and_prep(token: str) -> str:
    """
    Strip leading quantity + unit and trailing preparation descriptors from a
    recipe ingredient line, returning just the core ingredient name.

    Examples
    --------
    "1 kg ground chicken"              → "ground chicken"
    "1 tsp salt"                       → "salt"
    "1 tbsp chopped parsley"           → "parsley"
    "180 ml ice-cold water"            → "water"
    "150 g cheddar cheese, diced small"→ "cheddar cheese"
    "8 Sausages"                       → "Sausages"  (kept, filtered separately)
    """
    # Strip leading quantity + unit
    stripped = _LEADING_QUANTITY_RE.sub("", token).strip()
    if not stripped:
        return token.strip()

    # Strip "ice-cold" prefix before the ingredient name (e.g. "ice-cold water" → "water")
    candidate = _ICE_COLD_PREFIX_RE.sub("", stripped).strip()
    if candidate:
        stripped = candidate

    # Strip trailing preparation descriptions (", diced small", ", chopped", etc.)
    candidate = _TRAILING_DESC_RE.sub("", stripped).strip().rstrip(",").strip()
    if candidate:
        stripped = candidate

    # Strip preparation prefixes (chopped, diced…) only when a word remains after
    candidate = _PREP_PREFIX_RE.sub("", stripped).strip()
    if candidate:
        stripped = candidate

    return stripped


def _is_non_ingredient_token(token: str) -> bool:
    """
    Return True if the token should be discarded as a non-ingredient entry:
    pure section heading, standalone quantity/unit, preparation description,
    or recipe title/name.
    """
    t = token.strip()
    if not t:
        return True
    # Section headings (e.g. "Ingredients", "Method")
    if _SECTION_HEADING_RE.match(t):
        return True
    # Pure quantity or unit token (e.g. "180 ml", "1 tsp", "1 kg", "8")
    if _PURE_QUANTITY_RE.match(t):
        return True
    # Pure preparation description without an ingredient (e.g. "diced small")
    if _PURE_PREP_DESC_RE.match(t):
        return True
    # Heuristic: 3+ Title-Case words whose last word is a protein/dish noun
    # e.g. "Spicy Chicken Sausage", "Cheese Chicken Sausage"
    if _RECIPE_TITLE_PROTEIN_RE.match(t):
        words = t.split()
        if len(words) >= 3 and words[-1].lower() in _PROTEIN_NOUNS:
            return True
    return False


# Nutritional-table noise lines
_NUTRITIONAL_LINE = re.compile(
    r"^\s*(?:nutrition|valeurs?\s+nutritives?|valeurs?\s+énergétiques?|"
    r"energy|calories?|kcal|kj|fat|carbohydrate|protein|sodium|fibre|"
    r"per\s+\d|pour\s+\d|\d+\s*(?:g|mg|ml|kcal|kj)|\d+\s*%)",
    re.IGNORECASE,
)

# Marketing / packaging noise
_MARKETING_LINE = re.compile(
    r"^\s*(?:new!?|nouveau!?|recyclable|best before|à consommer avant|"
    r"keep refrigerated|conserver|shake well|agiter|suitable for|"
    r"contains?\s+no\b|free from\b|allergen|may contain|peut contenir|"
    r"manufactured\s+in|produit\s+en|made\s+in|fabriqué\s+en|"
    r"www\.|http[s]?://|\d{4,})",
    re.IGNORECASE,
)

# Strip leading/trailing noise from individual items
_STRIP_ITEM_PUNCT = " \t\r\n:：;；,，、.。！？،•|-*"

# ---------------------------------------------------------------------------
# OCR digit/letter correction – safety-first
# ---------------------------------------------------------------------------

# Fix E-number suffixes: E47l → E471, E12O → E120
_ENUM_FIX = re.compile(r"\b(E\d{2,3})[lLoO]\b", re.IGNORECASE)
# Fix common word-interior OCR confusions (only in known safe food words)
_SAFE_OCR_CORRECTIONS: list[tuple[re.Pattern[str], str]] = [
    # palm 0il → palm oil  (zero in 'oil' word context)
    (re.compile(r"\bpalm\s+0il\b", re.IGNORECASE), "palm oil"),
    # 0live → olive
    (re.compile(r"\b0live\b", re.IGNORECASE), "olive"),
    # ge1atin → gelatin
    (re.compile(r"\bge[1l]atin\b", re.IGNORECASE), "gelatin"),
    # sug4r / sug@r → sugar
    (re.compile(r"\bsug[4@]r\b", re.IGNORECASE), "sugar"),
    # sa1t → salt
    (re.compile(r"\bsa[1l]t\b", re.IGNORECASE), "salt"),
    # wat3r → water
    (re.compile(r"\bwat[3e]r\b", re.IGNORECASE), "water"),
    # yea5t → yeast
    (re.compile(r"\byea[5s]t\b", re.IGNORECASE), "yeast"),
    # mono-and diglycerides (missing space)
    (re.compile(r"\bmono-and\b", re.IGNORECASE), "mono- and"),
    # e.g. "0il" standalone (digit-zero confusion in short words)
    (re.compile(r"\b0il\b", re.IGNORECASE), "oil"),
]


def correct_ocr_text(text: str) -> str:
    """Apply domain-aware OCR corrections. Never changes E-numbers to non-numbers."""
    # E-number trailing letter fix first (before any generic substitution)
    text = _ENUM_FIX.sub(lambda m: m.group(1) + "1", text)
    for pattern, replacement in _SAFE_OCR_CORRECTIONS:
        text = pattern.sub(replacement, text)
    # Normalize exotic whitespace
    text = re.sub(r"[\u00a0\u2000-\u200a\u202f\u3000]", " ", text)
    return text


# ---------------------------------------------------------------------------
# Line-level noise filters
# ---------------------------------------------------------------------------

def _is_noise_line(line: str) -> bool:
    """Return True if the line is very likely not an ingredient entry."""
    stripped = line.strip()
    if not stripped:
        return True
    if _RECIPE_LINE.match(stripped):
        return True
    if _NUTRITIONAL_LINE.match(stripped):
        return True
    if _MARKETING_LINE.match(stripped):
        return True
    return False


# ---------------------------------------------------------------------------
# Fallback: full-text extraction when no header found
# ---------------------------------------------------------------------------

def _extract_ingredient_block_from_full_text(full_text: str) -> str:
    """
    When no 'Ingredients:' header is found, try to extract an ingredient block
    by stripping nutritional/recipe/marketing noise from the full text.

    Returns the cleaned text (may still need splitting) or the full text
    as a last resort.
    """
    lines = full_text.splitlines()
    candidate_lines: list[str] = []
    for line in lines:
        if _SECTION_END.search(line):
            break
        if not _is_noise_line(line):
            candidate_lines.append(line)
    return "\n".join(candidate_lines).strip() or full_text


# ---------------------------------------------------------------------------
# Sub-ingredient extraction from parenthetical groups
# ---------------------------------------------------------------------------

def _extract_sub_ingredients(
    token: str,
    ocr_confidence: float,
) -> list[OCRIngredientSuggestion]:
    """
    If *token* looks like a compound ingredient with sub-ingredients in
    parentheses/brackets, return suggestions for both the parent and each
    sub-ingredient.  Otherwise return [token] wrapped as a suggestion.

    Example:
        "Chocolate coating (Sugar, Cocoa butter, Gelatin)"
        → parent: "Chocolate coating"
        → children: "Sugar", "Cocoa butter", "Gelatin"
    """
    paren_re = re.compile(r"\(([^()]*)\)|\[([^\[\]]*)\]")
    match = paren_re.search(token)
    if match is None:
        return [_make_suggestion(token, ocr_confidence)]

    parent_name = token[: match.start()].strip(_STRIP_ITEM_PUNCT)
    sub_text = match.group(1) or match.group(2) or ""

    results: list[OCRIngredientSuggestion] = []
    if parent_name:
        results.append(_make_suggestion(parent_name, ocr_confidence))
    for sub_raw in _split_ingredient_text(sub_text):
        sub = sub_raw.strip(_STRIP_ITEM_PUNCT)
        if sub:
            results.append(_make_suggestion(sub, ocr_confidence))
    return results if results else [_make_suggestion(token, ocr_confidence)]


def _make_suggestion(
    text: str,
    ocr_confidence: float,
    *,
    ocr_corrected: bool = False,
) -> OCRIngredientSuggestion:
    """Create an OCRIngredientSuggestion with sane defaults."""
    text = text.strip(_STRIP_ITEM_PUNCT)
    if not text:
        return OCRIngredientSuggestion(
            original_text="(empty)",
            normalized_text=None,
            confidence=0.0,
            normalization_confidence=0.0,
            needs_review=True,
        )
    return OCRIngredientSuggestion(
        original_text=text,
        # normalized_text is left as None here; the halal engine normalizes
        normalized_text=text,
        confidence=ocr_confidence,
        normalization_confidence=1.0 if not ocr_corrected else 0.75,
        needs_review=(ocr_corrected or ocr_confidence < 0.75),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_ingredients(
    regions: list[OCRTextRegion],
    *,
    full_text: str | None = None,
) -> tuple[list[OCRIngredientSuggestion], list[str]]:
    """
    Extract ingredient suggestions from OCR text regions.

    Strategy (in priority order):
    1. Try to find an 'Ingredients:' (or localised) header and extract
       the following text as the ingredient block.
    2. If no header is found, strip noise from the full OCR text and use
       the remainder as the ingredient block.
    3. If the result is still empty, return the raw full text so the halal
       engine can attempt classification.

    Sub-ingredients inside parentheses are flattened into individual
    suggestions so the halal engine can classify each one.
    """
    warnings: list[str] = []

    # Build full text from regions if not provided
    if full_text is None:
        full_text = "\n".join(region.text for region in regions)

    full_text_corrected = correct_ocr_text(full_text)

    # --- Step 1: header-based extraction ---
    section_lines: list[tuple[str, float]] = []
    section_started = False

    for region in regions:
        corrected_line = correct_ocr_text(region.text)
        if not section_started:
            header_match = _INGREDIENTS_HEADER.match(corrected_line)
            if header_match is None:
                continue
            section_started = True
            line = header_match.group(1)
        else:
            line = corrected_line

        if _SECTION_END.search(line):
            line = line[: (_SECTION_END.search(line).start())].strip(_STRIP_ITEM_PUNCT)  # type: ignore[union-attr]
            if line and not _is_noise_line(line):
                section_lines.append((line, region.confidence))
            break

        if not _is_noise_line(line):
            section_lines.append((line, region.confidence))

    # --- Step 2: fallback – strip noise from full text ---
    if not section_started:
        warnings.append(
            "No ingredient section header found; using full OCR text with noise filtering."
        )
        fallback_block = _extract_ingredient_block_from_full_text(full_text_corrected)
        if fallback_block:
            # Use an average confidence of 0.75 when no per-region data
            avg_conf = (
                sum(r.confidence for r in regions) / len(regions)
                if regions
                else 0.75
            )
            section_lines = [(fallback_block, avg_conf)]

    # --- Step 3: last resort – raw full text ---
    if not section_lines and full_text_corrected.strip():
        warnings.append(
            "Could not filter ingredient text; using raw OCR text for halal analysis."
        )
        avg_conf = (
            sum(r.confidence for r in regions) / len(regions) if regions else 0.75
        )
        section_lines = [(full_text_corrected.strip(), avg_conf)]

    if not section_lines:
        return [], ["No readable text found in image."]

    # --- Tokenise, strip quantities, and de-duplicate ---
    suggestions: list[OCRIngredientSuggestion] = []
    seen: set[str] = set()

    for block_text, confidence in section_lines:
        for raw_token in _split_ingredient_text(block_text):
            token = raw_token.strip(_STRIP_ITEM_PUNCT)
            if not token or _SECTION_END.search(token) or _is_noise_line(token):
                continue

            # --- Recipe-image quantity stripping ---
            # Strip leading quantities/units and trailing preparation descriptors
            # from each token so that "1 kg ground chicken" → "ground chicken"
            # and "1 tsp salt" → "salt" before halal classification.
            cleaned_token = _strip_quantity_and_prep(token)

            # Discard tokens that are purely quantities, section headings,
            # or recipe titles (e.g. "Spicy Chicken Sausage", "Ingredients").
            if _is_non_ingredient_token(cleaned_token):
                logger.debug("Discarding non-ingredient token %r → %r", token, cleaned_token)
                continue

            # Flatten compound ingredients (e.g. "Coating (sugar, cocoa)")
            for suggestion in _extract_sub_ingredients(cleaned_token, confidence):
                key = unicodedata.normalize("NFKC", suggestion.original_text).casefold().strip()
                if key and key not in seen:
                    seen.add(key)
                    suggestions.append(suggestion)

    if not suggestions:
        return [], ["Ingredient section found but no entries could be extracted."]

    return suggestions, warnings
