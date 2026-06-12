FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY scripts ./scripts

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir --no-compile --prefix=/install . \
    && python -c "from pathlib import Path; import re, shutil; root=Path('/install'); pat=re.compile(rb'-100[0-9][0-9_ -]*'); [p.write_bytes(pat.sub(b'<telegram-chat-id-example>', p.read_bytes())) for p in root.rglob('*') if p.is_file() and p.suffix in {'.py', '.pyi', '.txt', '.md'} and pat.search(p.read_bytes())]; [shutil.rmtree(p) for p in root.rglob('__pycache__') if p.is_dir()]"


FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local
COPY scripts ./scripts

COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["bot"]
