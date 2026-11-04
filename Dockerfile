FROM ollama/ollama:latest

# Image Studio (SDXL) pulls ~4GB of PyTorch wheels; opt in with --build-arg INSTALL_IMAGEGEN=1
ARG INSTALL_IMAGEGEN=0

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    OLLAMA_HOST=0.0.0.0:11434 \
    OLLAMA_LOAD_TIMEOUT=20m

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv curl ca-certificates openssh-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

COPY requirements.txt requirements-imagegen.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$INSTALL_IMAGEGEN" = "1" ]; then pip install --no-cache-dir -r requirements-imagegen.txt; fi

COPY . .
RUN chmod +x docker/entrypoint.sh

ENV DEMO_CONFIG=/app/profiles/active.json
EXPOSE 8080 9101 11434
VOLUME ["/root/.ollama", "/app/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
    CMD curl -sf http://localhost:8080/api/device || curl -sf http://localhost:9101/api/device || exit 1

ENTRYPOINT ["/app/docker/entrypoint.sh"]
