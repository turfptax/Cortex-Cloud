#!/usr/bin/env bash
# Schedule the memory loop. The core runs in external-loop mode, so a small
# cron Job pokes the gateway's /ops/tick a few times a day; each poke wakes
# the scaled-to-zero app, runs one loop tick (summaries, narratives), and
# lets it idle back down. Cheap: ~4 short wake-ups/day.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
: "${RESOURCE_GROUP:?}" "${LOCATION:?}"

APP="cortex-cloud"
FQDN=$(az containerapp show -g "$RESOURCE_GROUP" -n "$APP" \
  --query properties.configuration.ingress.fqdn -o tsv)
URL="${CUSTOM_DOMAIN:+https://$CUSTOM_DOMAIN}"; URL="${URL:-https://$FQDN}"
# The service token authorizes /ops/tick (same value the app runs with).
TOKEN=$(az containerapp show -g "$RESOURCE_GROUP" -n "$APP" \
  --query "properties.template.containers[?name=='gateway'].env" -o tsv >/dev/null 2>&1; \
  az containerapp secret show -g "$RESOURCE_GROUP" -n "$APP" --secret-name service-token \
  --query value -o tsv)

ENV_ID=$(az containerapp env show -g "$RESOURCE_GROUP" -n cortex-cloud-env --query id -o tsv)
az containerapp job create -g "$RESOURCE_GROUP" -n cortex-cloud-tick \
  --environment "$ENV_ID" \
  --trigger-type Schedule --cron-expression "0 3,9,15,21 * * *" \
  --replica-timeout 600 --replica-retry-limit 1 \
  --image curlimages/curl:latest \
  --cpu 0.25 --memory 0.5Gi \
  --secrets "svc-token=$TOKEN" \
  --env-vars "TICK_URL=$URL/ops/tick" "TICK_TOKEN=secretref:svc-token" \
  --command "/bin/sh" "-c" \
  "curl -fsS -X POST -H \"Authorization: Bearer \$TICK_TOKEN\" \"\$TICK_URL\"" \
  -o none
echo "scheduled cortex-cloud-tick (0 3,9,15,21 UTC) -> $URL/ops/tick"
