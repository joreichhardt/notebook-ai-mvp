FROM ghcr.io/astral-sh/uv:0.9.9@sha256:f6e3549ed287fee0ddde2460a2a74a2d74366f84b04aaa34c1f19fec40da8652 AS uv
FROM cgr.dev/chainguard/python:latest-dev@sha256:50bef4bfe98ea2f3c8c146a04847c8857e0afb1c8803e2047f140f10c028a7ee AS build
USER 0:0
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
RUN ["chown", "10001:10001", "/app"]
COPY --chown=10001:10001 pyproject.toml uv.lock ./
COPY tests/security/readiness_image/check_build_limits.py /tmp/notebook-check-build-limits.py
ARG NOTEBOOK_TEST_LIMITS="0"
USER 10001:10001
RUN if [ "$NOTEBOOK_TEST_LIMITS" = "1" ]; then python -I /tmp/notebook-check-build-limits.py; else test "$NOTEBOOK_TEST_LIMITS" = "0"; fi \
    && /usr/local/bin/uv sync --frozen --no-dev --no-install-project --no-cache
COPY --chown=10001:10001 backend/app /app/app
# Generate trusted build metadata in the developer stage; runtime has no shell.
# An unstamped foundation image builds but can never pass release readiness.
ARG NOTEBOOK_RELEASE_SHA=""
RUN python -c 'import re,sys; from pathlib import Path; value=sys.argv[1]; assert not value or re.fullmatch(r"[0-9a-f]{40}", value), "invalid release SHA"; Path("/app/app/_release_sha").write_text(value, encoding="ascii")' "$NOTEBOOK_RELEASE_SHA"
FROM cgr.dev/chainguard/python:latest@sha256:6a467605cd347cb7af8404af3648d01c2b39a967c342b70bbd5ef39dd0b74f5c
WORKDIR /app
COPY --from=build --chown=0:0 /app/.venv /app/.venv
COPY --from=build --chown=0:0 /app/app /app/app
COPY backend/migrations /app/backend/migrations
ARG NOTEBOOK_RELEASE_SHA=""
LABEL org.opencontainers.image.revision=$NOTEBOOK_RELEASE_SHA
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
# Verify locked native dependencies load in the actual shell-free runtime.
RUN ["/app/.venv/bin/python", "-c", "import sys, ssl, psycopg, bitwarden_sdk; print(sys.version.split()[0])"]
ENTRYPOINT []
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health/live',timeout=2)"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers"]
