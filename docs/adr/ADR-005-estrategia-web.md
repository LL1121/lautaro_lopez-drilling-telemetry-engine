# ADR-005 — Estrategia web (rendering y entrega)

| Campo | Valor |
|-------|-------|
| **Estado** | Aceptado |
| **Fecha** | 2026-08-31 |
| **Sprint / TP** | ADI TP4 · PP3 Sprint 1–2 |
| **Proyecto** | `lautaro_lopez-drilling-telemetry-engine` |
| **Decisores** | lautaro_lopez (alumno) · contraste contra [`SPEC.md`](../../SPEC.md) |
| **Dominios impactados** | `src/ui/`, `src/pipeline/api/` |
| **Relacionado** | [ADR-004](ADR-004-stack-ui.md) · [`api-contracts.yaml`](../arquitectura/api-contracts.yaml) |

---

## Contexto del problema técnico

El gemelo digital consume **WebSocket ~60 FPS** y renderiza **WebGL** (R3F). Hay que elegir cómo entregar HTML/JS al navegador:

- ¿Páginas tradicionales (MPA) con recarga completa?
- ¿SPA pura en el cliente?
- ¿SSR/SSG con hidratación?
- ¿Solo API REST/WS y front existente sin framework?

Criterios objetivos del proyecto:

| Criterio | Relevancia PP3 |
|----------|----------------|
| SEO / indexación pública | **Baja** — HMI operador en rig, no landing comercial |
| Complejidad de estado en UI | **Alta** — stream WS, frameRef throttled, recomendaciones |
| Hosting objetivo | Contenedor Docker (API + UI); sin edge CDN obligatorio |
| Tamaño del equipo | 1 alumno + agentes IA por dominio |
| Soft real-time | **Crítico** — no bloquear main thread con SSR del canvas 3D |

Contrato API-first (TP4) fija **5 endpoints** REST/WS; la estrategia web no debe inventar rutas fuera de [`api-contracts.yaml`](../arquitectura/api-contracts.yaml).

---

## Decisión tomada

**Híbrido Next.js App Router: SSR shell + islands cliente (CSR) para WebGL y WS.**

| Capa | Estrategia | Detalle |
|------|------------|---------|
| Documento HTML inicial | **SSR** (Next App Router) | Layout, metadata, shell del dashboard |
| Gemelo 3D + gauges + WS | **CSR** (`"use client"`, `dynamic(ssr:false)`) | `DrillStringCanvas`, `useTelemetryStream` |
| Navegación | **SPA-like** dentro de `/` | Sin recarga completa al consumir telemetría |
| API de negocio | **REST + WebSocket** (FastAPI) | Contrato OpenAPI; UI no expone BFF adicional en Sprint 1 |

En la taxonomía ADI: **no es SPA pura** (hay SSR del shell), **no es MPA** (una vista principal sin form posts tradicionales), **no es solo-API** (existe `src/ui/` dedicado).

---

## Alternativas descartadas

### Alternativa A — SPA pura (Vite + React, sin Next)

| Criterio | Evaluación |
|----------|------------|
| SEO | Irrelevante para HMI |
| Estado WS/3D | Adecuado |
| Alineación repo | ADR-004 ya fijó Next; reescritura innecesaria |
| Despliegue | `index.html` estático + API; viable pero duplica decisiones ADR-004 |
| **Veredicto** | **Descartada** — costo migración sin beneficio |

### Alternativa B — MPA clásica (templates server + jQuery/vanilla)

| Criterio | Evaluación |
|----------|------------|
| Complejidad estado | Mala — 60 FPS WS requiere SPA patterns |
| WebGL / R3F | Sin ecosistema React Three Fiber |
| Mantenimiento | Fragmentación entre páginas |
| **Veredicto** | **Descartada** — incumple RF-09 y ADR-004 |

### Alternativa C — SSR completo del dashboard (incl. Three.js en servidor)

| Criterio | Evaluación |
|----------|------------|
| WebGL en servidor | Imposible / sin sentido |
| Latencia | SSR del canvas no aporta; hidratar WebGL es frágil |
| **Veredicto** | **Descartada** — viola restricción soft real-time UI |

### Alternativa D — Solo API + consumidor externo (sin `src/ui/`)

| Criterio | Evaluación |
|----------|------------|
| PP3 entregables | Gemelo digital 3D es núcleo del proyecto |
| ADI TP3 | Wireframes y journeys asumen dashboard propio |
| **Veredicto** | **Descartada** — fuera de propuesta PP3 |

### Alternativa E — GraphQL en lugar de REST OpenAPI

| Criterio | Evaluación |
|----------|------------|
| Contrato TP4 | Consigna exige OpenAPI lint; GraphQL = SDL equivalente |
| Endpoints actuales | 5 operaciones REST + WS — REST suficiente |
| Streaming | WS ya cubre telemetría; GraphQL subscriptions añade complejidad |
| **Veredicto** | **Descartada** — REST+WS documentados en `api-contracts.yaml` |

---

## Consecuencias

### Positivas

- Shell SSR rápido; canvas 3D solo en cliente evita errores WebGL en build.
- Un solo contrato OpenAPI para REST; WS documentado en el mismo archivo.
- Coherente con monolito modular (ADR-002): UI y API desacoplados por contrato.

### Negativas / deuda

- “Híbrido” exige disciplina: no importar Three en Server Components.
- Auth JWT declarada en OpenAPI pero no enforced en Sprint 1 (NG-08) — threat model cubre gap.
- El contrato incluye `GET /api/v1/simulation/status` (ya consumido por la UI) y `GET /api/v1/health` (liveness del contenedor). La entrega de producción pre-renderiza el shell (`output: export`) y lo sirve estático; WebGL y WebSocket siguen en el cliente (ADR-002, build estático).
- Segunda pantalla (alerta crítica) es énfasis UX, no ruta MPA separada.

---

## Trazabilidad

| Artefacto | Ubicación |
|-----------|-----------|
| OpenAPI | [`docs/arquitectura/api-contracts.yaml`](../arquitectura/api-contracts.yaml) |
| Threat model | [`docs/seguridad/threat-model-lite.md`](../seguridad/threat-model-lite.md) |
| Arnés seguridad v3 | [`.cursor/rules/governance.mdc`](../../.cursor/rules/governance.mdc) |

---

*ADR-005 — Aceptado · ADI TP4*
