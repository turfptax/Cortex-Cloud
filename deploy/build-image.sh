#!/usr/bin/env bash
# Build the Cortex Cloud image from this monorepo and push it to your ACR.
# Self-contained: the multi-stage Dockerfile builds the web SPA and the
# Python runtime in one shot, so there is no separate stage or SPA build.
#
# Prereqs: az CLI logged in. Set ACR + IMAGE_TAG (deploy.sh does this).
set -euo pipefail
cd "$(dirname "$0")/.."          # repo root = the build context
: "${ACR:?set ACR (your container registry name)}"
: "${IMAGE_TAG:=latest}"
echo ">> az acr build -> $ACR.azurecr.io/cortex-cloud:$IMAGE_TAG"
az acr build --registry "$ACR" --image "cortex-cloud:$IMAGE_TAG" \
  --file deploy/Dockerfile .
echo "IMAGE=$ACR.azurecr.io/cortex-cloud:$IMAGE_TAG"
