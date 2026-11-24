import re
from difflib import SequenceMatcher

from app.schemas.ocr import OCRIngredientSuggestion, OCRTextRegion

_INGREDIENT_DICTIONARY = (
    "AQUA",
    "POTASSIUM OLIVATE",
    "GLYCERIN",
    "CITRUS NOBILIS PEEL OIL EXPRESSED",
    "CITRUS SINENSIS PEEL OIL EXPRESSED",
    "SODIUM CHLORIDE",
    "GERANIOL",
    "LIMONENE",
    "LINALOOL",
    "水",
    "甘油",
    "氯化钠",
    "香叶醇",
    "柠檬烯",
    "芳樟醇",
)
_INGREDIENTS_HEADER = re.compile(
    r"^\s*(?:ingredients?|ingrédients?|composition|配料表|配料|成分|成份|"
    r"المكونات|مكونات|مكوّنات)"
    r"\s*(?:[:：]\s*)?(.*)$",
    re.IGNORECASE,
)
_SECTION_END = re.compile(
    r"(?:^|[\s,;:，。；：！？])(?:warnings?|cautions?|directions?|instructions?|storage|recycling|"
    r"product description|description(?: of the product)?|certifications?|certified|"
    r"avertissements?|précautions?|mode d'emploi|conseils d'utilisation|"
    r"description(?: du produit)?|certification|certificat|conservation|recyclage|"
    r"تحذيرات|تحذير|طريقة الاستعمال|طريقة الاستخدام|التخزين|إعادة التدوير|"
    r"وصف المنتج|شهادة|警告|注意事项|使用方法|保存方法|产品说明|产品介绍|回收信息|认证信息)\b",
    re.IGNORECASE,
)
_ITEM_SEPARATOR = re.compile(r"[,，、،;；。|•\n]+")
_STRIP_ITEM_PUNCTUATION = " \t\r\n:：;；,，、.。！？،•|-"


def extract_ingredients(
    regions: list[OCRTextRegion],
) -> tuple[list[OCRIngredientSuggestion], list[str]]:
    section_lines: list[tuple[str, float]] = []
    section_started = False

    for region in regions:
        if not section_started:
            header = _INGREDIENTS_HEADER.match(region.text)
            if header is None:
                continue
            section_started = True
            line = header.group(1)
        else:
            line = region.text

        section_end = _SECTION_END.search(line)
        if section_end is not None:
            line = line[: section_end.start()].strip(_STRIP_ITEM_PUNCTUATION)
            if line:
                section_lines.append((line, region.confidence))
            break
        section_lines.append((line, region.confidence))

    if not section_started:
        return [], ["Ingredient section could not be identified confidently."]

    ingredients: list[OCRIngredientSuggestion] = []
    for line, confidence in section_lines:
        for raw_item in _ITEM_SEPARATOR.split(line):
            item = raw_item.strip(_STRIP_ITEM_PUNCTUATION)
            if not item:
                continue
            if _SECTION_END.search(item):
                break
            ingredients.append(_normalize_ingredient(item, confidence))

    if not ingredients:
        return [], ["Ingredient section was found, but no entries could be extracted."]

    warnings = []
    if any(ingredient.normalized_text is None for ingredient in ingredients):
        warnings.append(
            "Some ingredient entries did not match the local ingredient dictionary."
        )
    return ingredients, warnings


def _normalize_ingredient(
    original_text: str,
    confidence: float,
) -> OCRIngredientSuggestion:
    original_key = _ingredient_key(original_text)
    matches = sorted(
        (
            (SequenceMatcher(None, original_key, _ingredient_key(name)).ratio(), name)
            for name in _INGREDIENT_DICTIONARY
        ),
        reverse=True,
    )
    best_score, best_name = matches[0]
    ambiguous = len(matches) > 1 and best_score - matches[1][0] < 0.06

    if best_score < 0.78 or ambiguous:
        return OCRIngredientSuggestion(
            original_text=original_text,
            normalized_text=None,
            confidence=confidence,
            normalization_confidence=0.0,
            needs_review=True,
        )

    changed = original_key != _ingredient_key(best_name)
    return OCRIngredientSuggestion(
        original_text=original_text,
        normalized_text=best_name,
        confidence=confidence,
        normalization_confidence=best_score,
        needs_review=changed or confidence < 0.85,
    )


def _ingredient_key(text: str) -> str:
    return "".join(character for character in text.casefold() if character.isalnum())
