FROM ghcr.io/astral-sh/uv:0.9.9@sha256:f6e3549ed287fee0ddde2460a2a74a2d74366f84b04aaa34c1f19fec40da8652 AS uv
FROM python:3.12-slim@sha256:05a994d2344eb364cc186910f4fc8786a38215f39f00d0acc25bff33a08f4177
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /workspace
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-cache
COPY backend ./backend
ENV PATH="/workspace/.venv/bin:$PATH" PYTHONPATH=/workspace/backend PYTHONDONTWRITEBYTECODE=1
CMD ["python", "backend/tests/storage_real/phase_a.py"]
