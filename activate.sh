#!/usr/bin/env bash
set -euo pipefail

config_name="${CONFIG_NAME:-neraverin@work-wsl}"
backup_ext="${BACKUP_EXT:-backup}"
force=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force)
      force=true
      shift
      ;;
    -h|--help)
      cat <<'EOF'
Usage: ./activate.sh [--force]

Options:
  --force   Remove seeded Codex and Claude configs before activation so Home Manager restores them.
EOF
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: ./activate.sh [--force]" >&2
      exit 1
      ;;
  esac
done

if [[ ! -f flake.nix ]]; then
  echo "activate.sh must be run from the dotfiles repository root." >&2
  exit 1
fi

if ! command -v nix >/dev/null 2>&1; then
  echo "Nix is not installed or is not available in PATH. Run ./bootstrap-nix.sh first." >&2
  exit 1
fi

if [[ "${force}" == true ]]; then
  codex_config="${HOME}/.codex/config.toml"
  if [[ -e "${codex_config}" ]]; then
    rm -- "${codex_config}"
    echo "Removed ${codex_config}; Home Manager will seed it during activation."
  fi

  claude_settings="${HOME}/.claude/settings.json"
  claude_statusline="${HOME}/.claude/statusline-command.sh"

  if [[ -e "${claude_settings}" ]]; then
    rm -- "${claude_settings}"
    echo "Removed ${claude_settings}; Home Manager will seed it during activation."
  fi

  if [[ -e "${claude_statusline}" ]]; then
    rm -- "${claude_statusline}"
    echo "Removed ${claude_statusline}; Home Manager will seed it during activation."
  fi
fi

nix build ".#homeConfigurations.\"${config_name}\".activationPackage"
HOME_MANAGER_BACKUP_EXT="${backup_ext}" "./result/activate"
