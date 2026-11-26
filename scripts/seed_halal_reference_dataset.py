"""
Idempotent seed script: import the initial Halal ingredient reference dataset.

Sources:
  - Haram/Avoid list (20 entries)
  - Investigate Further (22 named + 35 E-number entries)

Safe to run multiple times — duplicates are detected by normalized_name and skipped.

Usage (from backend/):
    .\.venv310\Scripts\python.exe scripts/seed_halal_reference_dataset.py
"""

from __future__ import annotations

import asyncio
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure app package is importable when running from backend/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models.entities import Ingredient, IngredientSource
from app.models.enums import EvidenceReviewStatus, EvidenceType, IngredientHalalStatus
from app.services.halal_engine.normalizer import normalize_token

# ---------------------------------------------------------------------------
# Normalisation helpers (mirrors normalizer.py logic so they match at lookup)
# ---------------------------------------------------------------------------

_E_NUMBER_RE = re.compile(r"\bE\s*-?\s*(\d+[a-z]?)\b", re.IGNORECASE)


def _normalize_e_number(raw: str) -> str:
    """Normalize 'E-471', 'E 471', 'e471' → 'E471'."""
    m = _E_NUMBER_RE.search(raw)
    if m:
        return f"E{m.group(1).upper()}"
    return raw.strip().upper()


def _normalize_name(name: str) -> str:
    """Use the engine's canonical Unicode/accent/case normalizer."""
    return normalize_token(name)


# ---------------------------------------------------------------------------
# Data definitions
# ---------------------------------------------------------------------------

SOURCE_NAME = "User-provided Halal/Avoid ingredient reference"

# classification constants
HARAM_AVOID = "haram_avoid"
INVESTIGATE = "investigate"


@dataclass
class IngredientDef:
    canonical_name: str
    source_classification: str
    halal_status: IngredientHalalStatus
    ingredient_type: str = "food_additive"
    e_number: str | None = None
    aliases: list[str] = field(default_factory=list)
    description: str = ""
    conditions: list[str] = field(default_factory=list)
    source_requirements: str | None = None


def _haram(
    name: str,
    aliases: list[str] | None = None,
    ingredient_type: str = "substance",
    description: str = "",
    conditions: list[str] | None = None,
    source_requirements: str | None = None,
) -> IngredientDef:
    return IngredientDef(
        canonical_name=name,
        source_classification=HARAM_AVOID,
        halal_status=IngredientHalalStatus.HARAM,
        ingredient_type=ingredient_type,
        aliases=aliases or [],
        description=description,
        conditions=conditions or [],
        source_requirements=source_requirements,
    )


def _conditional_haram(
    name: str,
    aliases: list[str] | None = None,
    ingredient_type: str = "substance",
    description: str = "",
    conditions: list[str] | None = None,
    source_requirements: str | None = None,
) -> IngredientDef:
    """Haram/Avoid entries that are ambiguous and require expert review."""
    return IngredientDef(
        canonical_name=name,
        source_classification=HARAM_AVOID,
        halal_status=IngredientHalalStatus.CONDITIONAL,
        ingredient_type=ingredient_type,
        aliases=aliases or [],
        description=description,
        conditions=conditions or ["Source and processing method must be verified"],
        source_requirements=source_requirements,
    )


def _investigate(
    name: str,
    aliases: list[str] | None = None,
    ingredient_type: str = "food_additive",
    description: str = "",
    conditions: list[str] | None = None,
    e_number: str | None = None,
    source_requirements: str | None = None,
) -> IngredientDef:
    return IngredientDef(
        canonical_name=name,
        source_classification=INVESTIGATE,
        halal_status=IngredientHalalStatus.DOUBTFUL,
        ingredient_type=ingredient_type,
        e_number=e_number,
        aliases=aliases or [],
        description=description,
        conditions=conditions or ["Source verification required"],
        source_requirements=source_requirements or "Must confirm animal-free or halal-certified source",
    )


def _investigate_conditional(
    name: str,
    aliases: list[str] | None = None,
    ingredient_type: str = "food_additive",
    description: str = "",
    conditions: list[str] | None = None,
    e_number: str | None = None,
    source_requirements: str | None = None,
) -> IngredientDef:
    return IngredientDef(
        canonical_name=name,
        source_classification=INVESTIGATE,
        halal_status=IngredientHalalStatus.CONDITIONAL,
        ingredient_type=ingredient_type,
        e_number=e_number,
        aliases=aliases or [],
        description=description,
        conditions=conditions or ["Halal if from plant or synthetic source; haram if from pork or non-halal animal"],
        source_requirements=source_requirements or "Must confirm halal-certified source",
    )


