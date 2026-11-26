class HalalEngineError(Exception):
    """Base exception for the halal ingredient analysis engine."""


class KnowledgeBaseUnavailableError(HalalEngineError):
    """Raised when the ingredient knowledge base cannot be queried."""


class InvalidIngredientInputError(HalalEngineError):
    """Raised when ingredient input cannot be parsed safely."""


class AIAssistanceUnavailableError(HalalEngineError):
    """Raised when optional AI assistance fails or returns invalid output."""
