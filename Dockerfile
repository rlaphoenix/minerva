FROM python:3.13-slim AS builder

ENV IS_DOCKER=true \
    DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PATH="/root/.local/bin:$PATH"

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        build-essential \
        git && \
    rm -rf /var/lib/apt/lists/*

RUN curl -Ls https://astral.sh/uv/install.sh | sh

WORKDIR /app

COPY pyproject.toml uv.lock* README.md ./
COPY . .

RUN uv venv

RUN uv pip install -e .

FROM python:3.13-slim AS runtime

ENV IS_DOCKER=true \
    PYTHONUNBUFFERED=1 \
    PATH="/root/.local/bin:$PATH"

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        aria2 \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/*

RUN curl -Ls https://astral.sh/uv/install.sh | sh

WORKDIR /app

# Copy venv and app from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app /app

# Default command
CMD ["uv", "run", "minerva", "run"]
