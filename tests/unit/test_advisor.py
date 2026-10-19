"""Tests unitarios del Advisor LLM (debounce, mock, invariantes, sanitización)."""

from __future__ import annotations

import asyncio
import math
import re
from datetime import UTC, datetime
from typing import Literal

import pytest
from pydantic import ValidationError

from src.advisor.llm_diagnostics import (
    AnthropicProvider,
    DeterministicMockLLMProvider,
    DrillingAdvisor,
    GroqProvider,
    LLMRequest,
    OpenAIProvider,
    build_default_advisor,
)
from src.advisor.prompts.drilling_sop import (
    build_system_prompt,
    build_user_prompt,
    sanitize_numeric,
)
from src.advisor.schemas import (
    SAFE_RPM_RANGE,
    SAFE_WOB_RANGE_KN,
    AdvisorIncidentSnapshot,
    AdvisorRecommendation,
)


def _snapshot(
    *,
    ssi: float = 1.2,
    surface_rpm: float = 120.0,
    bit_rpm: float = 20.0,
    wob_kn: float = 80.0,
    torque_contrast: float = 2.0,
    regime: Literal["normal", "warning", "critical"] = "critical",
) -> AdvisorIncidentSnapshot:
    return AdvisorIncidentSnapshot(
        timestamp=datetime(2026, 8, 25, 12, 0, 0, tzinfo=UTC),
        surface_rpm=surface_rpm,
        estimated_bit_rpm=bit_rpm,
        wob_kn=wob_kn,
        ssi=ssi,
        regime=regime,
        torque_contrast=torque_contrast,
    )


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class _FailingProvider:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def generate(self, request: LLMRequest) -> str:
        raise self.exc


def test_sanitize_numeric_rejects_nan_and_inf() -> None:
    with pytest.raises(ValueError):
        sanitize_numeric(float("nan"), "ssi")
    with pytest.raises(ValueError):
        sanitize_numeric(float("inf"), "ssi")
    assert sanitize_numeric(1.5, "ssi") == "1.500000"


def test_build_user_prompt_only_interpolates_sanitized_numbers() -> None:
    snap = _snapshot()
    prompt = build_user_prompt(snap)
    assert "surface_rpm:" in prompt
    assert "ssi: 1.200000" in prompt
    assert "regime: critical" in prompt
    # No JSON crudo ni claves arbitrarias de telemetría.
    assert "{" not in prompt
    assert "hookload" not in prompt
    assert re.search(r"surface_rpm: \d+\.\d+", prompt)


def test_system_prompt_mentions_safe_ranges() -> None:
    text = build_system_prompt()
    assert str(SAFE_WOB_RANGE_KN[1]) in text
    assert str(SAFE_RPM_RANGE[1]) in text


def test_mock_provider_is_deterministic() -> None:
    provider = DeterministicMockLLMProvider()
    snap = _snapshot()
    request = LLMRequest(
        system_prompt=build_system_prompt(),
        user_prompt=build_user_prompt(snap),
        response_schema={},
        snapshot=snap,
    )
    a = asyncio.run(provider.generate(request))
    b = asyncio.run(provider.generate(request))
    assert a == b
    rec = AdvisorRecommendation.model_validate_json(a)
    assert rec.incident_type == "stick_slip"
    assert SAFE_WOB_RANGE_KN[0] <= rec.target_wob_kn <= SAFE_WOB_RANGE_KN[1]
    assert SAFE_RPM_RANGE[0] <= rec.target_rpm <= SAFE_RPM_RANGE[1]


def test_mock_provider_classifies_over_torque() -> None:
    provider = DeterministicMockLLMProvider()
    snap = _snapshot(
        ssi=1.1,
        bit_rpm=100.0,
        surface_rpm=120.0,
        torque_contrast=12.0,
    )
    request = LLMRequest(
        system_prompt="s",
        user_prompt="u",
        response_schema={},
        snapshot=snap,
    )
    raw = asyncio.run(provider.generate(request))
    rec = AdvisorRecommendation.model_validate_json(raw)
    assert rec.incident_type == "over_torque"


