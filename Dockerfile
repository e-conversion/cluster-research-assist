# Serving image: no build extra, so no scikit-learn, umap or numba. The library
# bundle is mounted at runtime, already carrying its derived artifacts.
FROM python:3.13-slim AS build

ARG VERSION=0.0.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CLUSTER_RESEARCH_ASSIST=${VERSION} \
    PIP_NO_CACHE_DIR=1

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install ".[postgres]"

FROM python:3.13-slim

ARG VERSION=0.0.0
LABEL org.opencontainers.image.title="Cluster Research Assistant" \
      org.opencontainers.image.version=${VERSION} \
      org.opencontainers.image.licenses="Apache-2.0"

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    CRA_HOST=0.0.0.0 \
    CRA_PORT=8501 \
    CRA_LIBRARY_PATH=/srv/library \
    CRA_LOG_DIR=/var/log/cra

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --create-home --uid 10001 cra \
    && mkdir -p /srv/library /var/log/cra && chown cra /var/log/cra

COPY --from=build /opt/venv /opt/venv

USER cra
EXPOSE 8501

# the app answers under CRA_BASE_PATH, so the check has to use it too
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD curl -fsS "http://127.0.0.1:${CRA_PORT}${CRA_BASE_PATH}/api/health" >/dev/null || exit 1

ENTRYPOINT ["cra", "serve"]
