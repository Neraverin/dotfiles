#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -eq 0 ]]; then
  sudo_cmd=()
else
  if ! command -v sudo >/dev/null 2>&1; then
    echo "This script needs sudo when it is not run as root." >&2
    exit 1
  fi
  sudo_cmd=(sudo)
fi

run_root() {
  "${sudo_cmd[@]}" "$@"
}

install_system_packages() {
  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
  else
    echo "Cannot detect OS: /etc/os-release is missing." >&2
    exit 1
  fi

  case "${ID}" in
    debian | ubuntu)
      run_root apt-get update
      run_root apt-get install -y ca-certificates curl git sudo tar xz-utils
      ;;
    rhel | centos | rocky | almalinux | fedora)
      if command -v dnf >/dev/null 2>&1; then
        run_root dnf install -y ca-certificates curl git gzip shadow-utils sudo tar xz
      elif command -v yum >/dev/null 2>&1; then
        run_root yum install -y ca-certificates curl git gzip shadow-utils sudo tar xz
      else
        echo "RHEL-like system detected, but neither dnf nor yum is available." >&2
        exit 1
      fi
      ;;
    *)
      echo "Unsupported OS '${ID}'. This bootstrap supports Debian/Ubuntu and RHEL-like systems." >&2
      exit 1
      ;;
  esac
}

load_nix_profile() {
  if [[ -r /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh ]]; then
    # shellcheck disable=SC1091
    . /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh
  elif [[ -r "${HOME}/.nix-profile/etc/profile.d/nix.sh" ]]; then
    # shellcheck disable=SC1091
    . "${HOME}/.nix-profile/etc/profile.d/nix.sh"
  fi
}

install_nix() {
  if command -v nix >/dev/null 2>&1; then
    return
  fi

  curl --proto '=https' --tlsv1.2 -L https://nixos.org/nix/install | sh -s -- --daemon --yes
  load_nix_profile
}

ensure_nix_conf() {
  run_root mkdir -p /etc/nix
  run_root touch /etc/nix/nix.conf

  if run_root grep -q '^experimental-features[[:space:]]*=' /etc/nix/nix.conf; then
    current_features="$(run_root sed -n 's/^experimental-features[[:space:]]*=[[:space:]]*//p' /etc/nix/nix.conf | tail -n 1)"

    for feature in nix-command flakes; do
      case " ${current_features} " in
        *" ${feature} "*) ;;
        *) current_features="${current_features} ${feature}" ;;
      esac
    done

    current_features="${current_features#"${current_features%%[![:space:]]*}"}"
    run_root sed -i "s/^experimental-features[[:space:]]*=.*/experimental-features = ${current_features}/" /etc/nix/nix.conf
  else
    printf '%s\n' 'experimental-features = nix-command flakes' | run_root tee -a /etc/nix/nix.conf >/dev/null
  fi
}

restart_nix_daemon() {
  if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files nix-daemon.service >/dev/null 2>&1 2>/dev/null; then
    run_root systemctl restart nix-daemon.service
  fi
}

install_system_packages
install_nix
ensure_nix_conf
restart_nix_daemon
load_nix_profile

nix --version
echo "Nix is ready. Run ./activate.sh from this repository to activate the home-manager environment."
