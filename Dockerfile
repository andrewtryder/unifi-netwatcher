FROM node:26-slim AS assets
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci
COPY tailwind.config.js postcss.config.js ./
COPY scripts ./scripts/
COPY app/web/static/tailwind.input.css ./app/web/static/tailwind.input.css
COPY app/web/templates ./app/web/templates
RUN npm run build:css

FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

FROM python:3.14-slim-bookworm
WORKDIR /app

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --shell /usr/sbin/nologin --create-home app \
    && mkdir -p /app/data /app/logs /tmp \
    && chown -R app:app /app /tmp

COPY --from=builder --chown=app:app /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
COPY --from=assets --chown=app:app /app/app/web/static/app.css ./app/web/static/app.css
COPY --from=assets --chown=app:app /app/app/web/static/fonts ./app/web/static/fonts

RUN chmod +x /app/scripts/docker-entrypoint.sh

USER app
EXPOSE 8080
ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
