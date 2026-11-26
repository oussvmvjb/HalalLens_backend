"""
Optional AI assistant for HalalLens halal engine.

SAFETY CONTRACT
---------------
* Never makes or overrides halal/haram decisions.
* Never invents sources, certifications, or ingredient classifications.
* Always returns None gracefully when the AI is unavailable or the response
  is below minimum confidence.
* classify_unknown_ingredient() returns a *category hint* (plant/animal/
  synthetic/source_dependent/unknown), NOT a halal verdict.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.core.config import get_settings
from app.services.halal_engine.exceptions import AIAssistanceUnavailableError

logger = logging.getLogger(__name__)

IngredientCategory = Literal["plant", "animal", "synthetic", "source_dependent", "unknown"]


class AIExtractedIngredients(BaseModel):
    ingredients: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    manual_review_required: bool = True


class AIEngineExplanation(BaseModel):
    summary: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    manual_review_required: bool = False


class AIIngredientCategory(BaseModel):
    """Category hint returned by classify_unknown_ingredient.

    Does NOT carry a halal/haram verdict. Used only as a hint to decide
    whether to return UNKNOWN or DOUBTFUL for unlisted ingredients.
    """

    category: IngredientCategory = "unknown"
    likely_haram: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reasoning: str = ""


class OptionalAIAssistant:
    """Optional AI helper for messy OCR extraction and explanation formatting.

    Never makes or overrides halal/haram decisions and never invents sources.
    """

    async def extract_ingredients_from_ocr(
        self,
        ocr_text: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> AIExtractedIngredients | None:
        settings = get_settings()
        api_key = getattr(settings, "openai_api_key", None)
        if not api_key or not ocr_text.strip():
            return None

        payload = {
            "model": "gpt-4o-mini",
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Extract only the ingredient list from noisy OCR text as JSON "
                        '{"ingredients": ["..."], "confidence": 0.0..1.0, '
                        '"manual_review_required": true|false}. Do not classify halal/haram.'
                    ),
                },
                {"role": "user", "content": ocr_text},
            ],
        }
        try:
            raw = await self._post_json(
                "https://api.openai.com/v1/chat/completions",
                api_key=api_key,
                payload=payload,
                client=client,
            )
            content = raw["choices"][0]["message"]["content"]
            parsed = AIExtractedIngredients.model_validate_json(content)
            if parsed.confidence < 0.6:
                parsed.manual_review_required = True
            return parsed
        except (
            AIAssistanceUnavailableError,
            httpx.HTTPError,
            KeyError,
            IndexError,
            TypeError,
            ValidationError,
        ):
            logger.warning("Optional AI OCR extraction unavailable; continuing deterministically")
            return None

    async def classify_unknown_ingredient(
        self,
        ingredient_name: str,
        *,
        context: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> AIIngredientCategory | None:
        """Return a category hint for an ingredient not found in the database.

        Returns None when AI is unavailable or confidence is too low.
        The caller must NOT use this result alone to declare an ingredient halal
        or haram — it is a hint only.
        """
        settings = get_settings()
        api_key = getattr(settings, "openai_api_key", None)
        if not api_key or not ingredient_name.strip():
            return None

        context_note = f" Context from product label: {context}" if context else ""
        payload = {
            "model": "gpt-4o-mini",
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a food science assistant. Categorise the given food ingredient "
                        "by its likely origin. Reply ONLY with JSON: "
                        '{"category": "plant|animal|synthetic|source_dependent|unknown", '
                        '"likely_haram": false, "confidence": 0.0..1.0, "reasoning": "..."}. '
                        "Do NOT make a halal/haram ruling. "
                        "'likely_haram' should be true only if the ingredient name strongly "
                        "implies pork, blood, alcohol, or non-halal animal slaughter origin "
                        "and you are very confident (confidence >= 0.9). "
                        "When unsure, use category='unknown' and likely_haram=false."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Ingredient: {ingredient_name}{context_note}",
                },
            ],
        }
        try:
            raw = await self._post_json(
                "https://api.openai.com/v1/chat/completions",
                api_key=api_key,
                payload=payload,
                client=client,
            )
            content = raw["choices"][0]["message"]["content"]
            result = AIIngredientCategory.model_validate_json(content)
            # Safety floor: never trust low-confidence haram hints
            if result.likely_haram and result.confidence < 0.9:
                result.likely_haram = False
            if result.confidence < 0.5:
                return None
            return result
        except (
            AIAssistanceUnavailableError,
            httpx.HTTPError,
            KeyError,
            IndexError,
            TypeError,
            ValidationError,
        ):
            logger.warning(
                "Optional AI ingredient categorisation unavailable for %r", ingredient_name
            )
            return None

    async def _post_json(
        self,
        url: str,
        *,
        api_key: str,
        payload: dict[str, Any],
        client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        try:
            if client is not None:
                resp = await client.post(url, headers=headers, json=payload)
            else:
                async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as http_client:
                    resp = await http_client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                raise AIAssistanceUnavailableError("AI response is not a JSON object")
            return data
        except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as error:
            raise AIAssistanceUnavailableError("AI request failed") from error
