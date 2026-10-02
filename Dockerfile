# Playwright image ships Chromium and its system libraries.
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.8.19 /uv /bin/uv

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project

COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen

ENV PATH="/app/.venv/bin:$PATH" HUAWEI_PROFILE_DIR=/data/profile
EXPOSE 8000
CMD ["huawei-mcp"]
