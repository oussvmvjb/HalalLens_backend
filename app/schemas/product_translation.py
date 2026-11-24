from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

TranslationLanguage = Literal["en", "zh-CN", "fr", "ar"]
TranslationText = Annotated[str, Field(max_length=4000)]


class ProductDetailsText(BaseModel):
    product_name: TranslationText | None = None
    brand: TranslationText | None = None
    ingredients: list[TranslationText] = Field(default_factory=list, max_length=100)
    categories: list[TranslationText] = Field(default_factory=list, max_length=100)
    origin_description: TranslationText | None = None
    labels: list[TranslationText] = Field(default_factory=list, max_length=100)
    certification_issuers: list[TranslationText] = Field(
        default_factory=list, max_length=100
    )
    analysis_explanation: TranslationText

    @model_validator(mode="after")
    def enforce_translation_limits(self) -> "ProductDetailsText":
        texts = [
            value
            for value in (
                self.product_name,
                self.brand,
                self.origin_description,
                self.analysis_explanation,
                *self.ingredients,
                *self.categories,
                *self.labels,
                *self.certification_issuers,
            )
            if value
        ]
        if len(texts) > 128:
            raise ValueError("A product translation can contain at most 128 text fields.")
        if sum(map(len, texts)) > 25_000:
            raise ValueError("A product translation cannot exceed 25,000 characters.")
        return self


class ProductDetailsTranslationRequest(BaseModel):
    target_language: TranslationLanguage
    details: ProductDetailsText


class ProductDetailsTranslationResponse(BaseModel):
    target_language: TranslationLanguage
    details: ProductDetailsText


class ProductTranslationFailure(BaseModel):
    success: Literal[False] = False
    error_code: str
    message: str
    retryable: bool
