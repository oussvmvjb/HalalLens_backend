from dataclasses import dataclass

from app.models.enums import HalalStatus


@dataclass(frozen=True)
class ClassificationEvidence:
    verified_certification: bool = False
    all_ingredients_verified: bool = False
    verified_prohibited_source: bool = False
    unresolved_source_dependent_ingredient: bool = False


def determine_status(evidence: ClassificationEvidence) -> HalalStatus:
    if evidence.verified_prohibited_source:
        return HalalStatus.HARAM_FLAG
    if evidence.unresolved_source_dependent_ingredient:
        return HalalStatus.DOUBTFUL
    if evidence.verified_certification and evidence.all_ingredients_verified:
        return HalalStatus.HALAL_VERIFIED
    return HalalStatus.UNKNOWN
