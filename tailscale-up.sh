#!/usr/bin/env bash
# Join the Headscale tailnet and pick up its advertised routes.
set -euo pipefail

login_server="${TAILSCALE_LOGIN_SERVER:-https://hs.ptsecurity.com}"

if ! command -v tailscale >/dev/null 2>&1; then
  echo "tailscale is not installed or is not available in PATH." >&2
  exit 1
fi

tailscale up --login-server "${login_server}" --accept-routes "$@"