def test_debounce_suppresses_sustained_incident_within_cooldown() -> None:
    clock = _FakeClock(0.0)
    advisor = DrillingAdvisor(
        provider=DeterministicMockLLMProvider(),
        cooldown_sec=30.0,
        clock=clock,
    )
    snap = _snapshot(ssi=1.5)

    first = asyncio.run(advisor.evaluate_telemetry(snap))
    assert first is not None

    clock.advance(5.0)
    second = asyncio.run(advisor.evaluate_telemetry(snap))
    assert second is None

    clock.advance(30.0)
    third = asyncio.run(advisor.evaluate_telemetry(snap))
    assert third is not None


def test_debounce_resets_after_ssi_drops_below_threshold() -> None:
    clock = _FakeClock(0.0)
    advisor = DrillingAdvisor(
        provider=DeterministicMockLLMProvider(),
        cooldown_sec=30.0,
        clock=clock,
    )
    critical = _snapshot(ssi=1.5)
    normal = _snapshot(ssi=0.2, regime="normal")

    assert asyncio.run(advisor.evaluate_telemetry(critical)) is not None
    clock.advance(1.0)
    assert asyncio.run(advisor.evaluate_telemetry(normal)) is None
    clock.advance(1.0)
    # Nuevo incidente: dispara inmediatamente aunque cooldown no haya expirado.
    assert asyncio.run(advisor.evaluate_telemetry(critical)) is not None


def test_provider_timeout_returns_none() -> None:
    advisor = DrillingAdvisor(
        provider=_FailingProvider(TimeoutError("slow")),
        request_timeout_sec=0.1,
    )
    result = asyncio.run(advisor.evaluate_telemetry(_snapshot()))
    assert result is None


def test_provider_validation_error_returns_none() -> None:
    class BadJsonProvider:
        async def generate(self, request: LLMRequest) -> str:
            return '{"incident_type": "stick_slip"}'  # incomplete

    advisor = DrillingAdvisor(provider=BadJsonProvider())
    assert asyncio.run(advisor.evaluate_telemetry(_snapshot())) is None


def test_recommendation_rejects_out_of_range_targets() -> None:
    with pytest.raises(ValidationError):
        AdvisorRecommendation(
            incident_type="stick_slip",
            severity_level="critical",
            physical_root_cause="x",
            immediate_actions=["reduce WOB"],
            target_wob_kn=999.0,
            target_rpm=100.0,
            rationale="bad",
        )


def test_safety_invariants_across_extreme_snapshots() -> None:
    provider = DeterministicMockLLMProvider()
    cases = [
        _snapshot(ssi=5.0, wob_kn=0.0, surface_rpm=0.0, bit_rpm=0.0),
        _snapshot(ssi=1.01, wob_kn=200.0, surface_rpm=220.0, bit_rpm=10.0),
        _snapshot(ssi=2.0, wob_kn=150.0, surface_rpm=50.0, bit_rpm=5.0, torque_contrast=20.0),
    ]
    for snap in cases:
        request = LLMRequest(
            system_prompt="s",
            user_prompt="u",
            response_schema={},
            snapshot=snap,
        )
        rec = AdvisorRecommendation.model_validate_json(
            asyncio.run(provider.generate(request))
        )
        assert SAFE_WOB_RANGE_KN[0] <= rec.target_wob_kn <= SAFE_WOB_RANGE_KN[1]
        assert SAFE_RPM_RANGE[0] <= rec.target_rpm <= SAFE_RPM_RANGE[1]
        assert math.isfinite(rec.target_wob_kn)
        assert math.isfinite(rec.target_rpm)


