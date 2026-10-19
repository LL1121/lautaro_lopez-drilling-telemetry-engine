"""Orquestador del Advisor LLM: debounce, proveedores y evaluación asíncrona."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import ValidationError

from src.advisor.prompts.drilling_sop import (
    build_response_schema,
    build_system_prompt,
    build_user_prompt,
)
from src.advisor.schemas import (
    SAFE_RPM_RANGE,
    SAFE_WOB_RANGE_KN,
    AdvisorIncidentSnapshot,
    AdvisorRecommendation,
    IncidentType,
    SeverityLevel,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LLMRequest:
    """Pedido tipado al proveedor LLM.

    Parameters
    ----------
    system_prompt : str
        Prompt de sistema (SOP fijo).
    user_prompt : str
        Prompt de usuario (features sanitizados).
    response_schema : dict[str, object]
        JSON Schema de salida estructurada.
    snapshot : AdvisorIncidentSnapshot
        Snapshot tipado (solo lo usa el mock determinista).
    """

    system_prompt: str
    user_prompt: str
    response_schema: dict[str, object]
    snapshot: AdvisorIncidentSnapshot


class LLMProviderProtocol(Protocol):
    """Contrato de proveedor LLM (async, retorna JSON crudo)."""

    async def generate(self, request: LLMRequest) -> str:
        """Genera la respuesta JSON del modelo."""
        ...


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


class DeterministicMockLLMProvider:
    """Proveedor sin red/credenciales; reglas fijas sobre el snapshot.

    Mismo input → mismo output (sin RNG). Usado en CI y demos locales.
    """

    async def generate(self, request: LLMRequest) -> str:
        snap = request.snapshot
        incident_type, severity = self._classify(snap)
        target_wob = _clamp(snap.wob_kn * 0.85, SAFE_WOB_RANGE_KN[0], SAFE_WOB_RANGE_KN[1])
        # Empujar RPM fuera de banda de resonancia: +10% acotado.
        target_rpm = _clamp(
            snap.surface_rpm * 1.10,
            SAFE_RPM_RANGE[0],
            SAFE_RPM_RANGE[1],
        )
        actions = self._actions(incident_type)
        rec = AdvisorRecommendation(
            incident_type=incident_type,
            severity_level=severity,
            physical_root_cause=self._root_cause(incident_type),
            immediate_actions=actions,
            target_wob_kn=target_wob,
            target_rpm=target_rpm,
            rationale=(
                f"Mock SOP {incident_type}: SSI={snap.ssi:.3f}, "
                f"bit_rpm={snap.estimated_bit_rpm:.1f}, wob={snap.wob_kn:.1f}."
            ),
        )
        return rec.model_dump_json()

    @staticmethod
    def _classify(snap: AdvisorIncidentSnapshot) -> tuple[IncidentType, SeverityLevel]:
        severity: SeverityLevel = "critical" if snap.ssi > 1.0 else "warning"
        if snap.ssi > 1.0 and snap.estimated_bit_rpm < 0.4 * max(snap.surface_rpm, 1.0):
            return "stick_slip", severity
        if abs(snap.torque_contrast) > 8.0:
            return "over_torque", severity
        if snap.regime == "warning":
            return "transient_choke", "warning"
        if snap.ssi > 1.0:
            return "stick_slip", severity
        return "unknown", severity

    @staticmethod
    def _root_cause(incident_type: IncidentType) -> str:
        mapping: dict[IncidentType, str] = {
            "stick_slip": (
                "Torsional stick-slip: static friction at bit exceeds drive torque, "
                "releasing into high-slip RPM cycles."
            ),
            "over_torque": (
                "Surface-to-bit torque contrast indicates over-torque / trapped energy "
                "in the drillstring."
            ),
            "transient_choke": (
                "Transient choke-like friction increase elevating SSI into warning band."
            ),
            "unknown": "Unclassified torsional anomaly from validated numeric features.",
        }
        return mapping[incident_type]

    @staticmethod
    def _actions(incident_type: IncidentType) -> list[str]:
        mapping: dict[IncidentType, list[str]] = {
            "stick_slip": [
                "Reduce WOB gradually toward target_wob_kn",
                "Adjust surface RPM toward target_rpm to exit resonance",
                "Monitor SSI until below 0.5 before increasing WOB",
            ],
            "over_torque": [
                "Ease off WOB to relieve trapped torque",
                "Hold or slightly raise RPM while torque decays",
                "Verify standpipe pressure for cuttings pack-off indicators",
            ],
            "transient_choke": [
                "Hold WOB steady; avoid aggressive increases",
                "Fine-tune RPM ±5% around target_rpm",
            ],
            "unknown": [
                "Hold parameters; re-evaluate after next SSI window",
            ],
        }
        return mapping[incident_type]


class OpenAIProvider:
    """Adaptador OpenAI con import diferido (opcional fuera de CI)."""

    def __init__(self, model: str = "gpt-4o-mini") -> None:
        self.model = model
        self._api_key = os.environ.get("OPENAI_API_KEY", "")

    async def generate(self, request: LLMRequest) -> str:
        if not self._api_key:
            msg = "OPENAI_API_KEY is not set"
            raise RuntimeError(msg)
        try:
            from openai import AsyncOpenAI  # type: ignore[import-not-found]
        except ImportError as exc:
            msg = "openai package is not installed"
            raise RuntimeError(msg) from exc

        client = AsyncOpenAI(api_key=self._api_key)
        response = await client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
        if not isinstance(content, str) or not content:
            msg = "OpenAI returned empty content"
            raise RuntimeError(msg)
        return content


class GroqProvider:
    """Adaptador Groq con import diferido (opcional fuera de CI)."""

    def __init__(self, model: str = "llama-3.3-70b-versatile") -> None:
        self.model = model
        self._api_key = os.environ.get("GROQ_API_KEY", "")

    async def generate(self, request: LLMRequest) -> str:
        if not self._api_key:
            msg = "GROQ_API_KEY is not set"
            raise RuntimeError(msg)
        try:
            from groq import AsyncGroq  # type: ignore[import-not-found]
        except ImportError as exc:
            msg = "groq package is not installed"
            raise RuntimeError(msg) from exc

        client = AsyncGroq(api_key=self._api_key)
        response = await client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
        if not isinstance(content, str) or not content:
            msg = "Groq returned empty content"
            raise RuntimeError(msg)
        return content


class AnthropicProvider:
    """Adaptador Anthropic con import diferido (opcional fuera de CI)."""

    def __init__(self, model: str = "claude-3-5-haiku-latest") -> None:
        self.model = model
        self._api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    async def generate(self, request: LLMRequest) -> str:
        if not self._api_key:
            msg = "ANTHROPIC_API_KEY is not set"
            raise RuntimeError(msg)
        try:
            from anthropic import AsyncAnthropic  # type: ignore[import-not-found]
        except ImportError as exc:
            msg = "anthropic package is not installed"
            raise RuntimeError(msg) from exc

        client = AsyncAnthropic(api_key=self._api_key)
        schema_hint = json.dumps(request.response_schema)
        message = await client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=request.system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"{request.user_prompt}\n\n"
                        f"Return ONLY JSON matching this schema:\n{schema_hint}"
                    ),
                }
            ],
        )
        blocks = message.content
        if not blocks:
            msg = "Anthropic returned empty content"
            raise RuntimeError(msg)
        first = blocks[0]
        text = getattr(first, "text", None)
        if not isinstance(text, str) or not text:
            msg = "Anthropic returned non-text content"
            raise RuntimeError(msg)
        return text


@dataclass
class DrillingAdvisor:
    """Trigger SSI>1.0 con debounce/cooldown y proveedor desacoplado.

    Parameters
    ----------
    provider : LLMProviderProtocol
        Backend LLM (mock en CI).
    cooldown_sec : float
        Mínimo entre emisiones ante evento sostenido [s].
    ssi_trigger_threshold : float
        Umbral de disparo (SPEC: SSI > 1.0).
    request_timeout_sec : float
        Timeout del ``provider.generate`` [s].
    clock : Callable[[], float]
        Reloj monotónico inyectable (tests).
    """

    provider: LLMProviderProtocol
    cooldown_sec: float = 30.0
    ssi_trigger_threshold: float = 1.0
    request_timeout_sec: float = 5.0
    clock: Callable[[], float] = field(default=time.monotonic)
    _in_incident: bool = field(default=False, init=False, repr=False)
    _last_emitted_monotonic: float | None = field(default=None, init=False, repr=False)

    async def evaluate_telemetry(
        self,
        snapshot: AdvisorIncidentSnapshot,
    ) -> AdvisorRecommendation | None:
        """Evalúa un snapshot; retorna recomendación o ``None`` (suprimido/error).

        Nunca propaga excepciones del proveedor ni bloquea indefinidamente.
        """
        if snapshot.ssi <= self.ssi_trigger_threshold:
            self._in_incident = False
            return None

        now = self.clock()
        if self._in_incident:
            if self._last_emitted_monotonic is not None:
                elapsed = now - self._last_emitted_monotonic
                if elapsed < self.cooldown_sec:
                    return None

        request = LLMRequest(
            system_prompt=build_system_prompt(),
            user_prompt=build_user_prompt(snapshot),
            response_schema=build_response_schema(),
            snapshot=snapshot,
        )
        try:
            raw = await asyncio.wait_for(
                self.provider.generate(request),
                timeout=self.request_timeout_sec,
            )
            recommendation = AdvisorRecommendation.model_validate_json(raw)
        except (TimeoutError, ValidationError, ValueError, RuntimeError, OSError) as exc:
            logger.warning("advisor generate failed: %s", exc)
            return None
        except Exception as exc:  # noqa: BLE001 — never break physics loop
            logger.warning("advisor unexpected failure: %s", exc)
            return None

        self._in_incident = True
        self._last_emitted_monotonic = self.clock()
        return recommendation


_LLM_PROVIDER_MODULES: dict[str, str] = {
    "openai": "openai",
    "groq": "groq",
    "anthropic": "anthropic",
}

_LLM_PROVIDER_KEY_ENV: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


def build_default_advisor() -> DrillingAdvisor:
    """Selecciona el proveedor LLM desde el entorno.

    ``LLM_PROVIDER`` acepta ``mock`` (default), ``openai``, ``groq`` o
    ``anthropic``. Si el nombre es desconocido, falta la API key o el SDK
    no está instalado, el advisor usa el mock determinista y la API sigue
    en servicio (SOP fijo, sin red).
    """
    provider_name = os.environ.get("LLM_PROVIDER", "mock").strip().lower()
    if provider_name in {"", "mock", "deterministic"}:
        return DrillingAdvisor(provider=DeterministicMockLLMProvider())

    module_name = _LLM_PROVIDER_MODULES.get(provider_name)
    key_env = _LLM_PROVIDER_KEY_ENV.get(provider_name)
    if module_name is None or key_env is None:
        logger.warning(
            "unknown LLM_PROVIDER=%s; using deterministic mock",
            provider_name,
        )
        return DrillingAdvisor(provider=DeterministicMockLLMProvider())

    api_key = os.environ.get(key_env, "").strip()
    if not api_key:
        logger.warning(
            "LLM_PROVIDER=%s but %s is unset; using deterministic mock",
            provider_name,
            key_env,
        )
        return DrillingAdvisor(provider=DeterministicMockLLMProvider())

    if importlib.util.find_spec(module_name) is None:
        logger.warning(
            "LLM_PROVIDER=%s but package %s is not installed; using deterministic mock",
            provider_name,
            module_name,
        )
        return DrillingAdvisor(provider=DeterministicMockLLMProvider())

    if provider_name == "openai":
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        return DrillingAdvisor(provider=OpenAIProvider(model=model))
    if provider_name == "groq":
        model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
        return DrillingAdvisor(provider=GroqProvider(model=model))
    model = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")
    return DrillingAdvisor(provider=AnthropicProvider(model=model))


__all__ = [
    "AnthropicProvider",
    "DeterministicMockLLMProvider",
    "DrillingAdvisor",
    "build_default_advisor",
    "GroqProvider",
    "LLMProviderProtocol",
    "LLMRequest",
    "OpenAIProvider",
]
