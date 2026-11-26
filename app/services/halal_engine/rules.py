from app.models.enums import (
    EvidenceReviewStatus,
    HalalStatus,
    IngredientHalalStatus,
    ProductHalalVerdict,
)
from app.services.halal_engine.fallback import evaluate_controlled_fallback
from app.services.halal_engine.models import (
    CertificationEvidenceInput,
    HalalRuleRecord,
    IngredientRecord,
    MatchedIngredient,
    NormalizedIngredient,
)

_PROHIBITED_QUALIFIERS = ("pork", "porcine", "swine", "lard", "bacon", "ham", "ethanol", "wine", "rum")
_PERMITTED_QUALIFIERS = ("plant", "vegetable", "botanical", "synthetic", "microbial", "halal")


def evaluate_ingredient(
    normalized: NormalizedIngredient,
    record: IngredientRecord | None,
    match_type: str | None,
) -> MatchedIngredient:
    """Apply deterministic halal rules to a single normalized/matched ingredient."""
    if record is None:
        fallback = evaluate_controlled_fallback(normalized)
        if fallback is not None:
            return fallback
        return MatchedIngredient(
            normalized=normalized,
            record=None,
            match_type=None,
            status=IngredientHalalStatus.UNKNOWN,
            reason="No matching ingredient record with supporting evidence was found in the database.",
            warnings=("Unrecognized ingredient; cannot be assumed halal without evidence.",),
            evidence=(),
            confidence=0.0,
        )

    active_sources = tuple(
        src for src in record.sources if src.review_status != EvidenceReviewStatus.REJECTED
    )
    disputed_sources = tuple(
        src for src in active_sources if src.review_status == EvidenceReviewStatus.DISPUTED
    )
    verified_sources = tuple(
        src for src in active_sources if src.review_status == EvidenceReviewStatus.VERIFIED
    )
    primary_review_status = (
        EvidenceReviewStatus.VERIFIED
        if verified_sources
        else (active_sources[0].review_status if active_sources else None)
    )
    is_pending_review = primary_review_status in {
        EvidenceReviewStatus.PENDING,
        EvidenceReviewStatus.PENDING_REVIEW,
    }
    matched_alias = (
        normalized.e_number
        if match_type == "e_number"
        else (
            normalized.ins_number
            if match_type == "ins_number"
            else (normalized.original_text if match_type in {"alias", "translation"} else None)
        )
    )
    warnings: list[str] = []

    if disputed_sources and record.halal_status in {
        IngredientHalalStatus.HALAL,
        IngredientHalalStatus.CONDITIONAL,
    }:
        warnings.append(
            f"Conflicting or disputed evidence exists for '{record.canonical_name}'."
        )
        return MatchedIngredient(
            normalized=normalized,
            record=record,
            match_type=match_type,
            status=IngredientHalalStatus.DOUBTFUL,
            reason=(
                f"Matched '{record.canonical_name}' ({match_type}), but supporting evidence "
                "contains disputed records requiring review."
            ),
            matched_alias=matched_alias,
            review_status=EvidenceReviewStatus.DISPUTED,
            warnings=tuple(warnings),
            evidence=active_sources,
            confidence=min(record.confidence_level, 0.5),
        )

    if record.halal_status == IngredientHalalStatus.HARAM:
        conf = record.confidence_level or 0.9
        if is_pending_review:
            conf = min(conf, 0.65)
            warnings.append(
                f"'{record.canonical_name}' is flagged from reference data and is pending expert review."
            )
        return MatchedIngredient(
            normalized=normalized,
            record=record,
            match_type=match_type,
            status=IngredientHalalStatus.HARAM,
            reason=record.description
            or f"'{record.canonical_name}' is classified as haram based on database evidence.",
            matched_alias=matched_alias,
            review_status=primary_review_status,
            warnings=tuple(warnings),
            evidence=active_sources,
            confidence=conf,
        )

    if record.halal_status == IngredientHalalStatus.CONDITIONAL:
        qualifier = (normalized.source_qualifier or "").lower()
        if any(term in qualifier for term in _PROHIBITED_QUALIFIERS):
            return MatchedIngredient(
                normalized=normalized,
                record=record,
                match_type=match_type,
                status=IngredientHalalStatus.HARAM,
                reason=(
                    f"'{record.canonical_name}' is source-dependent and its label qualifier "
                    f"('{normalized.source_qualifier}') indicates a prohibited origin."
                ),
                matched_alias=matched_alias,
                review_status=primary_review_status,
                warnings=tuple(warnings),
                evidence=active_sources,
                confidence=max(record.confidence_level, 0.85),
            )
        if any(term in qualifier for term in _PERMITTED_QUALIFIERS) and verified_sources:
            return MatchedIngredient(
                normalized=normalized,
                record=record,
                match_type=match_type,
                status=IngredientHalalStatus.HALAL,
                reason=(
                    f"'{record.canonical_name}' is conditional, and its declared qualifier "
                    f"('{normalized.source_qualifier}') satisfies source requirements."
                ),
                matched_alias=matched_alias,
                review_status=primary_review_status,
                warnings=tuple(warnings),
                evidence=active_sources,
                confidence=record.confidence_level or 0.8,
            )

        req = record.source_requirements or "Source origin must be verified."
        warnings.append(
            f"'{record.canonical_name}' is conditional/source-dependent: {req}"
        )
        if is_pending_review:
            warnings.append(
                f"'{record.canonical_name}' is pending expert review."
            )
        return MatchedIngredient(
            normalized=normalized,
            record=record,
            match_type=match_type,
            status=IngredientHalalStatus.CONDITIONAL,
            reason=f"'{record.canonical_name}' requires source verification: {req}",
            matched_alias=matched_alias,
            review_status=primary_review_status,
            warnings=tuple(warnings),
            evidence=active_sources,
            confidence=min(record.confidence_level or 0.6, 0.6)
            if is_pending_review
            else (record.confidence_level or 0.6),
        )

    if record.halal_status == IngredientHalalStatus.DOUBTFUL:
        warnings.append(f"'{record.canonical_name}' is classified as doubtful (mashbooh).")
        if is_pending_review:
            warnings.append(f"'{record.canonical_name}' is pending expert review.")
        return MatchedIngredient(
            normalized=normalized,
            record=record,
            match_type=match_type,
            status=IngredientHalalStatus.DOUBTFUL,
            reason=record.description
            or f"'{record.canonical_name}' is doubtful and requires further verification.",
            matched_alias=matched_alias,
            review_status=primary_review_status,
            warnings=tuple(warnings),
            evidence=active_sources,
            confidence=record.confidence_level or 0.5,
        )

    if record.halal_status == IngredientHalalStatus.HALAL:
        if is_pending_review:
            warnings.append(
                f"'{record.canonical_name}' is pending expert review and cannot be treated as verified halal."
            )
            return MatchedIngredient(
                normalized=normalized,
                record=record,
                match_type=match_type,
                status=IngredientHalalStatus.CONDITIONAL,
                reason=(
                    f"'{record.canonical_name}' is marked halal in draft reference data "
                    "but is pending expert review."
                ),
                matched_alias=matched_alias,
                review_status=primary_review_status,
                warnings=tuple(warnings),
                evidence=active_sources,
                confidence=0.5,
            )
        if not active_sources and record.confidence_level <= 0.0:
            warnings.append(
                f"'{record.canonical_name}' has no traceable supporting evidence."
            )
            return MatchedIngredient(
                normalized=normalized,
                record=record,
                match_type=match_type,
                status=IngredientHalalStatus.UNKNOWN,
                reason=(
                    f"'{record.canonical_name}' lacks traceable evidence; "
                    "marked unknown for safety."
                ),
                matched_alias=matched_alias,
                review_status=None,
                warnings=tuple(warnings),
                evidence=(),
                confidence=0.0,
            )
        return MatchedIngredient(
            normalized=normalized,
            record=record,
            match_type=match_type,
            status=IngredientHalalStatus.HALAL,
            reason=record.description
            or f"'{record.canonical_name}' is classified as halal with traceable evidence.",
            matched_alias=matched_alias,
            review_status=primary_review_status,
            warnings=tuple(warnings),
            evidence=active_sources,
            confidence=record.confidence_level or 0.9,
        )

    return MatchedIngredient(
        normalized=normalized,
        record=record,
        match_type=match_type,
        status=IngredientHalalStatus.UNKNOWN,
        reason=record.description
        or f"'{record.canonical_name}' currently has unknown halal status.",
        matched_alias=matched_alias,
        review_status=primary_review_status,
        warnings=("Ingredient status is unknown; manual verification recommended.",),
        evidence=active_sources,
        confidence=0.0,
    )


