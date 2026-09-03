# syntax=docker/dockerfile:1

FROM python:3.12.11-slim-bookworm

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

COPY pyproject.toml ./
COPY .dockerignore compose.yaml .env.example ./
COPY docker ./docker
COPY src ./src
COPY tests ./tests

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install '.[dev]'

ENTRYPOINT ["guduck-poc"]
