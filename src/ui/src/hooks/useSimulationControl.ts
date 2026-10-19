"use client";

import { useCallback, useState } from "react";

import type { OrchestratorStatus, ScenarioName } from "@/types/telemetry";

/**
 * Base del API.
 * - sin variable: uvicorn local (`http://localhost:8000`)
 * - cadena vacía: mismo origen (producción detrás de Caddy, paths `/api/...`)
 */
export function resolveApiBase(configured: string | undefined): string {
  if (configured === undefined) {
    return "http://localhost:8000";
  }
  return configured.replace(/\/$/, "");
}

const DEFAULT_API_BASE = resolveApiBase(process.env.NEXT_PUBLIC_API_BASE);

export interface SimulationControlState {
  status: OrchestratorStatus | null;
  busy: boolean;
  error: string | null;
  start: (preset?: ScenarioName) => Promise<void>;
  stop: () => Promise<void>;
  setPreset: (preset: ScenarioName) => Promise<void>;
  refresh: () => Promise<void>;
}

async function postJson(
  path: string,
  body?: Record<string, unknown>,
): Promise<OrchestratorStatus> {
  const init: RequestInit = {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  };
  if (body !== undefined) {
    init.body = JSON.stringify(body);
  }
  const response = await fetch(`${DEFAULT_API_BASE}${path}`, init);
  if (!response.ok) {
    throw new Error(`HTTP ${response.status} on ${path}`);
  }
  return (await response.json()) as OrchestratorStatus;
}

export function useSimulationControl(): SimulationControlState {
  const [status, setStatus] = useState<OrchestratorStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async (fn: () => Promise<OrchestratorStatus>) => {
    setBusy(true);
    setError(null);
    try {
      const next = await fn();
      setStatus(next);
    } catch (err) {
      const message = err instanceof Error ? err.message : "request failed";
      setError(message);
    } finally {
      setBusy(false);
    }
  }, []);

  const start = useCallback(
    async (preset?: ScenarioName) => {
      await run(() =>
        postJson("/api/v1/simulation/start", preset ? { preset } : {}),
      );
    },
    [run],
  );

  const stop = useCallback(async () => {
    await run(() => postJson("/api/v1/simulation/stop"));
  }, [run]);

  const setPreset = useCallback(
    async (preset: ScenarioName) => {
      await run(() => postJson("/api/v1/simulation/preset", { preset }));
    },
    [run],
  );

  const refresh = useCallback(async () => {
    await run(async () => {
      const response = await fetch(`${DEFAULT_API_BASE}/api/v1/simulation/status`);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status} on /status`);
      }
      return (await response.json()) as OrchestratorStatus;
    });
  }, [run]);

  return { status, busy, error, start, stop, setPreset, refresh };
}