# ---------------------------------------------------------------------------
# Haram / Avoid entries  (20 total)
# ---------------------------------------------------------------------------
HARAM_ENTRIES: list[IngredientDef] = [
    # Unambiguously haram — pork / porcine
    _haram(
        "Lard",
        aliases=["animal fat (pork)", "porcine fat", "pig fat"],
        ingredient_type="fat",
        description="Fat rendered from pig tissue. Haram — derived from pork.",
    ),
    _haram(
        "Pork",
        aliases=["porcine", "pork meat", "pig meat"],
        ingredient_type="meat",
        description="Meat from pigs. Explicitly prohibited in Islam.",
    ),
    _haram(
        "Bacon",
        ingredient_type="meat",
        description="Cured pork product. Haram — derived from pork.",
    ),
    _haram(
        "Bacon bits",
        ingredient_type="meat",
        description="Small pieces of cooked or simulated bacon, often pork-derived. Haram.",
    ),
    _haram(
        "Ham",
        ingredient_type="meat",
        description="Cured pork leg. Haram — derived from pork.",
    ),
    _haram(
        "Hydrolyzed porcine collagen",
        aliases=["porcine collagen hydrolysate", "pork collagen"],
        ingredient_type="protein",
        description="Protein extracted from pig connective tissue. Haram — porcine origin.",
    ),
    # Unambiguously haram — alcohol
    _haram(
        "Beer",
        ingredient_type="alcoholic_beverage",
        description="Fermented alcoholic beverage. Haram — contains alcohol (khamr).",
    ),
    _haram(
        "Gin",
        ingredient_type="alcoholic_beverage",
        description="Distilled alcoholic spirit. Haram — contains alcohol.",
    ),
    _haram(
        "Malt liquor",
        ingredient_type="alcoholic_beverage",
        description="High-alcohol malt beverage. Haram — contains alcohol.",
    ),
    _haram(
        "Rum",
        ingredient_type="alcoholic_beverage",
        description="Distilled alcoholic spirit made from sugarcane. Haram — contains alcohol.",
    ),
    _haram(
        "Scotch",
        aliases=["Scotch whisky"],
        ingredient_type="alcoholic_beverage",
        description="Scottish whisky. Haram — contains alcohol.",
    ),
    _haram(
        "Whiskey",
        aliases=["whisky"],
        ingredient_type="alcoholic_beverage",
        description="Distilled alcoholic spirit from grain mash. Haram — contains alcohol.",
    ),
    _haram(
        "Wine",
        ingredient_type="alcoholic_beverage",
        description="Fermented grape juice. Haram — contains alcohol (khamr).",
    ),
    _haram(
        "Wine coolers",
        ingredient_type="alcoholic_beverage",
        description="Wine-based alcoholic drink. Haram — contains alcohol.",
    ),
    _haram(
        "Vodka",
        ingredient_type="alcoholic_beverage",
        description="Distilled alcoholic spirit. Haram — contains alcohol.",
    ),
    # Ambiguous Haram/Avoid entries → conditional
    _conditional_haram(
        "Gelatin",
        aliases=["gelatine"],
        ingredient_type="protein",
        description=(
            "Protein obtained by partial hydrolysis of collagen from animal bones, connective tissue, "
            "or skin. Status depends on the source: pork-derived is haram; beef-derived requires "
            "halal slaughter; fish or plant-based is halal."
        ),
        conditions=[
            "Halal only if derived from halal-slaughtered animal or fish; haram if pork-derived",
        ],
        source_requirements="Source animal and slaughter method must be verified",
    ),
    _conditional_haram(
        "Malt",
        ingredient_type="grain_product",
        description=(
            "Sprouted and dried grain (usually barley). Halal in food products (malt extract, "
            "malt flavoring) unless used as an ingredient in alcoholic beverages."
        ),
        conditions=[
            "Halal in food context (e.g., malt extract, malt vinegar without alcohol); "
            "haram when present in alcoholic beverages",
        ],
        source_requirements="Confirm alcohol content is negligible or absent",
    ),
    _conditional_haram(
        "Shortening",
        aliases=["vegetable shortening", "animal shortening"],
        ingredient_type="fat",
        description=(
            "Solid fat used in baking. Can be plant-derived (halal) or animal-derived "
            "(lard/tallow — haram without halal certification)."
        ),
        conditions=[
            "Halal if from fully plant-based or verified halal animal source",
        ],
        source_requirements="Source (plant vs. animal) and processing method must be verified",
    ),
    _conditional_haram(
        "Animal shortening",
        aliases=["animal fat shortening"],
        ingredient_type="fat",
        description=(
            "Shortening derived from animal fat. If the source is pork, it is haram. "
            "If beef, requires halal slaughter verification."
        ),
        conditions=[
            "Haram if pork-derived; conditional if beef-derived (halal slaughter required)",
        ],
        source_requirements="Source animal and slaughter method must be verified",
    ),
    _conditional_haram(
        "Hydrolyzed animal protein",
        aliases=["HAP", "hydrolyzed protein"],
        ingredient_type="protein",
        description=(
            "Protein broken down by hydrolysis from animal sources. Status depends on the "
            "source animal and slaughter method."
        ),
        conditions=[
            "Haram if pork-derived; conditional if from other animals (halal slaughter required)",
        ],
        source_requirements="Source animal must be declared; halal slaughter required for non-porcine sources",
    ),
]

