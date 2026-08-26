#!/usr/bin/env bash
# Install workstation-only software that is not managed by Nix.
set -euo pipefail

wezterm_keyring=/usr/share/keyrings/wezterm-fury.gpg
wezterm_repo=https://apt.fury.io/wez/

happ_repo=Happ-proxy/happ-desktop

components=(wezterm-nightly telegram-desktop happ tailscale)

verbose=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --verbose)
      verbose=true
      shift
      ;;
    --help)
      echo "Usage: $(basename "$0") [OPTIONS]"
      echo ""
      echo "Install workstation software that is not managed by Nix:"
      echo "${components[*]}."
      echo ""
      echo "Options:"
      echo "  --verbose   Stream apt/snap/systemd output instead of hiding it"
      echo "  --help      Show this message"
      exit 0
      ;;
    *)
      echo "Error: Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

installed=0
skipped=0
step_no=0

# step <name> — announce component N of M.
step() {
  step_no=$((step_no + 1))
  echo "[${step_no}/${#components[@]}] $1"
}

# detail <text> — indented status line under the current step.
detail() {
  echo "  $*"
}

# run <description> <command...> — hide output unless --verbose, and replay it
# indented if the command fails, so a normal run stays readable.
run() {
  local desc="$1"
  shift

  if $verbose; then
    detail "${desc}..."
    "$@"
    return
  fi

  local log
  log="$(mktemp)"

  if ! "$@" >"${log}" 2>&1; then
    echo "Error: ${desc} failed" >&2
    sed 's/^/    /' "${log}" >&2
    rm -f "${log}"
    exit 1
  fi

  rm -f "${log}"
  detail "${desc}: ok"
}

require_commands() {
  local missing=()

  for cmd in curl dpkg gpg snap sudo systemctl; do
    if ! command -v "${cmd}" >/dev/null 2>&1; then
      missing+=("${cmd}")
    fi
  done

  if [[ ${#missing[@]} -gt 0 ]]; then
    echo "Error: missing required commands: ${missing[*]}" >&2
    exit 1
  fi
}

deb_version() {
  dpkg-query -W -f='${Version}' "$1" 2>/dev/null || true
}

snap_version() {
  snap list "$1" 2>/dev/null | awk 'NR == 2 { print $2 }'
}

fetch_wezterm_keyring() {
  curl -fsSL "${wezterm_repo}gpg.key" | sudo gpg --dearmor --yes -o "${wezterm_keyring}"
  sudo chmod 0644 "${wezterm_keyring}"
}

write_wezterm_source() {
  # deb822 format: apt on Ubuntu 26.04 deprecates one-line .list entries.
  sudo tee /etc/apt/sources.list.d/wezterm.sources >/dev/null <<EOF
Types: deb
URIs: ${wezterm_repo}
Suites: *
Components: *
Signed-By: ${wezterm_keyring}
EOF
}

install_wezterm_nightly() {
  step "wezterm-nightly"
  detail "repository: ${wezterm_repo}"

  if [[ -s "${wezterm_keyring}" ]]; then
    detail "keyring: already present"
  else
    run "fetching signing key" fetch_wezterm_keyring
  fi

  run "writing apt source" write_wezterm_source
  run "updating apt index" sudo apt-get update

  local before after
  before="$(deb_version wezterm-nightly)"
  run "installing wezterm-nightly" sudo apt-get install -y wezterm-nightly
  after="$(deb_version wezterm-nightly)"

  if [[ -n "${before}" && "${before}" == "${after}" ]]; then
    detail "Skip: already at ${after}"
    skipped=$((skipped + 1))
  else
    detail "installed: ${after:-unknown}"
    installed=$((installed + 1))
  fi

  echo ""
}

install_telegram() {
  step "telegram-desktop"
  detail "source: snap"

  local version
  version="$(snap_version telegram-desktop)"

  if [[ -n "${version}" ]]; then
    detail "Skip: already installed (${version})"
    skipped=$((skipped + 1))
    echo ""
    return
  fi

  run "installing telegram-desktop" sudo snap install telegram-desktop
  version="$(snap_version telegram-desktop)"
  detail "installed: ${version:-unknown}"
  installed=$((installed + 1))

  echo ""
}

# Happ ships no apt repository, so releases are pulled from GitHub.
happ_latest_tag() {
  curl -fsSL "https://api.github.com/repos/${happ_repo}/releases/latest" |
    sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' |
    head -n 1
}

happ_asset_arch() {
  case "$(dpkg --print-architecture)" in
    amd64) echo x64 ;;
    arm64) echo arm64 ;;
    *) return 1 ;;
  esac
}

# download_happ_deb <tag> <asset-arch> <destination>
download_happ_deb() {
  curl -fsSL -o "$3" \
    "https://github.com/${happ_repo}/releases/download/$1/Happ.linux.$2.deb"
}

install_happ() {
  step "happ"
  detail "source: github.com/${happ_repo} releases"

  local arch
  if ! arch="$(happ_asset_arch)"; then
    echo "Error: no Happ build for $(dpkg --print-architecture)" >&2
    exit 1
  fi

  local tag
  tag="$(happ_latest_tag)"

  if [[ -z "${tag}" ]]; then
    echo "Error: could not determine the latest Happ release" >&2
    exit 1
  fi

  detail "latest release: ${tag}"

  # The deb version carries a build suffix (4.1.1-312) the tag lacks.
  local before
  before="$(deb_version happ)"

  if [[ -n "${before}" && "${before%%-*}" == "${tag}" ]]; then
    detail "Skip: already at ${before}"
    skipped=$((skipped + 1))
    echo ""
    return
  fi

  local deb
  deb="$(mktemp --suffix=.deb)"

  run "downloading Happ ${tag} (${arch})" download_happ_deb "${tag}" "${arch}" "${deb}"
  run "installing happ" sudo apt-get install -y "${deb}"
  rm -f "${deb}"

  detail "installed: $(deb_version happ)"
  installed=$((installed + 1))

  echo ""
}

install_tailscale_package() {
  curl -fsSL https://tailscale.com/install.sh | sh
}

install_tailscale() {
  step "tailscale"

  if command -v tailscale >/dev/null 2>&1; then
    detail "Skip: already installed ($(tailscale version | head -n 1))"
    skipped=$((skipped + 1))
  else
    run "running tailscale.com/install.sh" install_tailscale_package
    detail "installed: $(tailscale version | head -n 1)"
    installed=$((installed + 1))
  fi

  # Configuration is re-applied on every run: it is cheap, and a package
  # reinstall or `tailscale logout` drops it.
  local operator
  operator="$(id -un)"
  run "setting operator to ${operator}" sudo tailscale set --operator="${operator}"
  run "configuring systray autostart" tailscale configure systray --enable-startup=systemd
  run "reloading user units" systemctl --user daemon-reload
  run "enabling tailscale-systray" systemctl --user enable --now tailscale-systray

  echo ""
}

if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  system="${PRETTY_NAME:-unknown}"
else
  system="unknown"
fi

echo "Workstation bootstrap"
echo "System: ${system}"
echo "Components: ${components[*]}"
if $verbose; then
  echo "Output: streamed"
else
  echo "Output: hidden unless a step fails (--verbose to stream)"
fi
echo ""

require_commands
install_wezterm_nightly
install_telegram
install_happ
install_tailscale

echo "Done. ${installed} installed, ${skipped} already present."
echo "Run ./tailscale-up.sh to join the tailnet."
