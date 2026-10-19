"""Aplicación FastAPI del Data Pipeline (lifespan + routers)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.advisor.llm_diagnostics import (
    DrillingAdvisor,
    build_default_advisor,
)
from src.pipeline.api.advisor_store import AdvisorHistoryStore
from src.pipeline.api.connection_manager import ConnectionManager
from src.pipeline.api.routers.advisor import router as advisor_router
from src.pipeline.api.routers.simulation import router as simulation_router
from src.pipeline.api.routers.telemetry_ws import router as telemetry_ws_router
from src.pipeline.api.schemas.requests import HealthStatusDTO
from src.pipeline.orchestration.simulation_orchestrator import (
    SimulationOrchestrator,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Crea orquestador / conexiones y lanza loops; cancela en shutdown."""
    orchestrator: SimulationOrchestrator = app.state.orchestrator
    connections: ConnectionManager = app.state.connections
    # Asegura que el orquestador pueda emitir advisor_recommendation por WS.
    orchestrator.connections = connections
    physics_task = asyncio.create_task(
        orchestrator.run_physics_loop(),
        name="physics-loop",
    )
    broadcast_task = asyncio.create_task(
        orchestrator.run_broadcast_loop(connections),
        name="broadcast-loop",
    )
    app.state.physics_task = physics_task
    app.state.broadcast_task = broadcast_task
    try:
        yield
    finally:
        orchestrator.stop()
        physics_task.cancel()
        broadcast_task.cancel()
        await asyncio.gather(physics_task, broadcast_task, return_exceptions=True)
        await connections.close_all()


def create_app(
    orchestrator: SimulationOrchestrator | None = None,
    connections: ConnectionManager | None = None,
    advisor: DrillingAdvisor | None = None,
    advisor_store: AdvisorHistoryStore | None = None,
) -> FastAPI:
    """Factory de la app FastAPI.

    Parameters
    ----------
    orchestrator : SimulationOrchestrator or None
        Instancia inyectable (tests); si es ``None`` se crea con defaults.
    connections : ConnectionManager or None
        Gestor de WebSockets inyectable.
    advisor : DrillingAdvisor or None
        Advisor LLM inyectable. Si es ``None``, :func:`build_default_advisor`
        elige proveedor por entorno y cae al mock determinista sin API key.
    advisor_store : AdvisorHistoryStore or None
        Historial de recomendaciones inyectable.

    Returns
    -------
    FastAPI
        App con lifespan, REST y WebSocket.
    """
    store = advisor_store if advisor_store is not None else AdvisorHistoryStore()
    adv = advisor if advisor is not None else build_default_advisor()
    mgr = connections if connections is not None else ConnectionManager()
    if orchestrator is not None:
        orch = orchestrator
        orch.advisor = adv
        orch.advisor_store = store
        orch.connections = mgr
    else:
        orch = SimulationOrchestrator(
            advisor=adv,
            advisor_store=store,
            connections=mgr,
        )

    app = FastAPI(
        title="Drilling Telemetry Engine — Pipeline",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.orchestrator = orch
    app.state.connections = mgr
    app.state.advisor = adv
    app.state.advisor_store = store

    @app.get("/api/v1/health", response_model=HealthStatusDTO)
    async def health() -> HealthStatusDTO:
        """Liveness para el healthcheck del contenedor (sin estado de simulación)."""
        return HealthStatusDTO(status="ok")

    app.include_router(simulation_router)
    app.include_router(telemetry_ws_router)
    app.include_router(advisor_router)
    return app


__all__ = ["create_app", "lifespan"]
