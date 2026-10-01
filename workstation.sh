#!/usr/bin/env bash
# Install the Ubuntu-desktop software declared in config.yaml under
# `workstation`. A wrapper and nothing more: provision/workstation.py adds
# --workstation and hands everything else to provision.py, so --dry-run,
# --verbose, --only and --upgrade all work here too.
set -euo pipefail

exec "$(dirname "$(readlink -f "$0")")/provision/workstation.py" "$@"
