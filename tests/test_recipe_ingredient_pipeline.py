"""
Recipe-image ingredient pipeline tests.

Covers the specific chicken sausage recipe scenario where OCR extracts
quantities, recipe titles, section headings, and preparation descriptions
that must be stripped before halal analysis.

Expected behaviour
------------------
Recognized (actual ingredients):
  ground chicken, salt, black pepper, parsley, dried oregano,
  dried thyme, paprika, chili powder, cayenne pepper,
  garlic powder, cheddar cheese, mozzarella cheese, water

Must NOT appear in the extracted ingredient list:
  "8 Sausages", "1 kg", "1 tsp", "1 tbsp", "180 ml",
  "Ingredients", "Spicy Chicken Sausage", "Cheese Chicken Sausage",
  "diced small"
"""

from __future__ import annotations

import pytest

from app.schemas.ocr import OCRTextRegion
from app.services.ingredient_processing import (
    _is_non_ingredient_token,
    _strip_quantity_and_prep,
    extract_ingredients,
)
from app.services.halal_engine.normalizer import normalize_token
from app.services.halal_engine.fallback import evaluate_controlled_fallback
from app.services.halal_engine.models import NormalizedIngredient
from app.models.enums import IngredientHalalStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _regions(*texts: str, confidence: float = 0.90) -> list[OCRTextRegion]:
    return [OCRTextRegion(text=t, confidence=confidence, bounding_box=[]) for t in texts]


# ---------------------------------------------------------------------------
# Unit tests: quantity stripping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected_core", [
    ("1 kg ground chicken",         "ground chicken"),
    ("1 tsp salt",                  "salt"),
    ("1 tbsp chopped parsley",      "parsley"),
    ("180 ml ice-cold water",       "water"),
    ("150 g cheddar cheese, diced small", "cheddar cheese"),
    ("2 tsp dried oregano",         "dried oregano"),
    ("1/2 tsp cayenne pepper",      "cayenne pepper"),
    ("1 tsp garlic powder",         "garlic powder"),
    ("1 tsp chili powder",          "chili powder"),
    ("1 tsp paprika",               "paprika"),
    ("1 tsp dried thyme",           "dried thyme"),
    ("100 g mozzarella cheese",     "mozzarella cheese"),
    ("2 tsp black pepper",          "black pepper"),
])
def test_strip_quantity_and_prep(raw: str, expected_core: str) -> None:
    result = _strip_quantity_and_prep(raw)
    assert result.lower() == expected_core.lower(), (
        f"_strip_quantity_and_prep({raw!r}) → {result!r}; expected {expected_core!r}"
    )


# ---------------------------------------------------------------------------
# Unit tests: non-ingredient token filter
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("token", [
    "Ingredients",
    "8 Sausages",
    "180 ml",
    "1 tsp",
    "1 kg",
    "Spicy Chicken Sausage",
    "Cheese Chicken Sausage",
    "diced small",
    "",
])
def test_non_ingredient_tokens_are_discarded(token: str) -> None:
    # After stripping quantity the remnant must be recognized as non-ingredient
    stripped = _strip_quantity_and_prep(token)
    assert _is_non_ingredient_token(stripped), (
        f"Token {token!r} → {stripped!r} should be recognized as non-ingredient"
    )


@pytest.mark.parametrize("token", [
    "ground chicken",
    "salt",
    "black pepper",
    "parsley",
    "dried oregano",
    "dried thyme",
    "paprika",
    "chili powder",
    "cayenne pepper",
    "garlic powder",
    "cheddar cheese",
    "mozzarella cheese",
    "water",
])
def test_valid_ingredients_are_kept(token: str) -> None:
    assert not _is_non_ingredient_token(token), (
        f"Token {token!r} should NOT be discarded as a non-ingredient"
    )


# ---------------------------------------------------------------------------
# Integration test: full recipe OCR text simulation
# ---------------------------------------------------------------------------

# This is representative of what OCR would return from the chicken sausage screenshot
_RECIPE_OCR_TEXT = """Spicy Chicken Sausage
Ingredients
1 kg ground chicken
1 tsp salt
1 tsp black pepper
1 tbsp chopped parsley
1 tsp dried oregano
1 tsp dried thyme
1 tsp paprika
1 tsp chili powder
1/2 tsp cayenne pepper
1 tsp garlic powder
180 ml ice-cold water

Cheese Chicken Sausage
Ingredients
1 kg ground chicken
150 g cheddar or mozzarella cheese, diced small
1 tsp salt
1 tsp black pepper
1 tbsp chopped parsley
1 tsp garlic powder
180 ml ice-cold water"""

_EXPECTED_INGREDIENTS = {
    "ground chicken",
    "salt",
    "black pepper",
    "parsley",
    "dried oregano",
    "dried thyme",
    "paprika",
    "chili powder",
    "cayenne pepper",
    "garlic powder",
    "water",
}

_FORBIDDEN_TOKENS = {
    "8 sausages",
    "1 kg",
    "1 tsp",
    "1 tbsp",
    "180 ml",
    "ingredients",
    "spicy chicken sausage",
    "cheese chicken sausage",
    "diced small",
}


