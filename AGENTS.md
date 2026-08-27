# AGENTS.md

Personal dotfiles for Linux/WSL machines. Two implementations of the same setup coexist
while hosts are migrated: `provision/`, which uses the distribution's own tooling, and the
original Nix flake + home-manager, which is on its way out. `README.md` covers day-to-day
usage.

## Layout

| Path | What it is |
| --- | --- |
| `flake.nix` | One output: `homeConfigurations."neraverin@server"`. Pins `nixos-26.05` as `pkgs`, `nixpkgs-unstable` as `unstable`. |
| `home/common.nix` | The only home-manager module — packages, shell, activation hooks. Everything lands here. |
| `activate.sh` | Builds and activates a generation. `--force` deletes seeded Codex/Claude configs first. |
| `bootstrap-nix.sh` | Installs Nix on a fresh Debian/Ubuntu or RHEL host. Precedes `activate.sh`. |
| `bootstrap-workstation.sh` | Ubuntu desktop only. Software Nix does not handle: wezterm-nightly, telegram-desktop, happ, tailscale. |
| `tailscale-up.sh` | Joins the Headscale tailnet (`TAILSCALE_LOGIN_SERVER` overrides the server). |
| `provision/provision.py` | The replacement for the Nix path: apt, GitHub releases, tarballs, npm, `go install`, fonts, dotfiles and the bash snippet, driven by `config.yaml`. |
| `provision/config.yaml` | Full declaration of every host. `common` everywhere, `gui` merged on top on graphical hosts. |
| `migrate-from-nix.sh` | Deactivates home-manager, runs `provision.py`, and with `--purge-nix` deletes Nix itself. |
| `claude/`, `codex/`, `starship/`, `wezterm/` | Payload files referenced from `home/common.nix` and `provision/config.yaml`. |

## Verifying a change

```sh
./provision/provision.py --dry-run --gui                                         # provision edit
./provision/provision.py --dry-run --no-gui                                      # ...both hosts
nix build --no-link '.#homeConfigurations."neraverin@server".activationPackage'   # any .nix edit
nix flake check
bash -n script.sh && shellcheck script.sh                                        # any shell edit
```

`--dry-run` resolves every upstream version and prints the URLs it would fetch, so a broken
asset template shows up without downloading anything. Check both `--gui` and `--no-gui`: they
exercise different halves of `config.yaml`. Build before claiming a Nix change works; the build
is cheap and catches most mistakes. `./provision/provision.py` and `./activate.sh` apply to the
live machine — run them only when asked.

## Conventions

- Quote the configuration name — the `@` needs escaping inside a flake reference.
- `home.packages` is alphabetical; `unstable.*` entries keep their sorted position.
- Two ways to place a dotfile, and the choice matters:
  - `home.file` — read-only symlink into the store. Only for files the app never rewrites (wezterm).
  - `home.activation.seed*` — copy or patch once, leave the file writable. For files the app
    rewrites itself (Codex, Claude, DeaDBeeF). Seed when unset; do not enforce on every switch.
- Comments explain *why*, and are most valuable where Nix meets the host distribution
  (fontconfig paths, ALSA/PipeWire, apt quirks). Keep that habit rather than restating the code.
- `programs.git` is deliberately absent so no store-owned `~/.config/git/config` exists;
  git identity is set per repository.
- `config.yaml` picks a source per package, and the rule is fixed: apt if Debian 12 — the oldest
  host — ships a usable version, otherwise upstream. Say which one and why when adding an entry.
- `provision.py` only ever removes what its own state file says it installed. Keep that property:
  a new source section needs both an install path and a removal path keyed off state.
- Scripts: `set -euo pipefail`, idempotent, safe to re-run, silent unless `--verbose` or a failure.
- Commits: one logical change each, imperative subject, no body. History is linear on `master`.

## Hosts

One profile, `neraverin@server`, covers every machine; the name is historical, not a filter.
Today that means an Ubuntu 26.04 workstation, a Debian 12 WSL instance and a Debian 13
netinstall server, with more servers expected. There is no per-host module, so any change to
`home/common.nix` reaches all of them:

- Keep it host-agnostic. Nothing may assume a graphical session, a desktop-only service, a
  specific distribution or a path outside `$HOME`.
- Activation hooks run everywhere, headless hosts included. Keep them cheap and harmless
  there — seeding a config for a GUI app on a server is acceptable, failing on one is not.
- Desktop packages (deadbeef, nerd fonts, the wezterm config) land on servers and WSL under
  Nix, and they are not free: deadbeef alone drags in a ~2.9 GB closure via swift and clang.
  Check `nix path-info -S` before adding a GUI package there, and say what it costs. Under
  `provision/` the same things sit in the `gui` section and never reach a headless host.
- Nix installs nothing system-wide. Anything needing apt, snap, a vendor repo or a GUI belongs
  in `bootstrap-workstation.sh`, which targets the Ubuntu desktop alone and never runs on the
  servers. `bootstrap-nix.sh` is the portable half: Debian/Ubuntu plus the RHEL family.

## Distribution quirks worth remembering

- Ubuntu 26.04: upstream `.deb`s that declare pre-t64 dependency names no longer resolve.
  Prefer a self-contained payload or the Nix package over forcing dpkg.
- Ubuntu 26.04: Nix-built audio apps cannot load the host ALSA→PipeWire plugin, so their ALSA
  output fails. Use the PulseAudio output instead; it reaches `pipewire-pulse` directly.
