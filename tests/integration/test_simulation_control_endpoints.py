"""Tests de endpoints REST de control del simulador."""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.pipeline.api.app import create_app
from src.pipeline.orchestration.simulation_orchestrator import (
    OrchestratorConfig,
    SimulationOrchestrator,
)
from src.engine.simulator.well_generator import default_simulator_config


def _client() -> TestClient:
    sim = default_simulator_config(seed=1, acoustic_delay_sec=20.0)
    orch = SimulationOrchestrator(config=OrchestratorConfig(simulator_config=sim))
    app = create_app(orchestrator=orch)
    return TestClient(app)


def test_start_stop_and_status() -> None:
    with _client() as client:
        r = client.post("/api/v1/simulation/start", json={})
        assert r.status_code == 200
        body = r.json()
        assert body["running"] is True
        assert body["preset"] == "normal"

        status = client.get("/api/v1/simulation/status")
        assert status.status_code == 200
        assert status.json()["running"] is True

        stop = client.post("/api/v1/simulation/stop")
        assert stop.status_code == 200
        assert stop.json()["running"] is False


def test_start_with_preset() -> None:
    with _client() as client:
        r = client.post(
            "/api/v1/simulation/start",
            json={"preset": "severe_stick_slip"},
        )
        assert r.status_code == 200
        assert r.json()["preset"] == "severe_stick_slip"
        assert r.json()["running"] is True


def test_set_preset() -> None:
    with _client() as client:
        r = client.post(
            "/api/v1/simulation/preset",
            json={"preset": "transient_choke"},
        )
        assert r.status_code == 200
        assert r.json()["preset"] == "transient_choke"


def test_health_is_ok() -> None:
    with _client() as client:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_invalid_preset_returns_422() -> None:
    with _client() as client:
        r = client.post(
            "/api/v1/simulation/preset",
            json={"preset": "not_a_real_preset"},
        )
        assert r.status_code == 422