def test_recipe_ocr_extracts_ingredients_not_quantities() -> None:
    """
    When OCR returns a recipe image with quantities and titles, only the actual
    ingredient names should appear in the extracted suggestions.
    """
    regions = _regions(_RECIPE_OCR_TEXT)
    suggestions, warnings = extract_ingredients(regions)
    extracted_names = {s.original_text.casefold().strip() for s in suggestions}

    # None of the forbidden tokens should appear
    for forbidden in _FORBIDDEN_TOKENS:
        assert forbidden not in extracted_names, (
            f"Forbidden token {forbidden!r} appeared in extracted ingredients: {extracted_names}"
        )


def test_recipe_ocr_contains_expected_ingredients() -> None:
    """
    At minimum the common spices and meat should be detected.
    """
    regions = _regions(_RECIPE_OCR_TEXT)
    suggestions, _warnings = extract_ingredients(regions)
    extracted_names = {s.original_text.casefold().strip() for s in suggestions}

    for expected in _EXPECTED_INGREDIENTS:
        assert any(expected in name for name in extracted_names), (
            f"Expected ingredient {expected!r} not found in: {sorted(extracted_names)}"
        )


def test_recipe_ocr_produces_non_empty_results() -> None:
    """
    The pipeline must return meaningful suggestions (not empty list)
    when given typical recipe OCR text.
    """
    regions = _regions(_RECIPE_OCR_TEXT)
    suggestions, _warnings = extract_ingredients(regions)
    assert len(suggestions) >= 5, (
        f"Expected at least 5 ingredient suggestions; got {len(suggestions)}: "
        + str([s.original_text for s in suggestions])
    )


# ---------------------------------------------------------------------------
# Fallback classification tests: spices and meat
# ---------------------------------------------------------------------------

def _make_normalized(name: str) -> NormalizedIngredient:
    return NormalizedIngredient(
        original_text=name,
        normalized_name=normalize_token(name),
    )


@pytest.mark.parametrize("ingredient", [
    "salt", "black pepper", "parsley", "dried oregano", "dried thyme",
    "paprika", "chili powder", "cayenne pepper", "garlic powder",
    "water", "sugar",
])
def test_spice_fallback_is_halal(ingredient: str) -> None:
    """Common spices and seasonings must resolve to HALAL via the fallback index."""
    result = evaluate_controlled_fallback(_make_normalized(ingredient))
    assert result is not None, (
        f"No fallback entry for {ingredient!r}; it would return UNKNOWN"
    )
    assert result.status == IngredientHalalStatus.HALAL, (
        f"{ingredient!r} fallback status is {result.status}; expected HALAL"
    )


@pytest.mark.parametrize("ingredient", [
    "ground chicken", "chicken", "chicken breast",
    "beef", "ground beef",
    "lamb",
])
def test_meat_fallback_is_conditional(ingredient: str) -> None:
    """Meat ingredients must resolve to CONDITIONAL (requires slaughter verification)."""
    result = evaluate_controlled_fallback(_make_normalized(ingredient))
    assert result is not None, (
        f"No fallback entry for {ingredient!r}; it would return UNKNOWN"
    )
    assert result.status == IngredientHalalStatus.CONDITIONAL, (
        f"{ingredient!r} fallback status is {result.status}; expected CONDITIONAL"
    )


@pytest.mark.parametrize("ingredient", [
    "cheddar cheese", "mozzarella cheese", "cheddar", "mozzarella",
])
def test_cheese_fallback_is_halal(ingredient: str) -> None:
    """Cheese types must resolve to HALAL (with note about rennet) via fallback."""
    result = evaluate_controlled_fallback(_make_normalized(ingredient))
    assert result is not None, (
        f"No fallback entry for {ingredient!r}; it would return UNKNOWN"
    )
    assert result.status == IngredientHalalStatus.HALAL, (
        f"{ingredient!r} fallback status is {result.status}; expected HALAL"
    )


def test_ice_cold_water_resolves_to_halal() -> None:
    """'ice-cold water' (a common recipe quantity phrase) must resolve to HALAL."""
    result = evaluate_controlled_fallback(_make_normalized("ice-cold water"))
    assert result is not None, "No fallback entry for 'ice-cold water'"
    assert result.status == IngredientHalalStatus.HALAL


# ---------------------------------------------------------------------------
# Normalization alias tests
# ---------------------------------------------------------------------------

def test_ground_chicken_normalizes_same_as_chicken() -> None:
    """
    'ground chicken' and 'chicken' must both hit the same fallback entry
    so neither returns UNKNOWN.
    """
    r1 = evaluate_controlled_fallback(_make_normalized("ground chicken"))
    r2 = evaluate_controlled_fallback(_make_normalized("chicken"))
    assert r1 is not None, "ground chicken must have a fallback"
    assert r2 is not None, "chicken must have a fallback"
    assert r1.status == r2.status == IngredientHalalStatus.CONDITIONAL


def test_dried_oregano_resolves() -> None:
    r = evaluate_controlled_fallback(_make_normalized("dried oregano"))
    assert r is not None, "dried oregano must resolve via fallback"
    assert r.status == IngredientHalalStatus.HALAL
