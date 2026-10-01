#!/usr/bin/env bash
# Apply config.yaml to this machine: everything under $HOME, plus the apt
# packages the host needs. A wrapper and nothing more — provision/provision.py
# is the tool and takes every flag itself (--dry-run, --upgrade, --only, --gui).
# System-wide desktop software is not installed here; that is ./workstation.sh.
set -euo pipefail

exec "$(dirname "$(readlink -f "$0")")/provision/provision.py" "$@"
