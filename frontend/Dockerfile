# syntax=docker/dockerfile:1
#
# Frontend image for the Bluestaq Notes web client.
#
# Stage 1 builds the static SPA with Vite. Stage 2 serves it with nginx, which
# also reverse-proxies /api to the backend so the browser talks to a single
# origin (no CORS, no backend hostname baked into the bundle).

# ---- build stage ----------------------------------------------------------
FROM node:22-slim AS build

WORKDIR /app

# Install dependencies first so the layer is cached until the lockfile changes.
COPY package.json package-lock.json ./
RUN npm ci

# Copy sources and build. VITE_API_BASE_URL is intentionally left empty so the
# client issues same-origin requests that nginx forwards to the API.
COPY . .
ENV VITE_API_BASE_URL=""
RUN npm run build

# ---- runtime stage --------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

# Our own server config template. The nginx image's entrypoint runs envsubst
# over /etc/nginx/templates/*.template at container start, substituting
# ${WEB_INTERNAL_PORT} and ${API_UPSTREAM} from the environment, and writes the
# result to /etc/nginx/conf.d/default.conf.
COPY docker/nginx.conf.template /etc/nginx/templates/default.conf.template

# Static assets produced by the build stage.
COPY --from=build /app/dist /usr/share/nginx/html

# Default listen port inside the container; overridable via WEB_INTERNAL_PORT.
ENV WEB_INTERNAL_PORT=80
EXPOSE 80

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=6 \
    CMD wget -q -O /dev/null "http://127.0.0.1:${WEB_INTERNAL_PORT}/" || exit 1
