# syntax=docker/dockerfile:1

FROM node:22-bookworm-slim AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .

# Mismo origen en producción: REST relativo /api y WebSocket /ws/telemetry (wss).
ENV STATIC_EXPORT=1 \
    NEXT_PUBLIC_API_BASE= \
    NEXT_PUBLIC_WS_URL=/ws/telemetry \
    NODE_ENV=production

RUN npm run build

FROM nginx:1.27-alpine AS runtime

RUN apk add --no-cache libcap \
    && rm -f /etc/nginx/conf.d/default.conf \
    && mkdir -p /tmp/client_temp /tmp/proxy_temp /tmp/fastcgi_temp /tmp/uwsgi_temp /tmp/scgi_temp \
    && chown -R nginx:nginx \
        /usr/share/nginx/html \
        /var/cache/nginx \
        /var/log/nginx \
        /tmp/client_temp \
        /tmp/proxy_temp \
        /tmp/fastcgi_temp \
        /tmp/uwsgi_temp \
        /tmp/scgi_temp \
    && setcap 'cap_net_bind_service=+ep' /usr/sbin/nginx

COPY nginx.conf /etc/nginx/nginx.conf
COPY --from=build --chown=nginx:nginx /app/out /usr/share/nginx/html

USER nginx
EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1

ENTRYPOINT ["nginx", "-g", "daemon off;"]