def determine_product_verdict(
    classifications: list[MatchedIngredient],
    certifications: list[CertificationEvidenceInput],
    active_rules: list[HalalRuleRecord] | None = None,
) -> tuple[ProductHalalVerdict, HalalStatus, str, float | None, bool, list[str]]:
    """Compute product-level verdict, legacy HalalStatus, explanation, and warnings."""
    _ = active_rules  # Reserved for database-configured priority overrides
    certification_verified = any(
        cert.is_recognized_issuer and cert.is_verified and not cert.is_expired
        for cert in certifications
    )
    warnings: list[str] = []
    for item in classifications:
        for warning in item.warnings:
            if warning not in warnings:
                warnings.append(warning)

    if not classifications:
        warnings.append("No ingredients were provided for analysis.")
        return (
            ProductHalalVerdict.INSUFFICIENT_INFORMATION,
            HalalStatus.UNKNOWN,
            "No ingredients were available to analyze. Scan or enter the ingredient list.",
            None,
            certification_verified,
            warnings,
        )

    statuses = [item.status for item in classifications]
    if IngredientHalalStatus.HARAM in statuses:
        haram_names = [
            item.record.canonical_name if item.record else item.normalized.original_text
            for item in classifications
            if item.status == IngredientHalalStatus.HARAM
        ]
        return (
            ProductHalalVerdict.HARAM_DETECTED,
            HalalStatus.HARAM_FLAG,
            f"Haram ingredient(s) detected: {', '.join(haram_names)}.",
            0.95,
            certification_verified,
            warnings,
        )

    if (
        IngredientHalalStatus.DOUBTFUL in statuses
        or IngredientHalalStatus.CONDITIONAL in statuses
    ):
        flagged = [
            item.record.canonical_name if item.record else item.normalized.original_text
            for item in classifications
            if item.status in {IngredientHalalStatus.DOUBTFUL, IngredientHalalStatus.CONDITIONAL}
        ]
        unknown_names = [
            item.normalized.original_text
            for item in classifications
            if item.status == IngredientHalalStatus.UNKNOWN
        ]
        explanation = f"Source-dependent or doubtful ingredient(s) found: {', '.join(flagged)}."
        if unknown_names:
            explanation += f" Unrecognized ingredient(s) also require verification: {', '.join(unknown_names)}."
        return (
            ProductHalalVerdict.DOUBTFUL,
            HalalStatus.DOUBTFUL,
            explanation,
            0.55,
            certification_verified,
            warnings,
        )

    if IngredientHalalStatus.UNKNOWN in statuses:
        unknown_names = [
            item.normalized.original_text
            for item in classifications
            if item.status == IngredientHalalStatus.UNKNOWN
        ]
        return (
            ProductHalalVerdict.INSUFFICIENT_INFORMATION,
            HalalStatus.UNKNOWN,
            (
                "Insufficient evidence to confirm halal status; unrecognized ingredient(s): "
                f"{', '.join(unknown_names)}."
            ),
            0.25,
            certification_verified,
            warnings,
        )

    avg_confidence = sum(item.confidence for item in classifications) / len(classifications)
    if certification_verified:
        return (
            ProductHalalVerdict.HALAL_VERIFIED,
            HalalStatus.HALAL_VERIFIED,
            "All analyzed ingredients are halal and a recognized halal certification is verified.",
            round(avg_confidence, 3),
            True,
            warnings,
        )

    warnings.append(
        "Ingredient analysis shows halal ingredients, but no verified halal product certification is on record."
    )
    return (
        ProductHalalVerdict.LIKELY_HALAL,
        HalalStatus.UNKNOWN,
        "All analyzed ingredients are halal based on current evidence; no official product certification is verified.",
        round(avg_confidence, 3),
        False,
        warnings,
    )
