from typing import Literal

from pydantic import BaseModel, Field

OCRLanguage = Literal["en", "fr", "ar", "zh_CN"]


class OCRTextRegion(BaseModel):
    text: str
    confidence: float = Field(ge=0, le=1)
    bounding_box: list[list[float]] = Field(default_factory=list)


class OCRIngredientSuggestion(BaseModel):
    original_text: str
    normalized_text: str | None = None
    confidence: float = Field(ge=0, le=1)
    normalization_confidence: float = Field(ge=0, le=1)
    needs_review: bool


class OCRExtractionData(BaseModel):
    extracted_text: list[OCRTextRegion] = Field(default_factory=list)


class OCRExtractionResponse(BaseModel):
    success: bool
    status: Literal["completed"] = "completed"
    message: str = "Text extracted successfully."
    data: OCRExtractionData = Field(default_factory=OCRExtractionData)
    full_text: str
    extracted_text: list[OCRTextRegion]
    language: list[OCRLanguage] = Field(
        default_factory=list,
        description="OCR languages configured for this request; this is not language detection.",
    )
    ingredients: list[OCRIngredientSuggestion] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class OCRFailureResponse(BaseModel):
    success: Literal[False] = False
    status: Literal["failed"] = "failed"
    error_code: str
    message: str
    retryable: bool
