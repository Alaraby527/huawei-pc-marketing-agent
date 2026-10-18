FROM node:22-bookworm-slim AS node_runtime
FROM python:3.12-slim-bookworm
LABEL org.opencontainers.image.title="huawei-pc-marketing-agent" \
      org.opencontainers.image.source="https://github.com/cangyuyi/huawei-pc-marketing-agent" \
      org.opencontainers.image.description="Evidence-first Huawei PC marketing agent"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=node_runtime /usr/local/bin/node /usr/local/bin/node
WORKDIR /app
COPY requirements.lock ./
RUN python -m pip install --no-cache-dir -r requirements.lock
RUN useradd --create-home --uid 1000 appuser
COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser vendor ./vendor
COPY --chown=appuser:appuser prompts ./prompts
USER appuser
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/healthz',timeout=3)"
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "1"]
