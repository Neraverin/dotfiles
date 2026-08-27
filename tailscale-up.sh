#!/usr/bin/env bash
# Join the Headscale tailnet and pick up its advertised routes.
set -euo pipefail

login_server="${TAILSCALE_LOGIN_SERVER:-https://hs.ptsecurity.com}"

if ! command -v tailscale >/dev/null 2>&1; then
  echo "tailscale is not installed or is not available in PATH." >&2
  exit 1
fi

tailscale up --login-server "${login_server}" --accept-routes "$@"

# Without this, an exit node swallows the local subnet: tailscaled splits its
# 0.0.0.0/0 route into specific prefixes that cover the LAN, so the gateway
# stops answering. Setting it turns those into `throw` routes instead.
# `tailscale up` rejects the flag unless --exit-node is passed too; `tailscale
# set` has no such check, so it is applied separately.
tailscale set --exit-node-allow-lan-access=true
