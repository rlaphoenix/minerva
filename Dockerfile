FROM ghcr.io/astral-sh/uv:0.10.8-python3.13-trixie-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first for better layer caching
COPY pyproject.toml uv.lock README.md /app/
RUN uv sync --frozen --no-dev --no-install-project

# Copy source and install the project
COPY . /app
RUN uv sync --frozen --no-dev

ENV IS_DOCKER=true PYTHONUNBUFFERED=1

ENTRYPOINT ["uv", "run", "minerva"]
CMD ["run"]
