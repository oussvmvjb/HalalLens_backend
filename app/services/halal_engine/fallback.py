"""Controlled deterministic fallback for reliably known plant/mineral staples and known ambiguous terms."""

from dataclasses import dataclass

from app.models.enums import IngredientHalalStatus
from app.services.halal_engine.models import MatchedIngredient, NormalizedIngredient
from app.services.halal_engine.normalizer import normalize_token


@dataclass(frozen=True)
class _FallbackEntry:
    canonical_name: str
    status: IngredientHalalStatus
    reason: str
    warnings: tuple[str, ...] = ()
    confidence: float = 0.8


_PLANT_MINERAL_HALAL_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "Water",
        "Water is a natural mineral ingredient and is halal-compatible.",
        ("water", "eau", "aqua", "purified water", "eau purifiee", "ماء", "مياه"),
    ),
    (
        "Wheat flour",
        "Wheat flour is a plant-derived grain ingredient and is halal-compatible.",
        ("wheat flour", "farine de ble", "farine de froment", "دقيق القمح", "طحين القمح"),
    ),
    (
        "Whole wheat flour",
        "Whole wheat flour is a plant-derived grain ingredient and is halal-compatible.",
        (
            "whole wheat flour",
            "wholemeal wheat flour",
            "farine complete de ble",
            "farine de ble complet",
            "farine integrale de ble",
            "دقيق القمح الكامل",
        ),
    ),
    (
        "Wheat gluten",
        "Wheat gluten is a plant-derived grain protein and is halal-compatible.",
        ("wheat gluten", "gluten de ble", "غلوتين القمح"),
    ),
    (
        "Corn",
        "Corn (maize) is a plant-derived grain ingredient and is halal-compatible.",
        ("corn", "maize", "mais", "ذرة"),
    ),
    (
        "Corn flour",
        "Corn flour is a plant-derived grain ingredient and is halal-compatible.",
        ("corn flour", "maize flour", "farine de mais", "دقيق الذرة"),
    ),
    (
        "Fermented corn flour",
        "Fermented corn flour is a plant-derived grain ingredient (lactic/yeast fermentation) and is halal-compatible.",
        ("fermented corn flour", "farine de mais fermentee"),
    ),
    (
        "Rapeseed oil",
        "Rapeseed (canola) oil is a plant-derived vegetable oil and is halal-compatible.",
        ("rapeseed oil", "canola oil", "huile de colza", "زيت الكانولا", "زيت اللفت"),
    ),
    (
        "Sunflower oil",
        "Sunflower oil is a plant-derived vegetable oil and is halal-compatible.",
        ("sunflower oil", "huile de tournesol", "زيت دوار الشمس"),
    ),
    (
        "Olive oil",
        "Olive oil is a plant-derived vegetable oil and is halal-compatible.",
        ("olive oil", "huile d'olive", "زيت الزيتون"),
    ),
    (
        "Palm oil",
        "Palm oil is a plant-derived vegetable oil and is halal-compatible.",
        ("palm oil", "huile de palme", "زيت النخيل"),
    ),
    (
        "Salt",
        "Salt is a mineral ingredient and is halal-compatible.",
        ("salt", "sea salt", "table salt", "sel", "sel marin", "sel de table", "ملح"),
    ),
    (
        "Yeast",
        "Baker's yeast is a microbial ingredient and is halal-compatible.",
        ("yeast", "baker's yeast", "levure", "levure de boulanger", "خميرة"),
    ),
    (
        "Acerola",
        "Acerola is a plant-derived fruit ingredient and is halal-compatible.",
        ("acerola", "acerola cherry", "اسيرولا"),
    ),
    (
        "Acerola juice powder",
        "Acerola juice powder is a plant-derived fruit ingredient and is halal-compatible.",
        ("acerola juice powder", "jus d'acerola en poudre", "poudre de jus d'acerola"),
    ),
    (
        "Sugar",
        "Sugar (sucrose) is a plant-derived sweetener and is halal-compatible.",
        ("sugar", "cane sugar", "beet sugar", "sucre", "سكر"),
    ),
)

