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
    # --- Common herbs and spices (always plant-derived) ---
    (
        "Black pepper",
        "Black pepper is a plant-derived spice and is halal-compatible.",
        ("black pepper", "pepper", "ground pepper", "white pepper", "poivre noir",
         "poivre blanc", "poivre", "فلفل أسود", "فلفل"),
    ),
    (
        "Parsley",
        "Parsley is a plant-derived herb and is halal-compatible.",
        ("parsley", "dried parsley", "fresh parsley", "chopped parsley",
         "flat-leaf parsley", "persil", "بقدونس"),
    ),
    (
        "Oregano",
        "Oregano is a plant-derived herb and is halal-compatible.",
        ("oregano", "dried oregano", "origan", "أوريغانو"),
    ),
    (
        "Thyme",
        "Thyme is a plant-derived herb and is halal-compatible.",
        ("thyme", "dried thyme", "thym", "زعتر"),
    ),
    (
        "Paprika",
        "Paprika is a plant-derived spice made from dried red peppers and is halal-compatible.",
        ("paprika", "smoked paprika", "sweet paprika", "piment doux", "بابريكا"),
    ),
    (
        "Chili powder",
        "Chili powder is a plant-derived spice blend and is halal-compatible.",
        ("chili powder", "chilli powder", "chile powder", "piment en poudre", "بهارات الفلفل الحار"),
    ),
    (
        "Cayenne pepper",
        "Cayenne pepper is a plant-derived hot spice and is halal-compatible.",
        ("cayenne pepper", "cayenne", "piment de cayenne", "فلفل كايين"),
    ),
    (
        "Garlic powder",
        "Garlic powder is a plant-derived spice and is halal-compatible.",
        ("garlic powder", "garlic", "garlic clove", "garlic cloves", "minced garlic",
         "ail", "ail en poudre", "ail moulu", "ثوم", "مسحوق الثوم"),
    ),
    (
        "Onion powder",
        "Onion powder is a plant-derived spice and is halal-compatible.",
        ("onion powder", "onion", "onions", "oignon", "oignons", "بصل", "مسحوق البصل"),
    ),
    (
        "Cumin",
        "Cumin is a plant-derived spice and is halal-compatible.",
        ("cumin", "ground cumin", "cumin en poudre", "كمون"),
    ),
    (
        "Coriander",
        "Coriander is a plant-derived spice/herb and is halal-compatible.",
        ("coriander", "cilantro", "ground coriander", "coriandre", "كزبرة"),
    ),
    (
        "Turmeric",
        "Turmeric is a plant-derived spice and is halal-compatible.",
        ("turmeric", "ground turmeric", "curcuma", "كركم"),
    ),
    (
        "Cinnamon",
        "Cinnamon is a plant-derived spice and is halal-compatible.",
        ("cinnamon", "ground cinnamon", "cannelle", "قرفة"),
    ),
    (
        "Nutmeg",
        "Nutmeg is a plant-derived spice and is halal-compatible.",
        ("nutmeg", "ground nutmeg", "noix de muscade", "جوزة الطيب"),
    ),
    (
        "Ginger",
        "Ginger is a plant-derived spice and is halal-compatible.",
        ("ginger", "ground ginger", "fresh ginger", "gingembre", "زنجبيل"),
    ),
    (
        "Allspice",
        "Allspice is a plant-derived spice and is halal-compatible.",
        ("allspice", "piment de la jamaïque", "بهار حلو"),
    ),
    # --- Common dairy (halal, source not relevant for dairy itself) ---
    (
        "Cheddar cheese",
        "Cheddar cheese is a dairy product that is generally halal-compatible "
        "(verify rennet source for strict compliance).",
        ("cheddar cheese", "cheddar", "fromage cheddar", "جبن شيدر"),
    ),
    (
        "Mozzarella cheese",
        "Mozzarella cheese is a dairy product that is generally halal-compatible "
        "(verify rennet source for strict compliance).",
        ("mozzarella cheese", "mozzarella", "fromage mozzarella", "جبن موتزاريلا"),
    ),
    (
        "Parmesan cheese",
        "Parmesan is a dairy product that is generally halal-compatible "
        "(verify rennet source for strict compliance).",
        ("parmesan", "parmesan cheese", "parmigiano", "fromage parmesan", "جبن بارميزان"),
    ),
    (
        "Cheese",
        "Cheese is a dairy product; halal status depends on rennet source. Verify before consuming.",
        ("cheese", "fromage", "جبن", "جبنة"),
    ),
    # --- Eggs ---
    (
        "Egg",
        "Eggs are halal-compatible (from chickens or other permissible birds).",
        ("egg", "eggs", "egg white", "egg whites", "egg yolk", "egg yolks",
         "oeuf", "oeufs", "blanc d'oeuf", "jaune d'oeuf", "بيض", "بيضة"),
    ),
    # --- Common vegetables ---
    (
        "Tomato",
        "Tomatoes are a plant-derived ingredient and are halal-compatible.",
        ("tomato", "tomatoes", "tomato paste", "tomato sauce", "tomato purée",
         "tomate", "tomates", "طماطم", "بندورة"),
    ),
    (
        "Lemon",
        "Lemon juice/zest is a plant-derived ingredient and is halal-compatible.",
        ("lemon", "lemon juice", "lemon zest", "citron", "عصير الليمون", "ليمون"),
    ),
    (
        "Ice-cold water",
        "Water is a natural mineral ingredient and is halal-compatible.",
        ("ice-cold water", "ice cold water", "cold water", "iced water"),
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
    # --- Poultry and meat: halal status depends on slaughter method ---
    (
        "Chicken",
        IngredientHalalStatus.CONDITIONAL,
        "Chicken is halal only when slaughtered according to Islamic requirements (zabiha/dhabiha). "
        "Verify the source and slaughter method.",
        (
            "chicken", "ground chicken", "minced chicken", "chicken breast", "chicken thigh",
            "chicken thighs", "chicken wings", "chicken leg", "chicken legs",
            "whole chicken", "chicken pieces", "chicken fillet", "chicken fillets",
            "poulet", "poulet haché", "blanc de poulet", "دجاج", "دجاج مفروم",
        ),
    ),
    (
        "Beef",
        IngredientHalalStatus.CONDITIONAL,
        "Beef is halal only when slaughtered according to Islamic requirements (zabiha/dhabiha). "
        "Verify the source and slaughter method.",
        (
            "beef", "ground beef", "minced beef", "beef mince", "beef steak",
            "beef tenderloin", "beef sirloin", "boeuf", "boeuf haché", "لحم بقر", "لحم مفروم",
        ),
    ),
    (
        "Lamb",
        IngredientHalalStatus.CONDITIONAL,
        "Lamb is halal only when slaughtered according to Islamic requirements (zabiha/dhabiha). "
        "Verify the source and slaughter method.",
        (
            "lamb", "ground lamb", "minced lamb", "lamb mince", "lamb chop", "lamb chops",
            "agneau", "agneau haché", "لحم غنم", "لحم ضأن",
        ),
    ),
    (
        "Turkey",
        IngredientHalalStatus.CONDITIONAL,
        "Turkey is halal only when slaughtered according to Islamic requirements. "
        "Verify the source and slaughter method.",
        (
            "turkey", "ground turkey", "minced turkey", "turkey breast",
            "dinde", "dinde hachée", "ديك رومي",
        ),
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
