#!/usr/bin/env bash
set -euo pipefail

config_name="${CONFIG_NAME:-neraverin@work-wsl}"

if [[ ! -f flake.nix ]]; then
  echo "activate.sh must be run from the dotfiles repository root." >&2
  exit 1
fi

if ! command -v nix >/dev/null 2>&1; then
  echo "Nix is not installed or is not available in PATH. Run ./bootstrap-nix.sh first." >&2
  exit 1
fi

nix build ".#homeConfigurations.\"${config_name}\".activationPackage"
"./result/activate"
