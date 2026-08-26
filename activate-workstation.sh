#!/usr/bin/env bash
# Activate the workstation profile: the base profile plus desktop extras.
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
CONFIG_NAME="neraverin@workstation" exec ./activate.sh "$@"
