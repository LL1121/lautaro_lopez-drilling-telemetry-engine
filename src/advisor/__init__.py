"""LLM Advisor — trigger SSI>1.0, prompts SOP y proveedores desacoplados."""

from src.advisor.llm_diagnostics import (
    AnthropicProvider,
    DeterministicMockLLMProvider,
    DrillingAdvisor,
    GroqProvider,
    LLMRequest,
    OpenAIProvider,
    build_default_advisor,
)
from src.advisor.schemas import (
    SAFE_RPM_RANGE,
    SAFE_WOB_RANGE_KN,
    AdvisorIncidentSnapshot,
    AdvisorRecommendation,
)

__all__ = [
    "SAFE_RPM_RANGE",
    "SAFE_WOB_RANGE_KN",
    "AdvisorIncidentSnapshot",
    "AdvisorRecommendation",
    "AnthropicProvider",
    "DeterministicMockLLMProvider",
    "DrillingAdvisor",
    "GroqProvider",
    "LLMRequest",
    "OpenAIProvider",
    "build_default_advisor",
]
