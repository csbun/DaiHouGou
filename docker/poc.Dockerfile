FROM python:3.12.11-slim-bookworm

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

COPY pyproject.toml ./
COPY src ./src
COPY tests ./tests

RUN pip install --no-cache-dir '.[dev]'

ENTRYPOINT ["daihougou-poc"]
