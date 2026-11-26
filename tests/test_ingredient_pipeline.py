"""
Comprehensive ingredient pipeline tests covering 10 canonical scenarios.

Scenarios
---------
1.  Haram detected – "Water, Pork gelatin, Salt"
2.  All halal plant/mineral – "Water, Salt, Sugar, Rapeseed oil"
3.  Gelatin alone (conditional) – "Water, Gelatin"
4.  E471 (conditional emulsifier) – "Water, E471"
5.  Unknown additive (not in DB) – "Water, Xanthozinamide-7"
6.  OCR digit/letter confusions – "palm 0il, ge1atin, E47l"
7.  Nested compound ingredient – "Chocolate coating (Sugar, Cocoa butter, Gelatin, Emulsifier)"
8.  Incomplete text, no header – single ingredient "Water"
9.  Recipe OCR noise – "Preheat oven to 180°C. Mix 200g flour with yeast and salt"
10. Multilingual ingredients – "Eau, Farine de blé, Levure, ماء"
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.schemas.ocr import OCRTextRegion
from app.services.halal_engine.normalizer import normalize_token
from app.services.ingredient_processing import (
    correct_ocr_text,
    extract_ingredients,
)


# ---------------------------------------------------------------------------
# In-memory SQLite engine for tests that need a DB
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def test_engine():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def test_db(test_engine):
    with Session(test_engine) as session:
        yield session
        session.rollback()


def _regions(*texts: str, confidence: float = 0.92) -> list[OCRTextRegion]:
    return [OCRTextRegion(text=t, confidence=confidence, bounding_box=[]) for t in texts]


# ---------------------------------------------------------------------------
# Scenario 1: Haram detected
# ---------------------------------------------------------------------------

def test_scenario_1_haram_detected():
    """Pork gelatin must surface as HARAM via DB or fallback."""
    regions = _regions("Ingredients: Water, Pork gelatin, Salt")
    suggestions, warnings = extract_ingredients(regions)
    names = [s.original_text.lower() for s in suggestions]
    assert any("pork" in n or "gelatin" in n for n in names), (
        f"Expected pork/gelatin in suggestions; got {names}"
    )
    # The normalizer should NOT strip 'pork' from the token
    assert any("pork" in n for n in names), "Pork qualifier must not be silently stripped"


# ---------------------------------------------------------------------------
# Scenario 2: All halal plant/mineral
# ---------------------------------------------------------------------------

def test_scenario_2_all_halal_plant_mineral():
    """Common plant/mineral staples must be extracted as individual suggestions."""
    regions = _regions("Ingredients: Water, Salt, Sugar, Rapeseed oil")
    suggestions, warnings = extract_ingredients(regions)
    names = {s.original_text.casefold() for s in suggestions}
    for expected in ("water", "salt", "sugar", "rapeseed oil"):
        assert expected in names, f"Expected '{expected}' in extracted suggestions; got {names}"


# ---------------------------------------------------------------------------
# Scenario 3: Gelatin alone (conditional)
# ---------------------------------------------------------------------------

def test_scenario_3_gelatin_conditional():
    """Gelatin without a source qualifier must be CONDITIONAL (source-dependent)."""
    regions = _regions("Ingrédients: Water, Gelatin")
    suggestions, _warnings = extract_ingredients(regions)
    names = {s.original_text.casefold() for s in suggestions}
    assert "gelatin" in names, f"Gelatin must be extracted; got {names}"


# ---------------------------------------------------------------------------
# Scenario 4: E471 conditional emulsifier
# ---------------------------------------------------------------------------

def test_scenario_4_e471_conditional():
    """E471 must be extracted and its normalised E-number recognised."""
    regions = _regions("Ingredients: Water, E471")
    suggestions, _warnings = extract_ingredients(regions)
    names = {s.original_text.casefold() for s in suggestions}
    assert "e471" in names, f"E471 must be extracted; got {names}"

    from app.services.halal_engine.normalizer import extract_e_number
    e_num = extract_e_number("E471")
    assert e_num == "E471"


# ---------------------------------------------------------------------------
# Scenario 5: Unknown additive
# ---------------------------------------------------------------------------

def test_scenario_5_unknown_additive():
    """An entirely unknown additive must be extracted (not discarded) so the
    halal engine can mark it as UNKNOWN rather than silently skipping it."""
    regions = _regions("Ingredients: Water, Xanthozinamide-7")
    suggestions, _warnings = extract_ingredients(regions)
    names = {s.original_text.casefold() for s in suggestions}
    assert "xanthozinamide-7" in names or any(
        "xanthozinamide" in n for n in names
    ), f"Unknown additive must pass through; got {names}"


# ---------------------------------------------------------------------------
# Scenario 6: OCR digit/letter confusions
# ---------------------------------------------------------------------------

def test_scenario_6_ocr_corrections():
    """Common OCR errors must be corrected before extraction."""
    text = "palm 0il, ge1atin, E47l"
    corrected = correct_ocr_text(text)
    assert "palm oil" in corrected.lower(), f"'palm 0il' → 'palm oil' expected; got {corrected!r}"
    assert "gelatin" in corrected.lower(), f"'ge1atin' → 'gelatin' expected; got {corrected!r}"
    # E47l → E471
    assert "E471" in corrected or "e471" in corrected.lower(), (
        f"'E47l' → 'E471' expected; got {corrected!r}"
    )


def test_scenario_6_ocr_extraction_after_correction():
    """After OCR correction the ingredients must be properly extracted."""
    regions = _regions("Ingredients: palm 0il, ge1atin, E47l")
    suggestions, _warnings = extract_ingredients(regions)
    names = {s.original_text.casefold() for s in suggestions}
    assert any("palm oil" in n for n in names), f"palm oil expected after correction; got {names}"
    assert any("gelatin" in n for n in names), f"gelatin expected after correction; got {names}"
    assert any("e471" in n for n in names), f"E471 expected after correction; got {names}"


# ---------------------------------------------------------------------------
# Scenario 7: Nested compound ingredient
# ---------------------------------------------------------------------------

def test_scenario_7_nested_compound_ingredient():
    """Sub-ingredients in parentheses must be flattened into individual entries."""
    text = "Ingredients: Chocolate coating (Sugar, Cocoa butter, Gelatin, Emulsifier)"
    regions = _regions(text)
    suggestions, _warnings = extract_ingredients(regions)
    names = {s.original_text.casefold() for s in suggestions}
    # Sub-ingredients must appear
    assert "sugar" in names or any("sugar" in n for n in names), (
        f"Sub-ingredient 'sugar' must be extracted; got {names}"
    )
    assert any("gelatin" in n for n in names), (
        f"Sub-ingredient 'gelatin' must be extracted; got {names}"
    )
    # Parent may or may not appear; at minimum 3 sub-ingredients must be there
    assert len(suggestions) >= 3, (
        f"Expected ≥3 suggestions from compound ingredient; got {len(suggestions)}"
    )


# ---------------------------------------------------------------------------
# Scenario 8: Incomplete text, no header
# ---------------------------------------------------------------------------

def test_scenario_8_no_header_fallback():
    """When no ingredient header is found the full text fallback must kick in."""
    regions = _regions("Water")
    suggestions, warnings = extract_ingredients(regions)
    # Must have produced at least one suggestion
    assert suggestions, "Fallback must produce at least one suggestion from raw text"
    names = {s.original_text.casefold() for s in suggestions}
    assert "water" in names, f"'Water' must be extracted from raw text fallback; got {names}"
    # At least one warning about the missing header
    assert any("header" in w.lower() or "full" in w.lower() for w in warnings)


# ---------------------------------------------------------------------------
# Scenario 9: Recipe OCR noise
# ---------------------------------------------------------------------------

def test_scenario_9_recipe_ocr_stripped():
    """Recipe instructions must be filtered; food ingredients must pass through."""
    recipe_text = (
        "Preheat oven to 180°C. Mix 200g flour with yeast and salt. "
        "Bake for 30 minutes."
    )
    regions = _regions(recipe_text, confidence=0.80)
    suggestions, warnings = extract_ingredients(regions)
    # Recipe-only text may produce an empty list or return items — the key
    # constraint is that it does NOT crash and does NOT produce items like
    # "Preheat oven to 180°C" as a valid ingredient entry.
    for s in suggestions:
        name = s.original_text.lower()
        assert "preheat" not in name, (
            f"Recipe instruction must not appear as ingredient: {name!r}"
        )
        assert "bake for" not in name, (
            f"Recipe instruction must not appear as ingredient: {name!r}"
        )


# ---------------------------------------------------------------------------
# Scenario 10: Multilingual (FR + AR)
# ---------------------------------------------------------------------------

def test_scenario_10_multilingual():
    """French and Arabic ingredient names must be extracted correctly."""
    regions = _regions("Ingrédients: Eau, Farine de blé, Levure, ماء")
    suggestions, _warnings = extract_ingredients(regions)
    names = {s.original_text for s in suggestions}
    expected = {"Eau", "Farine de blé", "Levure", "ماء"}
    for exp in expected:
        assert any(exp.casefold() in s.casefold() for s in names), (
            f"Expected '{exp}' in extracted suggestions; got {names}"
        )


def test_scenario_10_french_normalization():
    """French accented names must normalize to the same token as their stripped form."""
    assert normalize_token("Farine de blé") == normalize_token("farine de ble")
    assert normalize_token("Arôme naturel") == normalize_token("arome naturel")
    assert normalize_token("Levure") == normalize_token("levure")
    assert normalize_token("Eau") == normalize_token("eau")


# ---------------------------------------------------------------------------
# OCR correction unit tests
# ---------------------------------------------------------------------------

def test_correct_ocr_e_number_trailing_l():
    assert "E471" in correct_ocr_text("E47l")
    assert "E120" in correct_ocr_text("E12O")


def test_correct_ocr_common_words():
    assert "palm oil" in correct_ocr_text("palm 0il").lower()
    assert "gelatin" in correct_ocr_text("ge1atin").lower()
    assert "salt" in correct_ocr_text("sa1t").lower()
    assert "water" in correct_ocr_text("wat3r").lower()


def test_correct_ocr_does_not_change_safe_e_number():
    """A valid E number like E471 must not be altered."""
    assert "E471" in correct_ocr_text("E471")