def test_mock_classifies_transient_choke_and_unknown() -> None:
    provider = DeterministicMockLLMProvider()
    choke = _snapshot(ssi=0.7, regime="warning", bit_rpm=100.0, torque_contrast=1.0)
    # ssi <= 1.0 no dispara advisor, pero el mock sí clasifica si se llama directo.
    raw = asyncio.run(
        provider.generate(
            LLMRequest("s", "u", {}, choke),
        )
    )
    assert AdvisorRecommendation.model_validate_json(raw).incident_type == "transient_choke"

    unknown = _snapshot(ssi=1.05, bit_rpm=100.0, surface_rpm=110.0, torque_contrast=1.0)
    raw2 = asyncio.run(provider.generate(LLMRequest("s", "u", {}, unknown)))
    assert AdvisorRecommendation.model_validate_json(raw2).incident_type in (
        "stick_slip",
        "unknown",
    )


def test_action_length_validator() -> None:
    with pytest.raises(ValidationError):
        AdvisorRecommendation(
            incident_type="stick_slip",
            severity_level="critical",
            physical_root_cause="x",
            immediate_actions=["x" * 201],
            target_wob_kn=50.0,
            target_rpm=100.0,
            rationale="r",
        )


def test_optional_providers_require_api_keys() -> None:
    from src.advisor.llm_diagnostics import (
        AnthropicProvider,
        GroqProvider,
        OpenAIProvider,
    )

    snap = _snapshot()
    request = LLMRequest("s", "u", {}, snap)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        asyncio.run(OpenAIProvider().generate(request))
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        asyncio.run(GroqProvider().generate(request))
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        asyncio.run(AnthropicProvider().generate(request))


