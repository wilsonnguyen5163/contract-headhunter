# syntax=docker/dockerfile:1
# CPU-only Linux x86_64 image for jobmatch. Never add CUDA packages or run with --gpus.
ARG UV_VERSION=0.8.0
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:3.12-slim-bookworm

# Match the host user so files written to the ./data bind mount stay editable on the host.
ARG APP_UID=1000
ARG APP_GID=1000

COPY --from=uv /uv /uvx /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/jobmatch/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    PATH="/opt/jobmatch/venv/bin:${PATH}" \
    HF_HOME=/cache/huggingface \
    HF_HUB_DISABLE_TELEMETRY=1 \
    TOKENIZERS_PARALLELISM=false

RUN groupadd --gid "${APP_GID}" app \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home app \
    && mkdir -p /opt/jobmatch /cache/huggingface /app/data /app/exports \
    && chown -R app:app /opt/jobmatch /cache /app

USER app
WORKDIR /app

# Dependencies first (cached layer), then the project itself. --frozen: install exactly uv.lock.
COPY --chown=app:app pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY --chown=app:app src ./src
COPY --chown=app:app config ./config
COPY --chown=app:app tests ./tests
RUN uv sync --frozen

CMD ["python", "-m", "jobmatch", "--help"]
