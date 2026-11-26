from enum import Enum


class RiskCategory(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    SOURCE_DEPENDENT = "SOURCE_DEPENDENT"
    UNKNOWN = "UNKNOWN"


class HalalStatus(str, Enum):
    HALAL_VERIFIED = "HALAL_VERIFIED"
    HARAM_FLAG = "HARAM_FLAG"
    DOUBTFUL = "DOUBTFUL"
    UNKNOWN = "UNKNOWN"


class VerificationStatus(str, Enum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    PENDING = "PENDING"


class AuthorityStatus(str, Enum):
    RECOGNIZED = "RECOGNIZED"
    UNRECOGNIZED = "UNRECOGNIZED"
    PENDING = "PENDING"
    REVOKED = "REVOKED"


class ScanStatus(str, Enum):
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ScanType(str, Enum):
    BARCODE = "BARCODE"
    IMAGE = "IMAGE"


class EvidenceType(str, Enum):
    INGREDIENT = "INGREDIENT"
    CERTIFICATION = "CERTIFICATION"
    SOURCE = "SOURCE"
    USER_REPORT = "USER_REPORT"
    OTHER = "OTHER"


class ReportType(str, Enum):
    INCORRECT_INGREDIENT = "INCORRECT_INGREDIENT"
    CERTIFICATION = "CERTIFICATION"
    PRODUCT_DATA = "PRODUCT_DATA"
    OTHER = "OTHER"


class ReportStatus(str, Enum):
    OPEN = "OPEN"
    UNDER_REVIEW = "UNDER_REVIEW"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"


class IngredientHalalStatus(str, Enum):
    HALAL = "halal"
    HARAM = "haram"
    DOUBTFUL = "doubtful"
    CONDITIONAL = "conditional"
    UNKNOWN = "unknown"


class ProductHalalVerdict(str, Enum):
    HALAL_VERIFIED = "halal_verified"
    LIKELY_HALAL = "likely_halal"
    HARAM_DETECTED = "haram_detected"
    DOUBTFUL = "doubtful"
    INSUFFICIENT_INFORMATION = "insufficient_information"


class EvidenceReviewStatus(str, Enum):
    VERIFIED = "verified"
    PENDING = "pending"
    PENDING_REVIEW = "pending_review"
    DISPUTED = "disputed"
    REJECTED = "rejected"
