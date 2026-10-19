# Despliegue en Lyntrix (Hetzner)

API FastAPI (`drilling_backend:8000`) y shell Next exportado servido por nginx (`drilling_frontend:80`). Caddy (`caddy_proxy`) ya está en la red Docker externa `proxy_net` y termina TLS.

No hay base SQL ni Redis en este compose. El buffer y el historial del advisor son in-memory (ADR-003, NG-09). `./data` se monta en el backend (`DATA_DIR=/app/data`) para estado local futuro; el proceso actual no escribe ahí.

## Variables de entorno

Copiar [`.env.example`](.env.example) a `.env`.

| Variable | Obligatoria | Uso |
|----------|-------------|-----|
| `LLM_PROVIDER` | no (`mock`) | `mock`, `openai`, `groq` o `anthropic` |
| `OPENAI_API_KEY` | solo si `LLM_PROVIDER=openai` | Si falta, o el paquete `openai` no está en la imagen, el advisor usa el mock SOP |
| `GROQ_API_KEY` | solo si `LLM_PROVIDER=groq` | Igual, degrada al mock |
| `ANTHROPIC_API_KEY` | solo si `LLM_PROVIDER=anthropic` | Igual, degrada al mock |
| `OPENAI_MODEL` / `GROQ_MODEL` / `ANTHROPIC_MODEL` | no | Modelo del proveedor elegido |
| `DATA_DIR` | no | Default `/app/data` dentro del contenedor |

La UI de producción no lee `.env` en runtime. La imagen frontend se construye con `NEXT_PUBLIC_API_BASE` vacío y `NEXT_PUBLIC_WS_URL=/ws/telemetry`, así el navegador llama al mismo origen (`/api/...` y `wss://<host>/ws/telemetry`).

## Rutas que Caddy debe enviar al backend

El navegador habla solo con `https://drilling.lyntrix.com.ar`. Estas rutas van a `drilling_backend:8000`. El resto (HTML, `/_next/*`) va a `drilling_frontend:80`.

Prefijo REST (la UI usa las cuatro primeras; el healthcheck de Docker pega a `/api/v1/health` **dentro** del contenedor, no hace falta publicarlo aparte):

| Método | Ruta | Quién la usa |
|--------|------|----------------|
| `POST` | `/api/v1/simulation/start` | UI — Start |
| `POST` | `/api/v1/simulation/stop` | UI — Stop |
| `POST` | `/api/v1/simulation/preset` | UI — preset |
| `GET` | `/api/v1/simulation/status` | UI — refresh |
| `GET` | `/api/v1/advisor/recommendations` | API (historial SOP; la UI recibe las recomendaciones por WebSocket) |
| `GET` | `/api/v1/health` | Liveness |

WebSocket (upgrade). **No** matchea `handle /api/*`. Hay que enrutarlo aparte:

| Método | Ruta | Quién la usa |
|--------|------|----------------|
| `GET` (upgrade) | `/ws/telemetry` | UI — frames `telemetry_frame` (~60 FPS) y eventos `advisor_recommendation` |

`/docs` y `/openapi.json` no se publican: Caddy no los reenvía al backend.

## Bloque Caddy

En el Caddy del VPS (`caddy_proxy`), sitio `drilling.lyntrix.com.ar`:

```caddy
drilling.lyntrix.com.ar {
	import lyntrix_ssl
	handle /api/* {
		reverse_proxy drilling_backend:8000
	}
	handle /ws/* {
		reverse_proxy drilling_backend:8000
	}
	handle {
		reverse_proxy drilling_frontend:80
	}
}
```

Caddy hace el upgrade de WebSocket en `reverse_proxy` sin directivas extra. Recargar Caddy después de editar el sitio.

## Comandos

En el VPS, con la red `proxy_net` ya creada y este repo en el directorio de trabajo:

```bash
cp .env.example .env
mkdir -p data
docker compose build
docker compose up -d
docker compose ps
```

`docker compose` no publica puertos en el host. Caddy alcanza `drilling_backend:8000` y `drilling_frontend:80` por DNS de Docker.

Actualizar:

```bash
git pull
docker compose build
docker compose up -d
```

Bajar:

```bash
docker compose down
```

## Notas

- Imágenes multi-stage. El runtime corre como usuario no root (`app` uid 10001 en el backend, `nginx` en el frontend). nginx escucha en 80 con `cap_net_bind_service`.
- El shell de la UI es el export estático de Next (`STATIC_EXPORT=1`). El canvas 3D y el WebSocket siguen en el cliente. `next start` / Playwright no usan ese modo.
- Los SDK `openai`, `groq` y `anthropic` no están en la imagen. Con `LLM_PROVIDER=mock` (default) el SOP determinista funciona sin clave. Para un proveedor real hay que añadir el paquete y la key; si falta cualquiera de los dos, el proceso arranca igual y el advisor vuelve al mock.
