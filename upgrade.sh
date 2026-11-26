#!/usr/bin/env bash
# Shorthand for `apply.sh --upgrade`: apply config.yaml and re-check every
# upstream version on the way. Any further flags pass through (--dry-run,
# --only, --gui).
set -euo pipefail

exec "$(dirname "$(readlink -f "$0")")/apply.sh" --upgrade "$@"
