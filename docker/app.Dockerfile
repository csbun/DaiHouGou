# syntax=docker/dockerfile:1

FROM python:3.12.11-slim-bookworm

ARG MODEL_URL=https://media.githubusercontent.com/media/opencv/opencv_zoo/47534e27c9851bb1128ccc0102f1145e27f23f98/models/person_detection_mediapipe/person_detection_mediapipe_2023mar.onnx
ARG MODEL_SHA384=cdc21e3741c46ae24e4d2fa3c368886bd7dadcd23d98b6acdc0db966d2d9ecc5624c095fa05d5f949cce69ef1029f9ef
ARG OBJECT_MODEL_URL=https://media.githubusercontent.com/media/opencv/opencv_zoo/47534e27c9851bb1128ccc0102f1145e27f23f98/models/object_detection_nanodet/object_detection_nanodet_2022nov.onnx
ARG OBJECT_MODEL_SHA384=84ee6a6dd605f7019f25a81615a8fff886b235e8d3924930ca367c6e239a8c6d9c14a7e60b8bae54edca040cbf7b86e7

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

COPY pyproject.toml ./
COPY .dockerignore compose.yaml .env.example ./
COPY docker ./docker
COPY src ./src
COPY tests ./tests
COPY third_party/nanodet/LICENSE /opt/guduck/licenses/nanodet.LICENSE
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install '.[dev]'

RUN mkdir -p /opt/guduck/models \
    && curl --fail --silent --show-error --location \
      "$MODEL_URL" \
      --output /opt/guduck/models/person_detection_mediapipe_2023mar.onnx \
    && cd /opt/guduck/models \
    && printf '%s  %s\n' "$MODEL_SHA384" person_detection_mediapipe_2023mar.onnx \
      | sha384sum --check

RUN curl --fail --silent --show-error --location \
      "$OBJECT_MODEL_URL" \
      --output /opt/guduck/models/object_detection_nanodet_2022nov.onnx \
    && cd /opt/guduck/models \
    && printf '%s  %s\n' "$OBJECT_MODEL_SHA384" object_detection_nanodet_2022nov.onnx \
      | sha384sum --check

ENTRYPOINT ["guduck"]
