"""DTOs de requests REST para control del simulador."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ScenarioLiteral = Literal["normal", "severe_stick_slip", "transient_choke"]


class StartSimulationRequestDTO(BaseModel):
    """Body de ``POST /api/v1/simulation/start``.

    Attributes
    ----------
    preset : ScenarioLiteral or None
        Escenario a cargar al arrancar; ``None`` conserva el actual.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    preset: ScenarioLiteral | None = None


class SetPresetRequestDTO(BaseModel):
    """Body de ``POST /api/v1/simulation/preset``.

    Attributes
    ----------
    preset : ScenarioLiteral
        Escenario preconfigurado del ``WellSimulator``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    preset: ScenarioLiteral


class HealthStatusDTO(BaseModel):
    """Liveness del proceso API.

    Attributes
    ----------
    status : Literal["ok"]
        Siempre ``ok`` cuando el proceso acepta HTTP.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ok"]


class OrchestratorStatusDTO(BaseModel):
    """Estado operativo del orquestador de simulación.

    Attributes
    ----------
    running : bool
        ``True`` si el loop físico está activo.
    preset : ScenarioLiteral
        Preset activo del simulador.
    sim_time_s : float
        Tiempo de simulación actual [s], ≥ 0.
    mwd_drops : int
        Paquetes MWD descartados por ventana insuficiente, ≥ 0.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    running: bool
    preset: ScenarioLiteral
    sim_time_s: float = Field(ge=0.0)
    mwd_drops: int = Field(ge=0)