def test_optional_providers_import_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.advisor.llm_diagnostics import (
        AnthropicProvider,
        GroqProvider,
        OpenAIProvider,
    )

    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("GROQ_API_KEY", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")

    import builtins

    real_import = builtins.__import__

    def _blocked(name: str, *args: object, **kwargs: object) -> object:
        if name in {"openai", "groq", "anthropic"}:
            raise ImportError(name)
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", _blocked)
    snap = _snapshot()
    request = LLMRequest("s", "u", {}, snap)
    with pytest.raises(RuntimeError, match="openai package"):
        asyncio.run(OpenAIProvider().generate(request))
    with pytest.raises(RuntimeError, match="groq package"):
        asyncio.run(GroqProvider().generate(request))
    with pytest.raises(RuntimeError, match="anthropic package"):
        asyncio.run(AnthropicProvider().generate(request))


def test_unexpected_provider_exception_returns_none() -> None:
    class Boom:
        async def generate(self, request: LLMRequest) -> str:
            raise RuntimeError("boom")

    advisor = DrillingAdvisor(provider=Boom())
    assert asyncio.run(advisor.evaluate_telemetry(_snapshot())) is None


def test_unexpected_non_runtime_exception_returns_none() -> None:
    class Weird:
        async def generate(self, request: LLMRequest) -> str:
            raise KeyError("weird")

    advisor = DrillingAdvisor(provider=Weird())
    assert asyncio.run(advisor.evaluate_telemetry(_snapshot())) is None


def test_optional_providers_success_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock SDKs opcionales para cubrir adaptadores sin red."""
    from types import SimpleNamespace

    from src.advisor.llm_diagnostics import (
        AnthropicProvider,
        GroqProvider,
        OpenAIProvider,
    )

    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

    class _FakeCompletions:
        async def create(self, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))]
            )

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeOpenAI:
        def __init__(self, **_kwargs: object) -> None:
            self.chat = _FakeChat()

    class _FakeGroq:
        def __init__(self, **_kwargs: object) -> None:
            self.chat = _FakeChat()

    class _FakeMessages:
        async def create(self, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(content=[SimpleNamespace(text='{"ok":true}')])

    class _FakeAnthropic:
        def __init__(self, **_kwargs: object) -> None:
            self.messages = _FakeMessages()

    import sys

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(AsyncOpenAI=_FakeOpenAI))
    monkeypatch.setitem(sys.modules, "groq", SimpleNamespace(AsyncGroq=_FakeGroq))
    monkeypatch.setitem(
        sys.modules, "anthropic", SimpleNamespace(AsyncAnthropic=_FakeAnthropic)
    )

    snap = _snapshot()
    request = LLMRequest("s", "u", {"type": "object"}, snap)
    assert asyncio.run(OpenAIProvider().generate(request)) == '{"ok":true}'
    assert asyncio.run(GroqProvider().generate(request)) == '{"ok":true}'
    assert asyncio.run(AnthropicProvider().generate(request)) == '{"ok":true}'


def test_optional_providers_empty_content(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from src.advisor.llm_diagnostics import AnthropicProvider, OpenAIProvider

    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

    class _EmptyCompletions:
        async def create(self, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=None))]
            )

    class _FakeOpenAI:
        def __init__(self, **_kwargs: object) -> None:
            self.chat = SimpleNamespace(completions=_EmptyCompletions())

    class _EmptyMessages:
        async def create(self, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(content=[])

    class _FakeAnthropic:
        def __init__(self, **_kwargs: object) -> None:
            self.messages = _EmptyMessages()

    import sys

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(AsyncOpenAI=_FakeOpenAI))
    monkeypatch.setitem(
        sys.modules, "anthropic", SimpleNamespace(AsyncAnthropic=_FakeAnthropic)
    )
    request = LLMRequest("s", "u", {}, _snapshot())
    with pytest.raises(RuntimeError, match="empty"):
        asyncio.run(OpenAIProvider().generate(request))
    with pytest.raises(RuntimeError, match="empty"):
        asyncio.run(AnthropicProvider().generate(request))


def test_default_advisor_is_mock_without_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    advisor = build_default_advisor()
    assert isinstance(advisor.provider, DeterministicMockLLMProvider)


@pytest.mark.parametrize(
    ("provider_name", "key_env"),
    [
        ("openai", "OPENAI_API_KEY"),
        ("groq", "GROQ_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
    ],
)
def test_default_advisor_degrades_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
    provider_name: str,
    key_env: str,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", provider_name)
    monkeypatch.delenv(key_env, raising=False)
    advisor = build_default_advisor()
    assert isinstance(advisor.provider, DeterministicMockLLMProvider)


def test_default_advisor_degrades_when_sdk_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "present-but-not-a-live-secret")
    monkeypatch.setattr(
        "src.advisor.llm_diagnostics.importlib.util.find_spec",
        lambda _name: None,
    )
    advisor = build_default_advisor()
    assert isinstance(advisor.provider, DeterministicMockLLMProvider)


def test_default_advisor_unknown_provider_uses_mock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "not-a-provider")
    advisor = build_default_advisor()
    assert isinstance(advisor.provider, DeterministicMockLLMProvider)


def test_default_advisor_selects_configured_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "present-but-not-a-live-secret")
    monkeypatch.setenv("GROQ_MODEL", "llama-test")
    monkeypatch.setattr(
        "src.advisor.llm_diagnostics.importlib.util.find_spec",
        lambda name: object() if name == "groq" else None,
    )
    advisor = build_default_advisor()
    assert isinstance(advisor.provider, GroqProvider)
    assert advisor.provider.model == "llama-test"

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "present-but-not-a-live-secret")
    monkeypatch.setattr(
        "src.advisor.llm_diagnostics.importlib.util.find_spec",
        lambda name: object() if name == "openai" else None,
    )
    openai_advisor = build_default_advisor()
    assert isinstance(openai_advisor.provider, OpenAIProvider)

    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "present-but-not-a-live-secret")
    monkeypatch.setattr(
        "src.advisor.llm_diagnostics.importlib.util.find_spec",
        lambda name: object() if name == "anthropic" else None,
    )
    anthropic_advisor = build_default_advisor()
    assert isinstance(anthropic_advisor.provider, AnthropicProvider)


