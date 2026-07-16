FROM ghcr.io/astral-sh/uv:0.11.3 AS uv-bin


FROM python:3.11-slim AS python-base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=uv-bin /uv /uvx /usr/local/bin/
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY scripts ./scripts


FROM python-base AS runtime

ARG SOURCE_COMMIT=unknown
LABEL org.opencontainers.image.revision=$SOURCE_COMMIT

RUN uv sync --locked --no-dev --no-editable --no-cache \
    && python -c "from pathlib import Path; import re, shutil; root=Path('/app/.venv'); pat=re.compile(rb'-100[0-9][0-9_ -]*'); [p.write_bytes(pat.sub(b'<telegram-chat-id-example>', p.read_bytes())) for p in root.rglob('*') if p.is_file() and p.suffix in {'.py', '.pyi', '.txt', '.md'} and pat.search(p.read_bytes())]; [shutil.rmtree(p) for p in root.rglob('__pycache__') if p.is_dir()]" \
    && rm -f /usr/local/bin/uv /usr/local/bin/uvx

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["bot"]


FROM python-base AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN uv sync --locked --no-dev --no-editable --extra build --no-cache \
    && python -c "from pathlib import Path; import re, shutil; root=Path('/app/.venv'); pat=re.compile(rb'-100[0-9][0-9_ -]*'); [p.write_bytes(pat.sub(b'<telegram-chat-id-example>', p.read_bytes())) for p in root.rglob('*') if p.is_file() and p.suffix in {'.py', '.pyi', '.txt', '.md'} and pat.search(p.read_bytes())]; [shutil.rmtree(p) for p in root.rglob('__pycache__') if p.is_dir()]"

COPY deploy/builder-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["verify-db"]