# ---------------------------------------------------------------------------
# Investigate Further — named entries (22 total)
# ---------------------------------------------------------------------------
INVESTIGATE_NAMED: list[IngredientDef] = [
    _investigate(
        "Artificial and natural colorings",
        aliases=["artificial colorings", "natural colorings", "artificial colour", "natural colour"],
        ingredient_type="coloring",
        description=(
            "Food colorings may be derived from plant, synthetic, or animal sources. "
            "Some (e.g., carmine/E120) are derived from insects and are controversial."
        ),
    ),
    _investigate(
        "Artificial and natural flavorings",
        aliases=[
            "artificial flavorings",
            "natural flavorings",
            "natural flavoring",
            "natural flavouring",
            "artificial flavour",
            "natural flavour",
            "arôme naturel",
            "arômes naturels",
            "arôme",
            "arômes",
            "arôme artificiel",
            "نكهة طبيعية",
            "نكهة اصطناعية",
        ],
        ingredient_type="flavoring",
        description=(
            "Flavorings may be synthesized or extracted from plant or animal sources. "
            "Animal-derived flavorings require halal sourcing."
        ),
    ),
    _investigate_conditional(
        "Calcium stearoyl lactylate",
        aliases=["calcium stearoyl-2-lactylate", "CSL"],
        ingredient_type="emulsifier",
        description="Emulsifier derived from lactic acid and stearic acid. Stearic acid may be animal-derived.",
        conditions=["Halal if stearic acid from plant or synthetic source; doubtful if animal-derived"],
    ),
    _investigate(
        "Enzymes",
        ingredient_type="enzyme",
        description=(
            "Biological catalysts used in food processing. May be of microbial, plant, or animal origin. "
            "Rennet from non-halal-slaughtered animals is haram."
        ),
        conditions=["Source (microbial, plant, animal) must be verified"],
    ),
    _investigate_conditional(
        "Fatty acids",
        aliases=["free fatty acids"],
        ingredient_type="fat",
        description="Fatty acids used as food additives may come from animal (tallow) or plant (vegetable oil) sources.",
    ),
    _investigate_conditional(
        "Glycerin",
        aliases=["glycerol", "E422"],
        e_number="E422",
        ingredient_type="humectant",
        description=(
            "Trihydric alcohol used as a humectant, sweetener, and solvent. "
            "Can be derived from vegetable oils or animal fats."
        ),
    ),
    _investigate_conditional(
        "Glycerol stearate",
        aliases=["glyceryl stearate", "monostearin"],
        ingredient_type="emulsifier",
        description="Ester of glycerol and stearic acid. Status depends on the source of stearic acid.",
    ),
    _investigate(
        "Gum base",
        ingredient_type="gum",
        description=(
            "Complex mixture used in chewing gum. May contain lanolin, stearic acid, "
            "or other animal-derived components."
        ),
        conditions=["Ingredient breakdown of gum base must be reviewed"],
    ),
    _investigate_conditional(
        "Hydrolyzed bovine collagen",
        aliases=["beef collagen hydrolysate", "bovine collagen"],
        ingredient_type="protein",
        description="Protein extracted from cattle connective tissue. Halal only if from halal-slaughtered cattle.",
        conditions=["Halal only if from halal-slaughtered cattle; source verification required"],
    ),
    _investigate_conditional(
        "Lactylated fatty acid esters",
        aliases=["lactylated fatty acids"],
        ingredient_type="emulsifier",
        description="Esters of lactic acid and fatty acids. Status depends on fatty acid source.",
    ),
    _investigate_conditional(
        "Magnesium stearate",
        ingredient_type="anti-caking_agent",
        description=(
            "Salt of stearic acid and magnesium. Stearic acid may be animal- or plant-derived. "
            "Common in pharmaceuticals and supplements."
        ),
    ),
    _investigate_conditional(
        "Mono and Diglycerides",
        aliases=[
            "mono & diglycerides",
            "monoglycerides and diglycerides",
            "monoglycerides",
            "diglycerides",
            "E471",
        ],
        e_number="E471",
        ingredient_type="emulsifier",
        description=(
            "Emulsifiers produced from glycerol and fatty acids. Can be plant- or animal-derived. "
            "If from pork or non-halal-slaughtered animals, they are haram."
        ),
    ),
    _investigate_conditional(
        "Phospholipids",
        aliases=["lecithin"],
        ingredient_type="emulsifier",
        description=(
            "Lipids containing phosphate groups, used as emulsifiers. "
            "Soy or sunflower lecithin is plant-derived (halal); "
            "egg lecithin is from eggs (halal if from permissible birds); "
            "animal-derived phospholipids require halal sourcing."
        ),
    ),
    _investigate_conditional(
        "Polysorbates",
        aliases=["polysorbate 20", "polysorbate 60", "polysorbate 80"],
        ingredient_type="emulsifier",
        description=(
            "Emulsifiers derived from polyethoxylated sorbitan and fatty acids. "
            "The fatty acid component may be of animal or plant origin."
        ),
    ),
    _investigate_conditional(
        "Potassium stearate",
        ingredient_type="food_additive",
        description="Potassium salt of stearic acid. Stearic acid may be animal- or plant-derived.",
    ),
    _investigate_conditional(
        "Propylene glycol monostearate",
        aliases=["PGMS", "E477"],
        e_number="E477",
        ingredient_type="emulsifier",
        description="Emulsifier made from propylene glycol and stearic acid. Status depends on stearic acid source.",
    ),
    _investigate(
        "Sodium lauryl sulfate",
        aliases=["SLS", "sodium dodecyl sulfate"],
        ingredient_type="surfactant",
        description=(
            "Surfactant used in food processing and pharmaceutical products. "
            "Usually synthetic but verify source."
        ),
    ),
    _investigate_conditional(
        "Sodium stearate",
        ingredient_type="food_additive",
        description="Sodium salt of stearic acid. Stearic acid may be animal- or plant-derived.",
    ),
    _investigate_conditional(
        "Sorbitan monostearate",
        aliases=["Span 60", "E491"],
        e_number="E491",
        ingredient_type="emulsifier",
        description="Emulsifier made from sorbitol and stearic acid. Status depends on stearic acid source.",
    ),
    _investigate_conditional(
        "Stearic acid",
        aliases=["octadecanoic acid", "E570"],
        e_number="E570",
        ingredient_type="fatty_acid",
        description=(
            "Saturated fatty acid found in animal and plant fats. "
            "Halal if from plant (e.g., shea butter, palm) or synthetic source; "
            "haram if from pork; conditional if from other animals."
        ),
    ),
    _investigate_conditional(
        "Stearoyl lactylate",
        aliases=["sodium stearoyl lactylate", "SSL", "E481", "calcium stearoyl lactylate"],
        e_number="E481",
        ingredient_type="emulsifier",
        description="Emulsifier made from lactic acid and stearic acid. Status depends on stearic acid source.",
    ),
    _investigate_conditional(
        "Tallow",
        aliases=["beef tallow", "rendered animal fat", "suet"],
        ingredient_type="fat",
        description=(
            "Rendered fat from cattle or sheep. Halal only if from halal-slaughtered animals. "
            "Haram if porcine."
        ),
        conditions=["Must be from halal-slaughtered cattle or sheep; haram if porcine"],
    ),
    _investigate(
        "Whey",
        aliases=["whey protein", "whey powder", "dried whey"],
        ingredient_type="dairy",
        description=(
            "By-product of cheese production. Generally considered halal (from milk). "
            "Concern arises if the cheese was made using non-halal animal rennet."
        ),
        conditions=["Rennet source used in cheese production must be verified"],
    ),
]

