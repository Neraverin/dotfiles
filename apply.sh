#!/usr/bin/env bash
# Apply config.yaml to this machine: everything under $HOME, plus the apt
# packages the host needs, plus the desktop applications when a desktop is
# detected (--gui / --no-gui decide instead). A wrapper and nothing more —
# provision/provision.py is the tool and takes every flag itself (--dry-run,
# --upgrade, --only, --gui).
set -euo pipefail

exec "$(dirname "$(readlink -f "$0")")/provision/provision.py" "$@"
