#!/usr/bin/env bash
# Build the combined Cortex Cloud image and push it to your ACR.
#
# The image bundles four roles from three repos:
#   - this repo (Cortex-Cloud)          -> the gateway  (/app/gateway)
#   - cortex-core                        -> the engine   (/app/core)
#   - cortex-desktop (built SPA)         -> the web Hub  (/app/gateway/hub_static)
#
# It clones the two sibling repos next to this one (if not already present),
# builds the React SPA, assembles a clean build context, and runs
# `az acr build`. Set ACR + IMAGE_TAG (deploy.sh does this from .env).
#
# Prereqs: git, node/npm (for the SPA), az CLI logged in.
set -euo pipefail

: "${ACR:?set ACR (your container registry name)}"
: "${IMAGE_TAG:=latest}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"          # Cortex-Cloud repo root
WORK="$(dirname "$HERE")"                          # parent dir for siblings
CORE="${CORTEX_CORE_DIR:-$WORK/cortex-core}"
DESKTOP="${CORTEX_DESKTOP_DIR:-$WORK/cortex-desktop}"

clone_if_missing() { [ -d "$2/.git" ] || git clone "$1" "$2"; }
clone_if_missing "https://github.com/turfptax/cortex-core.git" "$CORE"
clone_if_missing "https://github.com/turfptax/cortex-desktop.git" "$DESKTOP"

echo ">> building the web Hub SPA"
( cd "$DESKTOP/hub/frontend" && npm ci && npm run build )
[ -f "$DESKTOP/hub/frontend/dist/index.html" ] || { echo "SPA build produced no dist/"; exit 1; }

echo ">> assembling the build context"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/core" "$STAGE/gateway"
cp -r "$CORE/src" "$STAGE/core/src"
cp -r "$CORE/plugins" "$STAGE/core/plugins"
cp -r "$HERE/cortex_gateway" "$STAGE/gateway/cortex_gateway"
cp -r "$DESKTOP/hub/frontend/dist" "$STAGE/gateway/hub_static"
cp "$HERE/deploy/Dockerfile" "$HERE/deploy/litestream.yml" \
   "$HERE/deploy/litestream-restore.sh" "$HERE/deploy/litestream-replicate.sh" "$STAGE/"

# Refuse to ship data- or identity-shaped files.
LEAKS=$(find "$STAGE" -type f \( -name '*.db' -o -name '*.jsonl' \
  -o -name 'USER.md' -o -name 'OVERSEER.md' -o -name 'APP.md' -o -name 'secrets.toml' \))
[ -z "$LEAKS" ] || { echo "LEAK - refusing to build:"; echo "$LEAKS"; exit 1; }

echo ">> az acr build -> $ACR.azurecr.io/cortex-cloud:$IMAGE_TAG"
az acr build --registry "$ACR" --image "cortex-cloud:$IMAGE_TAG" "$STAGE"
echo "IMAGE=$ACR.azurecr.io/cortex-cloud:$IMAGE_TAG"