# ---------------------------------------------------------------------------
# E-number entries (35 total) — each gets its own record with chemical name
# ---------------------------------------------------------------------------
E_NUMBER_ENTRIES: list[IngredientDef] = [
    _investigate_conditional(
        "Ascorbyl palmitate",
        aliases=["E304"],
        e_number="E304",
        ingredient_type="antioxidant",
        description="Antioxidant ester of ascorbic acid and palmitic acid. Palm acid may be animal-derived.",
    ),
    _investigate_conditional(
        "Lecithin",
        aliases=["E322", "soy lecithin", "sunflower lecithin", "egg lecithin"],
        e_number="E322",
        ingredient_type="emulsifier",
        description=(
            "Natural emulsifier from soybeans, sunflower, or eggs. "
            "Usually halal; animal-derived sources require verification."
        ),
    ),
    # E422 is covered under Glycerin above; keeping a separate record for E-number only lookup
    _investigate_conditional(
        "Glycerol",
        aliases=["E422", "glycerin", "glycerine"],
        e_number="E422",
        ingredient_type="humectant",
        description="Trihydric alcohol. Can be from vegetable or animal origin.",
    ),
    _investigate_conditional(
        "Polyoxyethylene sorbitan monolaureate",
        aliases=["Polysorbate 20", "E431", "Tween 20"],
        e_number="E431",
        ingredient_type="emulsifier",
        description="Emulsifier derived from sorbitol and lauric acid. Fatty acid source must be verified.",
    ),
    _investigate_conditional(
        "Polyoxyethylene sorbitan monooleate",
        aliases=["Polysorbate 80", "E433", "Tween 80"],
        e_number="E433",
        ingredient_type="emulsifier",
        description="Emulsifier derived from sorbitol and oleic acid. Fatty acid source must be verified.",
    ),
    _investigate_conditional(
        "Polyoxyethylene sorbitan monopalmitate",
        aliases=["Polysorbate 40", "E434"],
        e_number="E434",
        ingredient_type="emulsifier",
        description="Emulsifier derived from sorbitol and palmitic acid. Fatty acid source must be verified.",
    ),
    _investigate_conditional(
        "Polyoxyethylene sorbitan monostearate",
        aliases=["Polysorbate 60", "E435", "Tween 60"],
        e_number="E435",
        ingredient_type="emulsifier",
        description="Emulsifier derived from sorbitol and stearic acid. Fatty acid source must be verified.",
    ),
    _investigate_conditional(
        "Polyoxyethylene sorbitan tristearate",
        aliases=["Polysorbate 65", "E436", "Tween 65"],
        e_number="E436",
        ingredient_type="emulsifier",
        description="Emulsifier derived from sorbitol and stearic acid. Fatty acid source must be verified.",
    ),
    _investigate_conditional(
        "Ammonium phosphatides",
        aliases=["E442"],
        e_number="E442",
        ingredient_type="emulsifier",
        description=(
            "Emulsifier made from ammonium salts of phosphorylated glycerides. "
            "Usually from rapeseed oil, but animal-derived variants exist."
        ),
    ),
    _investigate_conditional(
        "Wood rosin esters",
        aliases=["E445", "glycerol esters of wood rosin", "ester gum"],
        e_number="E445",
        ingredient_type="emulsifier",
        description="Emulsifier/stabiliser from wood rosin. Glycerol component source must be confirmed.",
    ),
    _investigate_conditional(
        "Fatty acid salts (sodium, potassium)",
        aliases=["E470a", "sodium salts of fatty acids"],
        e_number="E470a",
        ingredient_type="food_additive",
        description="Sodium and potassium salts of fatty acids. Fatty acid source (animal or plant) must be verified.",
    ),
    _investigate_conditional(
        "Fatty acid salts (magnesium, calcium)",
        aliases=["E470b", "magnesium salts of fatty acids", "calcium salts of fatty acids"],
        e_number="E470b",
        ingredient_type="food_additive",
        description="Magnesium and calcium salts of fatty acids. Fatty acid source (animal or plant) must be verified.",
    ),
    _investigate_conditional(
        "Mono and diglycerides of fatty acids",
        aliases=["E471", "monoglycerides", "diglycerides", "mono & diglycerides"],
        e_number="E471",
        ingredient_type="emulsifier",
        description=(
            "Common emulsifier from glycerol and fatty acids. "
            "Halal if from plant; haram if from pork; conditional otherwise."
        ),
    ),
    _investigate_conditional(
        "Acetic acid esters of mono and diglycerides",
        aliases=["E472a", "acetylated monoglycerides"],
        e_number="E472a",
        ingredient_type="emulsifier",
        description="Emulsifier derived from monoglycerides and acetic acid. Source of fatty acid must be verified.",
    ),
    _investigate_conditional(
        "Lactic acid esters of mono and diglycerides",
        aliases=["E472b", "lactylated monoglycerides"],
        e_number="E472b",
        ingredient_type="emulsifier",
        description="Emulsifier derived from monoglycerides and lactic acid. Source of fatty acid must be verified.",
    ),
    _investigate_conditional(
        "Citric acid esters of mono and diglycerides",
        aliases=["E472c", "citroglycerides"],
        e_number="E472c",
        ingredient_type="emulsifier",
        description="Emulsifier derived from monoglycerides and citric acid. Source of fatty acid must be verified.",
    ),
    _investigate_conditional(
        "Tartaric acid esters of mono and diglycerides",
        aliases=["E472d"],
        e_number="E472d",
        ingredient_type="emulsifier",
        description="Emulsifier derived from monoglycerides and tartaric acid. Source of fatty acid must be verified.",
    ),
    _investigate_conditional(
        "Diacetyl tartaric acid esters of mono and diglycerides",
        aliases=["E472e", "DATEM"],
        e_number="E472e",
        ingredient_type="emulsifier",
        description="Emulsifier commonly used in bread. Source of monoglycerides must be verified.",
    ),
    _investigate_conditional(
        "Mixed acetic and tartaric acid esters of mono and diglycerides",
        aliases=["E472f"],
        e_number="E472f",
        ingredient_type="emulsifier",
        description="Mixed emulsifier from monoglycerides and acetic/tartaric acids. Source must be verified.",
    ),
    _investigate_conditional(
        "Sucrose esters of fatty acids",
        aliases=["E473"],
        e_number="E473",
        ingredient_type="emulsifier",
        description="Emulsifier made from sucrose and fatty acids. Status depends on fatty acid source.",
    ),
    _investigate_conditional(
        "Sucroglycerides",
        aliases=["E474"],
        e_number="E474",
        ingredient_type="emulsifier",
        description="Mixture of mono/diglycerides and sucrose esters. Status depends on fatty acid source.",
    ),
    _investigate_conditional(
        "Polyglycerol esters of fatty acids",
        aliases=["E475"],
        e_number="E475",
        ingredient_type="emulsifier",
        description="Emulsifier made from polyglycerol and fatty acids. Status depends on fatty acid source.",
    ),
    _investigate_conditional(
        "Polyglycerol polyricinoleate",
        aliases=["E476", "PGPR"],
        e_number="E476",
        ingredient_type="emulsifier",
        description="Emulsifier from castor oil (plant-derived ricinoleic acid) and glycerol. Generally considered halal.",
        conditions=["Usually plant-derived; verify glycerol source"],
    ),
    _investigate_conditional(
        "Propylene glycol esters of fatty acids",
        aliases=["E477"],
        e_number="E477",
        ingredient_type="emulsifier",
        description="Emulsifier made from propylene glycol and fatty acids. Status depends on fatty acid source.",
    ),
    _investigate_conditional(
        "Thermally oxidized soybean oil interacted with mono and diglycerides",
        aliases=["E479", "TOSOM"],
        e_number="E479",
        ingredient_type="emulsifier",
        description="Soybean oil-based emulsifier interacted with monoglycerides. Source of monoglycerides must be verified.",
    ),
    _investigate_conditional(
        "Sodium stearoyl lactylate",
        aliases=["E481", "SSL"],
        e_number="E481",
        ingredient_type="emulsifier",
        description="Emulsifier made from lactic acid and stearic acid (sodium salt). Source of stearic acid must be verified.",
    ),
    _investigate_conditional(
        "Calcium stearoyl lactylate",
        aliases=["E482", "CSL"],
        e_number="E482",
        ingredient_type="emulsifier",
        description="Emulsifier made from lactic acid and stearic acid (calcium salt). Source of stearic acid must be verified.",
    ),
    _investigate_conditional(
        "Stearyl tartrate",
        aliases=["E483"],
        e_number="E483",
        ingredient_type="emulsifier",
        description="Emulsifier made from tartaric acid and stearyl alcohol. Source of stearyl alcohol must be verified.",
    ),
    _investigate_conditional(
        "Sorbitan monostearate",
        aliases=["E491", "Span 60"],
        e_number="E491",
        ingredient_type="emulsifier",
        description="Emulsifier made from sorbitol and stearic acid. Source of stearic acid must be verified.",
    ),
    _investigate_conditional(
        "Sorbitan tristearate",
        aliases=["E492", "Span 65"],
        e_number="E492",
        ingredient_type="emulsifier",
        description="Emulsifier made from sorbitol and stearic acid. Source of stearic acid must be verified.",
    ),
    _investigate_conditional(
        "Sorbitan monolaurate",
        aliases=["E493", "Span 20"],
        e_number="E493",
        ingredient_type="emulsifier",
        description="Emulsifier made from sorbitol and lauric acid. Source of lauric acid must be verified.",
    ),
    _investigate_conditional(
        "Sorbitan monooleate",
        aliases=["E494", "Span 80"],
        e_number="E494",
        ingredient_type="emulsifier",
        description="Emulsifier made from sorbitol and oleic acid. Source of oleic acid must be verified.",
    ),
    _investigate_conditional(
        "Sorbitan monopalmitate",
        aliases=["E495", "Span 40"],
        e_number="E495",
        ingredient_type="emulsifier",
        description="Emulsifier made from sorbitol and palmitic acid. Source of palmitic acid must be verified.",
    ),
    _investigate_conditional(
        "Stearic acid",
        aliases=["E570", "octadecanoic acid"],
        e_number="E570",
        ingredient_type="fatty_acid",
        description=(
            "Saturated fatty acid used as anti-caking agent and release agent. "
            "Halal if plant-derived; conditional if animal-derived."
        ),
    ),
    _investigate_conditional(
        "Polysorbate 20",
        aliases=["E432", "Tween 20"],
        e_number="E432",
        ingredient_type="emulsifier",
        description="Emulsifier derived from sorbitol and lauric acid. Fatty acid source must be verified.",
    ),
]

