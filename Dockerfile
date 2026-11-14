# syntax=docker/dockerfile:1
#
# Backend image for the Bluestaq Notes service.
#
# Multi-stage: the first stage builds a wheel from source, the second installs
# only that wheel into a slim runtime, so build tooling and source caches never
# ship in the final image. The container runs as a non-root user and starts via
# an entrypoint that waits for the database, applies migrations, seeds demo
# users, then serves the API.

# ---- build stage ----------------------------------------------------------
FROM python:3.12-slim AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# Build the wheel from the packaged project. Only the files needed to build are
# copied here; .dockerignore keeps the context small.
COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --upgrade pip build \
    && python -m build --wheel --outdir /dist

# ---- runtime stage --------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# Create an unprivileged user up front.
RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app

# Install the built wheel (and nothing else).
COPY --from=build /dist/*.whl /tmp/
RUN python -m pip install /tmp/*.whl && rm -f /tmp/*.whl

# Alembic configuration and migrations are needed at startup. The migrations
# read the database URL from the application settings, so no URL is baked in.
COPY alembic.ini ./
COPY migrations ./migrations
COPY docker/api-entrypoint.sh /usr/local/bin/api-entrypoint.sh
# Set an explicit world-readable/executable mode: the build context may carry a
# restrictive umask, and the container runs as a non-root user that must be able
# to read and execute the script.
RUN chmod 0755 /usr/local/bin/api-entrypoint.sh

# Ensure the app user can read what it needs and own its working directory.
RUN chown -R app:app /app

USER app

EXPOSE 8000

# Liveness check against the service's own health endpoint.
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=6 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"

ENTRYPOINT ["/usr/local/bin/api-entrypoint.sh"]
