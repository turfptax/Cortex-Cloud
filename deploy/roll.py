#!/usr/bin/env python3
"""Roll every cortex-cloud container in the cortex-solo Container App to a new
image tag, via an ARM template PATCH. Called by the deploy workflow after the
image is built (the az session is already logged in via OIDC).

Usage: python deploy/roll.py <tag>
"""
import json
import os
import subprocess
import sys
import tempfile

RG = "cortex-rg"
APP = "cortex-solo"
REGISTRY = "cortexacr47df.azurecr.io"


def az(*args: str) -> str:
    return subprocess.check_output(["az", *args], text=True)


def main() -> int:
    tag = sys.argv[1]
    image = f"{REGISTRY}/cortex-cloud:{tag}"
    sub = az("account", "show", "--query", "id", "-o", "tsv").strip()
    url = (f"https://management.azure.com/subscriptions/{sub}/resourceGroups/"
           f"{RG}/providers/Microsoft.App/containerApps/{APP}"
           "?api-version=2024-03-01")
    cur = json.loads(az("rest", "--method", "get", "--url", url))
    t = cur["properties"]["template"]

    rolled = 0
    for c in (t.get("initContainers") or []) + t["containers"]:
        img = c.get("image", "")
        # roll the app's own containers (core/gateway/litestream/restore),
        # not the pinned third-party embed image.
        if "cortex-cloud" in img or "cortex-solo" in img:
            c["image"] = image
            rolled += 1

    # a fresh revision each deploy; suffix must be lowercase alnum + hyphens
    t["revisionSuffix"] = tag.lower().replace(".", "-")[:40]
    # scale sub-fields are read-only on PATCH; strip them or ARM 400s.
    scale = t.get("scale") or {}
    for k in ("cooldownPeriod", "pollingInterval"):
        scale.pop(k, None)

    body = json.dumps({"properties": {"template": t}})
    fd, path = tempfile.mkstemp(suffix=".json")
    os.write(fd, body.encode())
    os.close(fd)
    try:
        az("rest", "--method", "patch", "--url", url, "--body", f"@{path}")
    finally:
        os.unlink(path)
    print(f"rolled {rolled} containers to {image}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
