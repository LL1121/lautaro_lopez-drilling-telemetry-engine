# ADR-003 — Persistencia y buffer de streaming

| Campo | Valor |
|-------|-------|
| **Estado** | Aceptado |
| **Fecha** | 2026-08-31 |
| **Sprint / TP** | ADI TP2 · PP3 Sprint 1 |
| **Proyecto** | `lautaro_lopez-drilling-telemetry-engine` |
| **Decisores** | lautaro_lopez |
| **Relacionado** | [`ADR-001`](ADR-001-stack-tecnologico.md) · [`SPEC.md`](../../SPEC.md) RF-10, NG-09 |

---

## Contexto del problema técnico

El sistema debe:

1. **Desacoplar** telemetría superficie (100 Hz) y MWD (~0.05 Hz, retardo 15–45 s).
2. Soportar **fixed-lag smoothing** UKF con replay de mediciones MWD tardías.
3. Publicar estado estimado ~60 FPS sin bloquear el lazo físico.

El modelo de datos **no** es entidades CRUD (usuarios, turnos): es **streams temporales** de telemetría y estado de filtro. RF-10 declara **Redis Streams**; NG-09 excluye data lake / BI histórico.

Hay que decidir qué se persiste en Sprint 1 vs roadmap, sin violar soft real-time.

---

## Decisión tomada

### Sprint 1 (implementado)

| Capa | Mecanismo | Ubicación |
|------|-----------|-----------|
| Buffer superficie/MWD | `TimeSyncBuffer` — `deque` in-memory, journal por stream | `src/pipeline/buffer/time_sync_buffer.py` |
| Estado UKF / simulación | En memoria del `SimulationOrchestrator` | `src/pipeline/orchestration/` |
| Historial advisor | `AdvisorHistoryStore` in-memory (REST GET) | `src/pipeline/api/advisor_store.py` |
| Contratos | JSON Schema archivos + Pydantic | `docs/contratos/*.json` |

**Sin base SQL** en Sprint 1. Persistencia histórica larga = **Non-Goal NG-09**.

### Roadmap (RF-10, no bloqueante Sprint 1)

| Capa | Mecanismo | Infra |
|------|-----------|-------|
| Buffer durable ingest → consumidores | **Redis Streams** | Servicio Redis en despliegue planificado (RF-10) |
| Variable de entorno planificada | `REDIS_URL` cuando exista despliegue Docker | RF-10 · ADR-001 |

Redis está en compose y dependencias (`pyproject.toml`) como **preparación**; el código de producción Sprint 1 usa buffer in-memory documentado en auditoría A-004 trade-offs.

---

## Alternativas descartadas

### Alternativa A — PostgreSQL / SQLModel como store principal

| Criterio | Evaluación |
|----------|------------|
| Consultas analíticas, ACID | **Fuerte** para BI |
| Latencia escritura 100 Hz + replay MWD | **Débil** — overhead y modelado tabular forzado |
| Alineación SPEC NG-09 (no data lake) | **Falla** — scope creep |
| Complejidad migraciones Sprint 1 | **Falla** — tiempo en schema SQL vs UKF |

**Descarte:** no hay modelo relacional dominante; telemetría es stream temporal.

### Alternativa B — SQLite embedded para journal temporal

| Criterio | Evaluación |
|----------|------------|
| Simplicidad local sin servidor | **Fuerte** |
| Throughput 100 Hz inserts + lecturas UKF | **Débil** — contención I/O en un solo archivo |
| Redis Streams ya en ADR-001 para mismo rol | **Redundante** — dos buffers |

**Descarte:** `deque` + Redis roadmap cubren mejor el patrón streaming.

### Alternativa C — Solo archivos JSON/Parquet en disco

| Criterio | Evaluación |
|----------|------------|
| Auditoría offline | **Fuerte** |
| Fixed-lag replay en tiempo real | **Débil** — latencia y parsing |
| Operación concurrente API + writer | **Riesgo** — locks de archivo |

**Descarte:** útil como export futuro, no como buffer caliente del UKF.

### Alternativa D — Redis Streams desde día 1 (sin in-memory)

| Criterio | Evaluación |
|----------|------------|
| Desacople ingest/consumidor | **Fuerte** |
| Complejidad Sprint 1 (consumer groups, trimming) | **Falla** — prioridad núcleo RK4/UKF |
| Tests deterministas sin Redis | **Débil** — más fixtures infra |

**Descarte para Sprint 1:** diferido explícito en SPEC; in-memory suficiente para demo y tests (ver integración `test_time_sync_buffer.py`).

---

## Criterios de decisión (resumen)

| Criterio | Ganador |
|----------|---------|
| Fixed-lag MWD + superficie 100 Hz | In-memory `TimeSyncBuffer` |
| Sin data lake Sprint 1 | Sin SQL |
| Roadmap RF-10 Redis | Compose + deps declaradas |
| Tests CI sin Redis obligatorio | Buffer in-memory default |

---

## Consecuencias

### Positivas

- Tests rápidos y deterministas sin testcontainers.
- Orquestador y UKF en el mismo proceso (ADR-002).
- Migración a Redis acotada a capa `src/pipeline/buffer/` + ingest.

### Riesgos y mitigaciones

| Riesgo | Mitigación |
|--------|------------|
| Pérdida de datos al reiniciar proceso | Aceptado Sprint 1 (synthetic demo); Redis en RF-10 |
| Memoria crece con journal | `deque` maxlen / política de trimming en buffer |
| Drift código vs ADR (Redis unused) | ADR-003 + NG RF-10; compose listo |

### Seguimiento

- Al implementar Redis Streams → actualizar C4-contenedores (línea sólida, no dotted).
- Si se requiere histórico > 1 h → nuevo ADR (object storage o TSDB), no silent SQL.

---

## Referencias

- [`SPEC.md`](../../SPEC.md) — RF-10, NG-09.
- [`TimeSyncBuffer`](../../src/pipeline/buffer/time_sync_buffer.py).
- [`docker-compose.yml`](../../docker-compose.yml) — API + UI estática. Sin servicio Redis ni SQL; el buffer activo sigue in-memory.
- [`C4-contenedores.md`](../arquitectura/C4-contenedores.md).