_AMBIGUOUS_FALLBACK_GROUPS: tuple[tuple[str, IngredientHalalStatus, str, tuple[str, ...]], ...] = (
    (
        "Natural flavoring",
        IngredientHalalStatus.DOUBTFUL,
        "Natural flavorings may use animal-derived components or ethanol carriers; source verification is required.",
        ("natural flavoring", "natural flavouring", "natural flavor", "natural flavour", "arome naturel", "aromes naturels", "نكهة طبيعية"),
    ),
    (
        "Artificial flavoring",
        IngredientHalalStatus.DOUBTFUL,
        "Flavorings may contain animal-derived ingredients or alcohol-based solvents; source verification is required.",
        ("artificial flavoring", "artificial flavouring", "flavoring", "flavouring", "arome", "aromes", "arome artificiel", "نكهة اصطناعية"),
    ),
    (
        "Gelatin",
        IngredientHalalStatus.CONDITIONAL,
        "Gelatin is source-dependent (porcine is haram; bovine requires halal slaughter; fish/plant is halal).",
        ("gelatin", "gelatine", "جيلاتين"),
    ),
    (
        "Enzymes",
        IngredientHalalStatus.DOUBTFUL,
        "Enzymes may be microbial, plant, or animal-derived (e.g. non-halal rennet); source verification is required.",
        ("enzymes", "enzyme", "presure", "rennet", "إنزيمات"),
    ),
    (
        "Glycerin",
        IngredientHalalStatus.CONDITIONAL,
        "Glycerin/glycerol may be plant-derived or animal-derived; source verification is required.",
        ("glycerin", "glycerine", "glycerol", "جلسرين"),
    ),
    (
        "Mono and Diglycerides",
        IngredientHalalStatus.CONDITIONAL,
        "Mono- and diglycerides may be plant-derived or animal-derived; source verification is required.",
        ("mono and diglycerides", "mono & diglycerides", "mono et diglycerides", "mono- et diglycerides d'acides gras"),
    ),
    (
        "Animal fat",
        IngredientHalalStatus.CONDITIONAL,
        "Animal fat may be porcine (haram) or require halal slaughter verification.",
        ("animal fat", "graisse animale", "matiere grasse animale"),
    ),
)


def _build_fallback_index() -> dict[str, _FallbackEntry]:
    index: dict[str, _FallbackEntry] = {}
    for canonical, reason, names in _PLANT_MINERAL_HALAL_GROUPS:
        entry = _FallbackEntry(
            canonical_name=canonical,
            status=IngredientHalalStatus.HALAL,
            reason=f"{reason} (Controlled plant/mineral fallback; does not imply official product halal certification.)",
            warnings=(),
            confidence=0.8,
        )
        for name in names:
            key = normalize_token(name)
            if key:
                index[key] = entry

    for canonical, status, reason, names in _AMBIGUOUS_FALLBACK_GROUPS:
        entry = _FallbackEntry(
            canonical_name=canonical,
            status=status,
            reason=reason,
            warnings=(f"'{canonical}' requires source verification before confirming halal status.",),
            confidence=0.5,
        )
        for name in names:
            key = normalize_token(name)
            if key:
                index[key] = entry

    return index


_FALLBACK_INDEX = _build_fallback_index()


def evaluate_controlled_fallback(normalized: NormalizedIngredient) -> MatchedIngredient | None:
    """Return a deterministic classification for reliably known plant/mineral or ambiguous staples."""
    entry = _FALLBACK_INDEX.get(normalized.normalized_name)
    if entry is None:
        return None
    match_type = "fallback_halal" if entry.status == IngredientHalalStatus.HALAL else "fallback_review"
    return MatchedIngredient(
        normalized=normalized,
        record=None,
        match_type=match_type,
        status=entry.status,
        reason=entry.reason,
        matched_alias=entry.canonical_name,
        review_status=None,
        warnings=entry.warnings,
        evidence=(),
        confidence=entry.confidence,
    )
