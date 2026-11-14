FROM ghcr.io/astral-sh/uv:0.9.9@sha256:f6e3549ed287fee0ddde2460a2a74a2d74366f84b04aaa34c1f19fec40da8652 AS uv
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-cache
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY backend/app /app/app
COPY backend/migrations /app/backend/migrations
# Non-secret trusted build metadata, not a runtime environment override.
# An unstamped foundation image builds but can never pass release readiness.
ARG NOTEBOOK_RELEASE_SHA=""
RUN python -c 'import re,sys; from pathlib import Path; value=sys.argv[1]; assert not value or re.fullmatch(r"[0-9a-f]{40}", value), "invalid release SHA"; Path("/app/app/_release_sha").write_text(value, encoding="ascii")' "$NOTEBOOK_RELEASE_SHA"
LABEL org.opencontainers.image.revision=$NOTEBOOK_RELEASE_SHA
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health/live',timeout=2)"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers"]