# ---------------------------------------------------------------------------
# Common plant-derived, mineral, and microbial halal-compatible staples
# ---------------------------------------------------------------------------

PLANT_MINERAL_SOURCE_NAME = "HalalLens Plant & Mineral Reference"
PLANT_MINERAL_CLASSIFICATION = "plant_mineral_halal"


def _plant_halal(
    name: str,
    aliases: list[str],
    ingredient_type: str,
    description: str,
) -> IngredientDef:
    return IngredientDef(
        canonical_name=name,
        source_classification=PLANT_MINERAL_CLASSIFICATION,
        halal_status=IngredientHalalStatus.HALAL,
        ingredient_type=ingredient_type,
        aliases=aliases,
        description=description,
    )


PLANT_MINERAL_HALAL_ENTRIES: list[IngredientDef] = [
    _plant_halal(
        "Water",
        ["eau", "aqua", "purified water", "eau purifiée", "ماء", "مياه"],
        "mineral",
        "Water is a natural mineral ingredient and is halal-compatible.",
    ),
    _plant_halal(
        "Wheat flour",
        ["farine de blé", "farine de froment", "دقيق القمح", "طحين القمح"],
        "plant_grain",
        "Wheat flour is a plant-derived grain ingredient and is halal-compatible.",
    ),
    _plant_halal(
        "Whole wheat flour",
        [
            "wholemeal wheat flour",
            "farine complète de blé",
            "farine de blé complet",
            "farine intégrale de blé",
            "دقيق القمح الكامل",
        ],
        "plant_grain",
        "Whole wheat flour is a plant-derived grain ingredient and is halal-compatible.",
    ),
    _plant_halal(
        "Wheat gluten",
        ["gluten de blé", "غلوتين القمح"],
        "plant_protein",
        "Wheat gluten is a plant-derived grain protein and is halal-compatible.",
    ),
    _plant_halal(
        "Corn",
        ["maize", "maïs", "ذرة"],
        "plant_grain",
        "Corn (maize) is a plant-derived grain ingredient and is halal-compatible.",
    ),
    _plant_halal(
        "Corn flour",
        ["maize flour", "farine de maïs", "دقيق الذرة"],
        "plant_grain",
        "Corn flour is a plant-derived grain ingredient and is halal-compatible.",
    ),
    _plant_halal(
        "Fermented corn flour",
        ["farine de maïs fermentée"],
        "plant_grain",
        "Fermented corn flour is a plant-derived grain ingredient and is halal-compatible.",
    ),
    _plant_halal(
        "Rapeseed oil",
        ["canola oil", "huile de colza", "زيت الكانولا", "زيت اللفت"],
        "plant_oil",
        "Rapeseed (canola) oil is a plant-derived vegetable oil and is halal-compatible.",
    ),
    _plant_halal(
        "Sunflower oil",
        ["huile de tournesol", "زيت دوار الشمس"],
        "plant_oil",
        "Sunflower oil is a plant-derived vegetable oil and is halal-compatible.",
    ),
    _plant_halal(
        "Olive oil",
        ["huile d'olive", "زيت الزيتون"],
        "plant_oil",
        "Olive oil is a plant-derived vegetable oil and is halal-compatible.",
    ),
    _plant_halal(
        "Salt",
        ["sea salt", "table salt", "sel", "sel marin", "sel de table", "ملح"],
        "mineral",
        "Salt is a mineral ingredient and is halal-compatible.",
    ),
    _plant_halal(
        "Yeast",
        ["baker's yeast", "levure", "levure de boulanger", "خميرة"],
        "microbial",
        "Baker's yeast is a microbial ingredient and is halal-compatible.",
    ),
    _plant_halal(
        "Acerola",
        ["acerola cherry", "acérola", "اسيرولا"],
        "plant_fruit",
        "Acerola is a plant-derived fruit ingredient and is halal-compatible.",
    ),
    _plant_halal(
        "Acerola juice powder",
        ["jus d'acérola en poudre", "poudre de jus d'acérola"],
        "plant_fruit",
        "Acerola juice powder is a plant-derived fruit ingredient and is halal-compatible.",
    ),
    _plant_halal(
        "Sugar",
        ["cane sugar", "beet sugar", "sucre", "سكر"],
        "plant_sweetener",
        "Sugar (sucrose) is a plant-derived sweetener and is halal-compatible.",
    ),
]

