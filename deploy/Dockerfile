# Cortex Cloud image. ONE image, three roles selected by the container
# command in the Container App template:
#   core       : python /app/core/src/headless_main.py   (corpus writer + loop)
#   gateway    : python -m uvicorn cortex_gateway.app:app (public surface, ATTACH mode)
#   litestream : /app/litestream-replicate.sh            (restore runs as an init container)
#
# Self-contained monorepo build: the build context is the repo root and the
# web Hub SPA is built inline, so there is no stage.py and no sibling
# checkouts. Build with:
#   az acr build --registry <acr> --image cortex-cloud:<tag> --file deploy/Dockerfile .

# --- Stage 1: build the web Hub SPA (the gateway serves it in cloud mode) ---
FROM node:20-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# --- Stage 2: the Python runtime ---
FROM python:3.12-slim

# NB: this list is a THIRD copy of the runtime deps (the others are
# gateway/requirements.txt and the CI workflows). This copy is the one that
# actually builds production, so an unpinned entry here ships a broken image
# even when CI is green. That is exactly what happened on 2026-07-29: bare
# `mcp` resolved to 2.0.0, which removed mcp.server.fastmcp, and the gateway
# died at import with the revision still reporting Healthy.
# Keep the mcp bound in sync with gateway/requirements.txt. Unifying all three
# onto that file is the real fix and is tracked separately.
RUN pip install --no-cache-dir \
    sqlite-vec requests httpx \
    fastapi "uvicorn[standard]" "mcp>=1.2,<2" pydantic sqlalchemy \
    python-multipart

# Litestream for SQLite replication to Azure Blob.
ADD https://github.com/benbjohnson/litestream/releases/download/v0.3.13/litestream-v0.3.13-linux-amd64.tar.gz /tmp/ls.tar.gz
RUN tar -C /usr/local/bin -xzf /tmp/ls.tar.gz litestream && rm /tmp/ls.tar.gz

WORKDIR /app
COPY core/ /app/core/
COPY gateway/cortex_gateway/ /app/gateway/cortex_gateway/
COPY --from=web /web/dist /app/gateway/hub_static
COPY deploy/litestream.yml /etc/litestream.yml
COPY deploy/litestream-restore.sh /app/litestream-restore.sh
COPY deploy/litestream-replicate.sh /app/litestream-replicate.sh
RUN chmod +x /app/litestream-restore.sh /app/litestream-replicate.sh

ENV PYTHONUNBUFFERED=1

# Default command = core. The gateway and litestream containers override the
# command in the Container App template. The gateway sets PYTHONPATH=/app/gateway
# so `import cortex_gateway` resolves, and CORTEX_CORE_PATH=/app/core so it can
# reach the core's corpus/detail engines.
CMD ["python", "/app/core/src/headless_main.py"]
