#!/usr/bin/env bash
# Activate the base profile used on servers and WSL.
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
CONFIG_NAME="neraverin@server" exec ./activate.sh "$@"