# ---------------------------------------------------------------------------
# Main import logic
# ---------------------------------------------------------------------------

ALL_INGREDIENTS: list[IngredientDef] = (
    HARAM_ENTRIES + INVESTIGATE_NAMED + E_NUMBER_ENTRIES + PLANT_MINERAL_HALAL_ENTRIES
)


def _make_ingredient_obj(defn: IngredientDef) -> dict[str, Any]:
    """Build the dict of kwargs for the Ingredient ORM object."""
    confidence = 0.85 if defn.source_classification == PLANT_MINERAL_CLASSIFICATION else 0.0
    return {
        "canonical_name": defn.canonical_name,
        "normalized_name": _normalize_name(defn.canonical_name),
        "ingredient_type": defn.ingredient_type,
        "e_number": defn.e_number,
        "aliases": defn.aliases,
        "description": defn.description,
        "halal_status": defn.halal_status,
        "source_requirements": defn.source_requirements,
        "conditions": defn.conditions,
        "confidence_level": confidence,
        "source_classification": defn.source_classification,
    }


def _evidence_summary(defn: IngredientDef) -> str:
    if defn.source_classification == PLANT_MINERAL_CLASSIFICATION:
        return (
            f"Standard plant-derived, mineral, or microbial staple ingredient "
            f"({defn.canonical_name})."
        )
    category = (
        "Haram/Avoid category" if defn.source_classification == HARAM_AVOID else "Investigate Further category"
    )
    return (
        f"Imported from {SOURCE_NAME}. "
        f"Listed under '{category}'. "
        f"Initial status: {defn.halal_status.value}. "
        f"Pending expert review — do not treat as fully verified."
    )


