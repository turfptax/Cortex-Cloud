#!/usr/bin/env bash
# Create the Entra (Azure AD) app registration that gates the web Hub.
#
# The Hub is locked to ONE person: you. This script registers a single-
# tenant app, turns on ID-token issuance (Easy Auth needs it), and turns
# ON app-role-assignment-required with you as the only assigned user, so
# nobody else in your tenant can even sign in.
#
# Prints APP_ID and CLIENT_SECRET for deploy.sh to wire into Easy Auth.
# Run:  source deploy/entra-setup.sh   (so the exports survive), or capture
#       the printed values.
set -euo pipefail

: "${APP_NAME:=cortex-cloud-auth}"
: "${PUBLIC_URL:?set PUBLIC_URL (e.g. https://<app>.azurecontainerapps.io or your domain)}"
: "${OWNER_OID:?set OWNER_OID (az ad signed-in-user show --query id -o tsv)}"

REDIRECT="${PUBLIC_URL%/}/.auth/login/aad/callback"

echo ">> creating Entra app '$APP_NAME' (redirect: $REDIRECT)"
APP_ID=$(az ad app create \
  --display-name "$APP_NAME" \
  --sign-in-audience AzureADMyOrg \
  --web-redirect-uris "$REDIRECT" \
  --query appId -o tsv)
echo "   appId: $APP_ID"

# Easy Auth requires ID tokens; `az ad app create` leaves this false, which
# makes the very first browser login fail with a blank/error page. Turn it on.
OBJ_ID=$(az ad app show --id "$APP_ID" --query id -o tsv)
az rest --method patch \
  --url "https://graph.microsoft.com/v1.0/applications/$OBJ_ID" \
  --headers "Content-Type=application/json" \
  --body '{"web":{"implicitGrantSettings":{"enableIdTokenIssuance":true}}}'

echo ">> creating a client secret"
CLIENT_SECRET=$(az ad app credential reset --id "$APP_ID" \
  --display-name easy-auth --query password -o tsv)

echo ">> creating the service principal + locking sign-in to the owner"
az ad sp create --id "$APP_ID" >/dev/null 2>&1 || true
SP_OID=$(az ad sp show --id "$APP_ID" --query id -o tsv)
# Assign the owner (default-access role) BEFORE requiring assignment, so you
# don't lock yourself out.
az rest --method post \
  --url "https://graph.microsoft.com/v1.0/servicePrincipals/$SP_OID/appRoleAssignedTo" \
  --headers "Content-Type=application/json" \
  --body "{\"principalId\":\"$OWNER_OID\",\"resourceId\":\"$SP_OID\",\"appRoleId\":\"00000000-0000-0000-0000-000000000000\"}" \
  >/dev/null 2>&1 || echo "   (owner may already be assigned)"
az rest --method patch \
  --url "https://graph.microsoft.com/v1.0/servicePrincipals/$SP_OID" \
  --headers "Content-Type=application/json" \
  --body '{"appRoleAssignmentRequired":true}'

export APP_ID CLIENT_SECRET
echo ""
echo "APP_ID=$APP_ID"
echo "CLIENT_SECRET=$CLIENT_SECRET   # feed into Easy Auth; do not commit"
