from app.services.halal_engine.ai_assistant import OptionalAIAssistant
from app.services.halal_engine.analyzer import ANALYSIS_VERSION, HalalIngredientAnalyzer
from app.services.halal_engine.exceptions import (
    AIAssistanceUnavailableError,
    HalalEngineError,
    InvalidIngredientInputError,
    KnowledgeBaseUnavailableError,
)
from app.services.halal_engine.ingredient_matcher import IngredientMatcher
from app.services.halal_engine.knowledge_base import DatabaseKnowledgeBase
from app.services.halal_engine.models import (
    CertificationEvidenceInput,
    EvidenceReference,
    HalalAssessmentResult,
    IngredientRecord,
    MatchedIngredient,
    NormalizedIngredient,
)
from app.services.halal_engine.normalizer import (
    extract_e_number,
    extract_ins_number,
    normalize_ingredient,
    normalize_token,
    split_and_normalize_ingredients,
)

halal_analyzer = HalalIngredientAnalyzer()

__all__ = [
    "ANALYSIS_VERSION",
    "AIAssistanceUnavailableError",
    "CertificationEvidenceInput",
    "DatabaseKnowledgeBase",
    "EvidenceReference",
    "HalalAssessmentResult",
    "HalalEngineError",
    "HalalIngredientAnalyzer",
    "IngredientMatcher",
    "IngredientRecord",
    "InvalidIngredientInputError",
    "KnowledgeBaseUnavailableError",
    "MatchedIngredient",
    "NormalizedIngredient",
    "OptionalAIAssistant",
    "extract_e_number",
    "extract_ins_number",
    "halal_analyzer",
    "normalize_ingredient",
    "normalize_token",
    "split_and_normalize_ingredients",
]
