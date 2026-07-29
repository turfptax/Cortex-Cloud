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

# Runtime deps come from gateway/requirements.txt, which is the SINGLE source
# of truth: this image and the CI workflows install from the same file, so a
# pin there protects both. This used to be a hand-copied list, and on
# 2026-07-29 that drift took production down: it said bare `mcp`, mcp 2.0.0
# removed mcp.server.fastmcp, and the gateway died at import while CI was green
# because CI installed a different list again.
#
# The three extras below are NOT gateway deps and are why this cannot simply be
# `-r requirements.txt` alone: they serve the core, which is co-located in this
# image (sqlite-vec for the vector index, requests for the MCP client, httpx
# for the gateway's own upstream calls). Anything else belongs in the file.
COPY gateway/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
        sqlite-vec requests httpx \
 && python -c "import mcp.server.fastmcp, fastapi, sqlalchemy" \
 && rm /tmp/requirements.txt

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
