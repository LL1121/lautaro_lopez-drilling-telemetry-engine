import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  resolveWebSocketUrl,
  useTelemetryStream,
} from "@/hooks/useTelemetryStream";
import { resolveApiBase } from "@/hooks/useSimulationControl";

type Handler = ((ev: MessageEvent<string>) => void) | null;

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: Handler = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  readyState = 0;

  constructor(public url: string) {
    MockWebSocket.instances.push(this);
    queueMicrotask(() => {
      this.readyState = 1;
      this.onopen?.();
    });
  }

  close() {
    this.readyState = 3;
    this.onclose?.();
  }

  emit(data: string) {
    this.onmessage?.({ data } as MessageEvent<string>);
  }
}

describe("same-origin endpoints", () => {
  it("keeps absolute API and WebSocket URLs for local dev", () => {
    expect(resolveApiBase(undefined)).toBe("http://localhost:8000");
    expect(resolveApiBase("http://localhost:8000/")).toBe("http://localhost:8000");
    expect(resolveWebSocketUrl("ws://localhost:8000/ws/telemetry")).toBe(
      "ws://localhost:8000/ws/telemetry",
    );
  });

  it("uses relative /api and wss on the page origin in production", () => {
    expect(resolveApiBase("")).toBe("");
    expect(
      resolveWebSocketUrl("/ws/telemetry", {
        protocol: "https:",
        host: "drilling.lyntrix.com.ar",
      }),
    ).toBe("wss://drilling.lyntrix.com.ar/ws/telemetry");
    expect(
      resolveWebSocketUrl("/ws/telemetry", {
        protocol: "http:",
        host: "127.0.0.1:8080",
      }),
    ).toBe("ws://127.0.0.1:8080/ws/telemetry");
  });
});

describe("useTelemetryStream", () => {
  const OriginalWS = globalThis.WebSocket;

  beforeEach(() => {
    MockWebSocket.instances = [];
    vi.stubGlobal("WebSocket", MockWebSocket as unknown as typeof WebSocket);
  });

  afterEach(() => {
    vi.stubGlobal("WebSocket", OriginalWS);
  });

  it("updates frameRef on telemetry_frame and appends advisor events", async () => {
    const { result } = renderHook(() =>
      useTelemetryStream("ws://localhost:8000/ws/telemetry"),
    );

    await act(async () => {
      await Promise.resolve();
    });

    const socket = MockWebSocket.instances[0];
    expect(socket).toBeDefined();

    const framePayload = {
      type: "telemetry_frame",
      data: {
        timestamp: "2026-08-25T12:00:00Z",
        frame_id: 7,
        ukf_state: {
          theta_rad: [0, 0.2],
          omega_rad_s: [10, 8],
          rpm_bit_est: 76,
          torque_bit_est_knm: 2.2,
        },
        torsional_deformation_rad: [0, 0.2],
        ssi: 0.9,
        alert_level: "warning",
      },
    };

    act(() => {
      socket!.emit(JSON.stringify(framePayload));
    });

    expect(result.current.frameRef.current?.frame_id).toBe(7);
    expect(result.current.connectionStatus).toBe("open");

    const advisorPayload = {
      type: "advisor_recommendation",
      data: {
        triggered_at: "2026-08-25T12:00:01Z",
        snapshot: {
          timestamp: "2026-08-25T12:00:01Z",
          surface_rpm: 120,
          estimated_bit_rpm: 10,
          wob_kn: 80,
          ssi: 1.5,
          regime: "critical",
          torque_contrast: 2,
        },
        recommendation: {
          incident_type: "stick_slip",
          severity_level: "critical",
          physical_root_cause: "friction",
          immediate_actions: ["reduce WOB"],
          target_wob_kn: 60,
          target_rpm: 130,
          rationale: "SOP",
        },
      },
    };

    act(() => {
      socket!.emit(JSON.stringify(advisorPayload));
    });

    expect(result.current.recommendations).toHaveLength(1);
    expect(result.current.recommendations[0]?.recommendation.incident_type).toBe(
      "stick_slip",
    );
  });
});
