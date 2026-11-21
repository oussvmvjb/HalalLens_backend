from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    EvidenceType,
    HalalStatus,
    Scan,
    ScanEvidence,
    ScanResult,
    ScanStatus,
    VerificationStatus,
)


async def analyze_scan(
    db: AsyncSession,
    scan: Scan,
    *,
    original_ingredients_text: str | None,
    detected_language: str | None,
) -> ScanResult:
    if original_ingredients_text is not None:
        scan.original_ingredients_text = original_ingredients_text
    if detected_language is not None:
        scan.detected_language = detected_language
    scan.scan_status = ScanStatus.COMPLETED
    result = ScanResult(
        scan_id=scan.id,
        overall_status=HalalStatus.UNKNOWN,
        confidence_score=None,
        explanation=(
            "Automated ingredient and certification analysis is not enabled. "
            "No religious ruling was generated; verify the product with trusted sources."
        ),
        analysis_version="foundation-v1",
        evidence=[
            ScanEvidence(
                evidence_type=EvidenceType.OTHER,
                evidence_description=(
                    "Analysis service is a placeholder; no ingredient or certification evidence "
                    "was evaluated."
                ),
                source_name="HalalLens foundation",
                verification_status=VerificationStatus.UNVERIFIED,
            )
        ],
    )
    db.add(result)
    await db.commit()
    await db.refresh(result)
    return result


async def load_scan(db: AsyncSession, scan_id: UUID) -> Scan | None:
    return await db.get(Scan, scan_id)
