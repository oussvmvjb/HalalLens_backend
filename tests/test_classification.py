from app.models.enums import HalalStatus
from app.services.classification import ClassificationEvidence, determine_status


def test_missing_certification_or_ingredient_evidence_is_not_halal_or_haram() -> None:
    assert determine_status(ClassificationEvidence()) is HalalStatus.UNKNOWN
    assert (
        determine_status(
            ClassificationEvidence(unresolved_source_dependent_ingredient=True)
        )
        is HalalStatus.DOUBTFUL
    )
    assert (
        determine_status(
            ClassificationEvidence(unresolved_source_dependent_ingredient=True)
        )
        is not HalalStatus.HARAM_FLAG
    )


def test_only_verified_evidence_can_produce_definitive_status() -> None:
    assert (
        determine_status(
            ClassificationEvidence(
                verified_certification=True,
                all_ingredients_verified=True,
            )
        )
        is HalalStatus.HALAL_VERIFIED
    )
    assert (
        determine_status(ClassificationEvidence(verified_prohibited_source=True))
        is HalalStatus.HARAM_FLAG
    )
