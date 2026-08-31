#!/usr/bin/env bash
# Apply config.yaml to this machine. A wrapper and nothing more: provision.py is
# the tool and takes every flag itself (--dry-run, --upgrade, --only, --gui).
# It exists because the Nix setup had an ./activate.sh and the muscle memory
# outlived it; provision.py resolves the repository from its own path, so this
# adds nothing but a shorter name.
set -euo pipefail

exec "$(dirname "$(readlink -f "$0")")/provision/provision.py" "$@"
