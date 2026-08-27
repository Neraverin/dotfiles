#!/usr/bin/env bash
# Move a machine from the Nix/home-manager setup to provision.py.
#
# Three steps, in this order and for a reason: tear down the home-manager
# generation first so its symlinks stop shadowing anything, install the
# replacements second, and only then — behind an explicit flag — delete /nix.
# Stopping after step two leaves a working machine with Nix still installed,
# which is the safe place to pause and check the result.
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
purge_nix=false
assume_yes=false
dry_run=false
provision_args=()

usage() {
  cat <<'USAGE'
Usage: ./migrate-from-nix.sh [options] [-- provision.py options]

Deactivates home-manager, then provisions the machine from provision/config.yaml.

Options:
  --purge-nix   Also uninstall Nix itself: /nix, the daemon, the build users and
                the shell hooks. Destructive and not undone by this script.
  --yes         Do not prompt for confirmation.
  --dry-run     Report every step without changing anything.
  -h, --help    Show this help.

Anything after -- is passed to provision.py, e.g.:
  ./migrate-from-nix.sh -- --no-gui --verbose
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --purge-nix) purge_nix=true; shift ;;
    --yes|-y) assume_yes=true; shift ;;
    --dry-run) dry_run=true; provision_args+=(--dry-run); shift ;;
    -h|--help) usage; exit 0 ;;
    --) shift; provision_args+=("$@"); break ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 1 ;;
  esac
done

say() { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
detail() { printf '  %s\n' "$*"; }

act() {
  if [[ "${dry_run}" == true ]]; then
    detail "would run: $*"
  else
    "$@"
  fi
}

# Report what act() just did — but only when it actually did it.
did() { [[ "${dry_run}" == true ]] || detail "$*"; }

confirm() {
  [[ "${assume_yes}" == true ]] && return 0
  local answer
  read -r -p "$1 [y/N] " answer
  [[ "${answer}" == "y" || "${answer}" == "Y" ]]
}

if [[ $EUID -eq 0 ]]; then
  echo "Run this as your own user; it calls sudo where root is needed." >&2
  exit 1
fi

if [[ ! -f "${repo_root}/provision/provision.py" ]]; then
  echo "provision/provision.py is missing; run this from a full checkout." >&2
  exit 1
fi

# --------------------------------------------------------------- 1. home-manager

step "Deactivating home-manager"

hm_generation="${HOME}/.local/state/nix/profiles/home-manager"
[[ -e "${hm_generation}" ]] || hm_generation="${HOME}/.local/state/home-manager/gcroots/current-home"

if [[ -e "${hm_generation}" || -L "${HOME}/.nix-profile" ]]; then
  # `home-manager uninstall` removes the managed symlinks and runs the
  # deactivation hooks, which is what restores plain ~/.config entries. It is
  # only available while the profile is still on PATH, hence the fallbacks.
  hm_bin=""
  for candidate in \
    "$(command -v home-manager 2>/dev/null || true)" \
    "${HOME}/.nix-profile/bin/home-manager" \
    "${hm_generation}/home-path/bin/home-manager"; do
    [[ -n "${candidate}" && -x "${candidate}" ]] && { hm_bin="${candidate}"; break; }
  done

  if [[ -n "${hm_bin}" ]]; then
    if [[ "${dry_run}" == true ]]; then
      detail "would run: ${hm_bin} uninstall"
    elif confirm "Run '${hm_bin} uninstall' to remove the home-manager generation?"; then
      # It prompts on its own too; -h answers that one.
      yes | "${hm_bin}" uninstall || detail "uninstall reported an error; continuing"
    else
      detail "skipped"
    fi
  else
    detail "home-manager binary not found; removing the profile links by hand"
  fi
else
  detail "no home-manager generation found"
fi

# home-manager leaves the profile symlink behind on some versions.
for link in "${HOME}/.nix-profile" "${HOME}/.nix-defexpr"; do
  if [[ -L "${link}" ]]; then
    act rm -f "${link}"
    did "removed ${link}"
  fi
done

# Anything home-manager backed up during an earlier activation is still on disk
# under .backup; leaving it is harmless but the user should know it is there.
# home-manager only ever displaced dotfiles at the top of $HOME or one level
# into ~/.config; a deeper sweep just catches unrelated ".backup" files.
backups=$(
  {
    find "${HOME}" -maxdepth 1 -name '*.backup'
    find "${HOME}/.config" -maxdepth 2 -name '*.backup'
  } 2>/dev/null | head -20 || true
)
if [[ -n "${backups}" ]]; then
  detail "home-manager backups left in place:"
  while IFS= read -r file; do detail "    ${file}"; done <<<"${backups}"
fi

# ------------------------------------------------------------------ 2. provision

step "Provisioning from provision/config.yaml"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required." >&2
  exit 1
fi

if ! python3 -c "import yaml" >/dev/null 2>&1; then
  detail "installing python3-yaml"
  act sudo apt-get update
  act sudo apt-get install -y python3-yaml
fi

# The Nix profile used to provide the PATH entry; on a fresh migration
# ~/.local/bin may not be on PATH yet, so give the child process one.
PATH="${HOME}/.local/bin:${PATH}" \
  python3 "${repo_root}/provision/provision.py" "${provision_args[@]}"

# ------------------------------------------------------------------ 3. purge nix

if [[ "${purge_nix}" != true ]]; then
  step "Nix left installed"
  cat <<'NEXT'
  The machine is now provisioned without depending on Nix. Open a new shell and
  check the tools you use before going further:

      exec bash -l && which rg go claude starship

  When satisfied, remove Nix itself:

      ./migrate-from-nix.sh --purge-nix --yes -- --dry-run
NEXT
  exit 0
fi

step "Purging Nix"

if ! confirm "Delete /nix and everything Nix installed on this host?"; then
  detail "aborted"
  exit 0
fi

if command -v systemctl >/dev/null 2>&1; then
  for unit in nix-daemon.service nix-daemon.socket; do
    if systemctl list-unit-files "${unit}" >/dev/null 2>&1; then
      act sudo systemctl stop "${unit}" || true
      act sudo systemctl disable "${unit}" || true
    fi
  done
fi

# The multi-user installer writes these; the single-user one writes none of them.
for file in \
  /etc/systemd/system/nix-daemon.service \
  /etc/systemd/system/nix-daemon.socket \
  /etc/profile.d/nix.sh \
  /etc/profile.d/nix-daemon.sh \
  /etc/tmpfiles.d/nix-daemon.conf; do
  [[ -e "${file}" ]] && { act sudo rm -f "${file}"; did "removed ${file}"; }
done

# The installer appended a sourcing block to the shell profiles; it is fenced by
# its own markers, so a targeted delete is safe.
for profile in /etc/bash.bashrc /etc/bashrc /etc/zshrc "${HOME}/.bashrc" "${HOME}/.profile"; do
  if [[ -f "${profile}" ]] && grep -q 'Nix installer' "${profile}" 2>/dev/null; then
    act sudo sed -i '/# Nix$/,/# End Nix$/d' "${profile}"
    did "cleaned the Nix block out of ${profile}"
  fi
done

if command -v systemctl >/dev/null 2>&1; then
  act sudo systemctl daemon-reload
fi

for i in $(seq 1 32); do
  id -u "nixbld${i}" >/dev/null 2>&1 && act sudo userdel "nixbld${i}"
done
getent group nixbld >/dev/null 2>&1 && act sudo groupdel nixbld

# /nix is mounted read-only by the determinate installer and as a plain
# directory by the official one; unmount before deleting, ignoring failures.
if mountpoint -q /nix 2>/dev/null; then
  act sudo umount -R /nix || true
fi
[[ -e /etc/synthetic.conf ]] && detail "note: /etc/synthetic.conf is a macOS artefact, left alone"

act sudo rm -rf /nix
did "removed /nix"

for path in \
  "${HOME}/.nix-channels" \
  "${HOME}/.nix-defexpr" \
  "${HOME}/.nix-profile" \
  "${HOME}/.cache/nix" \
  "${HOME}/.local/state/nix" \
  "${HOME}/.local/share/nix" \
  "${HOME}/.local/state/home-manager" \
  "${HOME}/.config/nix" \
  "${HOME}/.config/home-manager"; do
  if [[ -e "${path}" ]]; then
    act rm -rf "${path}"
    did "removed ${path}"
  fi
done

if [[ -e /etc/nix ]]; then
  act sudo rm -rf /etc/nix
  did "removed /etc/nix"
fi

step "Done"
say "  Nix is gone. Start a new login shell to pick up the provisioned PATH."