async def run_import(session: AsyncSession) -> dict[str, int]:
    imported = 0
    skipped = 0
    total = len(ALL_INGREDIENTS)
    now = datetime.now(timezone.utc)

    for defn in ALL_INGREDIENTS:
        normalized = _normalize_name(defn.canonical_name)

        # Idempotency check: skip if normalized_name already exists
        existing = (
            await session.execute(
                select(Ingredient).where(Ingredient.normalized_name == normalized)
            )
        ).scalars().first()

        if existing is not None:
            skipped += 1
            continue

        ingredient = Ingredient(**_make_ingredient_obj(defn))
        session.add(ingredient)
        await session.flush()  # populate ingredient.id

        is_plant_mineral = defn.source_classification == PLANT_MINERAL_CLASSIFICATION
        source = IngredientSource(
            ingredient_id=ingredient.id,
            source_name=PLANT_MINERAL_SOURCE_NAME if is_plant_mineral else SOURCE_NAME,
            source_url=None,
            evidence_summary=_evidence_summary(defn),
            evidence_type=EvidenceType.INGREDIENT if is_plant_mineral else EvidenceType.OTHER,
            reviewer_or_organization=None,
            verification_date=None,
            imported_at=now,
            review_status=(
                EvidenceReviewStatus.VERIFIED
                if is_plant_mineral
                else EvidenceReviewStatus.PENDING_REVIEW
            ),
        )
        session.add(source)
        imported += 1

    await session.commit()

    return {"total_defined": total, "imported": imported, "skipped": skipped}


async def main() -> None:
    db_url = str(get_settings().database_url)

    print(f"Connecting to database...")
    engine = create_async_engine(db_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        stats = await run_import(session)

    await engine.dispose()

    print(f"\n{'='*50}")
    print(f"  HalalLens Halal Reference Dataset Import")
    print(f"{'='*50}")
    print(f"  Defined in script : {stats['total_defined']}")
    print(f"  Imported (new)    : {stats['imported']}")
    print(f"  Skipped (dupes)   : {stats['skipped']}")
    print(f"{'='*50}")
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
