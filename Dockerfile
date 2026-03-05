FROM ghcr.io/astral-sh/uv:0.10.8-python3.13-trixie-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends aria2 ca-certificates

COPY pyproject.toml uv.lock* README.md /app/
COPY . /app

RUN uv venv
RUN uv pip install -e .

ENV IS_DOCKER=true PYTHONUNBUFFERED=1

CMD ["uv", "run", "minerva", "run"]
