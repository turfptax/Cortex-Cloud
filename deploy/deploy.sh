#!/usr/bin/env bash
# Cortex Cloud - one-shot deploy of your own instance.
#
# Provisions everything in YOUR Azure subscription: a resource group, a
# container registry, a storage account (Litestream + imports), a Key
# Vault, an Entra app locked to you, and the Container App running the
# four-container image with scale-to-zero. Idempotent-ish: safe to re-run;
# existing resources are reused.
#
# Usage:
#   cp .env.example .env && edit .env
#   bash deploy/deploy.sh
#
# This CODIFIES the exact sequence used to stand up the reference instance.
# It has NOT been tested end-to-end against a fresh subscription in this
# repo's CI, so run it once in a THROWAWAY resource group first and read
# the output at each step. Requires: az CLI (logged in), git, node/npm,
# and gettext (`envsubst`).
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "copy .env.example to .env and fill it in first"; exit 1; }
set -a; . ./.env; set +a

: "${RESOURCE_GROUP:?}" "${LOCATION:?}" "${NAME_SUFFIX:?}" "${OWNER_OID:?}" "${OPENROUTER_API_KEY:?}"
ACR="cortexcloud${NAME_SUFFIX}"
STORAGE_ACCOUNT="cortexcloud${NAME_SUFFIX}"
KV="cortex-kv-${NAME_SUFFIX}"
ENV_NAME="cortex-cloud-env"
APP="cortex-cloud"
TENANT_TZ="${CORTEX_TENANT_TZ:-America/Chicago}"
GROQ_API_KEY="${GROQ_API_KEY:-}"
ELEVENLABS_API_KEY="${ELEVENLABS_API_KEY:-}"
# Owner identity: set CORTEX_OWNER_NAME / CORTEX_OWNER_EMAIL in your deploy
# env to personalize the instance. Empty = generic "the owner" (repo ships
# no personal name). Kept OUT of the committed template on purpose.
OWNER_NAME="${CORTEX_OWNER_NAME:-}"
OWNER_EMAIL="${CORTEX_OWNER_EMAIL:-}"

echo "== [1/9] resource group =="
az group create -n "$RESOURCE_GROUP" -l "$LOCATION" -o none

echo "== [2/9] container registry =="
az acr create -g "$RESOURCE_GROUP" -n "$ACR" --sku Basic --admin-enabled true -o none
ACR_PWD=$(az acr credential show -n "$ACR" --query "passwords[0].value" -o tsv)

echo "== [3/9] storage (litestream + imports containers) =="
az storage account create -g "$RESOURCE_GROUP" -n "$STORAGE_ACCOUNT" -l "$LOCATION" \
  --sku Standard_LRS -o none
STORAGE_KEY=$(az storage account keys list -g "$RESOURCE_GROUP" -n "$STORAGE_ACCOUNT" \
  --query "[0].value" -o tsv)
for c in litestream imports-raw; do
  az storage container create --account-name "$STORAGE_ACCOUNT" --account-key "$STORAGE_KEY" \
    -n "$c" -o none
done

echo "== [4/9] key vault + LLM secret =="
az keyvault create -g "$RESOURCE_GROUP" -n "$KV" -l "$LOCATION" \
  --enable-rbac-authorization false -o none
az keyvault secret set --vault-name "$KV" -n openrouter --value "$OPENROUTER_API_KEY" -o none

echo "== [5/9] Entra app (sign-in locked to you) =="
export APP_NAME="cortex-cloud-auth"
# PUBLIC_URL isn't known until the app exists; register with a placeholder,
# then deploy.sh patches the real redirect after the app is created.
export PUBLIC_URL="${CUSTOM_DOMAIN:+https://$CUSTOM_DOMAIN}"
export PUBLIC_URL="${PUBLIC_URL:-https://placeholder.local}"
. deploy/entra-setup.sh    # sets APP_ID, CLIENT_SECRET

echo "== [6/9] build image (or use \$IMAGE from .env) =="
if [ -z "${IMAGE:-}" ]; then
  export ACR IMAGE_TAG="v1"
  IMAGE="$ACR.azurecr.io/cortex-cloud:v1"
  bash deploy/build-image.sh
fi

echo "== [7/9] container app environment =="
az containerapp env create -g "$RESOURCE_GROUP" -n "$ENV_NAME" -l "$LOCATION" -o none
ENV_ID=$(az containerapp env show -g "$RESOURCE_GROUP" -n "$ENV_NAME" --query id -o tsv)

echo "== [8/9] deploy the container app =="
SERVICE_TOKEN="$(openssl rand -hex 24)"
export LOCATION ENV_ID ACR ACR_PWD KV STORAGE_ACCOUNT STORAGE_KEY SERVICE_TOKEN \
       IMAGE TENANT_TZ OWNER_OID GROQ_API_KEY ELEVENLABS_API_KEY \
       OWNER_NAME OWNER_EMAIL
# PUBLIC_URL: use the custom domain if set, else fill after first deploy.
export PUBLIC_URL="${CUSTOM_DOMAIN:+https://$CUSTOM_DOMAIN}"
export PUBLIC_URL="${PUBLIC_URL:-https://placeholder.local}"
envsubst < deploy/containerapp.tmpl.yaml > /tmp/cortex-app.yaml
az containerapp create -g "$RESOURCE_GROUP" -n "$APP" --yaml /tmp/cortex-app.yaml -o none
rm -f /tmp/cortex-app.yaml

# Grant the app's managed identity read access to Key Vault (the openrouter
# secret ref resolves after this on the next revision).
PRINCIPAL=$(az containerapp show -g "$RESOURCE_GROUP" -n "$APP" \
  --query identity.principalId -o tsv)
az keyvault set-policy -n "$KV" --object-id "$PRINCIPAL" --secret-permissions get -o none

FQDN=$(az containerapp show -g "$RESOURCE_GROUP" -n "$APP" \
  --query properties.configuration.ingress.fqdn -o tsv)
REAL_URL="${CUSTOM_DOMAIN:+https://$CUSTOM_DOMAIN}"; REAL_URL="${REAL_URL:-https://$FQDN}"

echo "== [9/9] Easy Auth (Entra) + owner sign-in =="
az containerapp auth microsoft update -g "$RESOURCE_GROUP" -n "$APP" \
  --client-id "$APP_ID" --client-secret "$CLIENT_SECRET" \
  --tenant-id "$(az account show --query tenantId -o tsv)" \
  --yes -o none
az containerapp auth update -g "$RESOURCE_GROUP" -n "$APP" \
  --unauthenticated-client-action AllowAnonymous -o none

# If no custom domain, the real URL is only known now - push it into the
# gateway (OAuth issuer) and the Entra redirect.
if [ -z "${CUSTOM_DOMAIN:-}" ]; then
  az containerapp update -g "$RESOURCE_GROUP" -n "$APP" --container-name gateway \
    --set-env-vars "GATEWAY_PUBLIC_URL=$REAL_URL" -o none
  OBJ=$(az ad app show --id "$APP_ID" --query id -o tsv)
  az rest --method patch --url "https://graph.microsoft.com/v1.0/applications/$OBJ" \
    --headers "Content-Type=application/json" \
    --body "{\"web\":{\"redirectUris\":[\"$REAL_URL/.auth/login/aad/callback\"]}}"
fi

cat <<DONE

  Cortex Cloud is deploying.
  URL:        $REAL_URL
  Sign in:    your Microsoft account ($OWNER_OID) - nobody else can.
  Tick job:   run deploy/tick-job.sh to schedule the memory loop.
  Custom domain: see docs/ARCHITECTURE.md.
DONE
