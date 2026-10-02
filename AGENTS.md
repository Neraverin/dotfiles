# AGENTS.md

Personal dotfiles for Linux/WSL machines, provisioned with the distribution's own tooling.
`README.md` covers day-to-day usage.

## Layout

| Path | What it is |
| --- | --- |
| `provision/host.py` | How the repository talks to the host: sudo handling, step lines, fetch/staging, the run counters and the state file. |
| `provision/provision.py` | The whole tool: apt (packages and third-party repositories), GitHub releases, tarballs, npm, `go install`, fonts, dotfiles and the bash snippet, driven by `config.yaml`. |
| `apply.sh` | One-line wrapper: execs `provision/provision.py` with the arguments it was given. |
| `provision/config.yaml` | Full declaration of every host. `common` everywhere, `gui` merged on top on graphical hosts, `workstation` only under `--workstation`. |
| `provision/workstation.py` | Front door for the `workstation` section: adds `--workstation` and hands everything else to `provision.py`. |
| `workstation.sh` | One-line wrapper: execs `provision/workstation.py` with the arguments it was given. |
| `tailscale-up.sh` | Joins the Headscale tailnet (`TAILSCALE_LOGIN_SERVER` overrides the server). |
| `migrate-from-nix.sh` | For hosts still on the old Nix/home-manager setup: deactivates it, restores the dotfiles it displaced, runs `provision.py`, and with `--purge-nix` deletes Nix itself. |
| `files/` | Payload files referenced from `provision/config.yaml`, one directory per application. |

The Nix flake and its home-manager module were removed from the working tree. They are still
in history — check out the commit before their deletion if a host needs them back, which is why
`migrate-from-nix.sh` stays: not every host has been converted yet.

## Verifying a change

```sh
./provision/provision.py --dry-run --gui      # any config.yaml or provision.py edit
./provision/provision.py --dry-run --no-gui   # ...run both, they cover different halves
./workstation.sh --dry-run                    # any workstation: edit
bash -n script.sh && shellcheck script.sh     # any shell edit
python3 -m py_compile provision/*.py          # any Python edit
```

`--dry-run` resolves every upstream version and prints the URLs it would fetch, so a broken
asset template shows up without downloading anything. `--only <section>` narrows a run to
`apt`, `github`, `archive`, `npm`, `go`, `fonts`, `files` or `shell` — and under
`--workstation`, to `apt`, `snap`, `deb` or `script`.

`./provision/provision.py` without `--dry-run` changes the live machine — and so does
`./apply.sh`, which is only a wrapper around it. Run either only when asked. The same goes for `migrate-from-nix.sh`, which is destructive by design.

## Conventions

- `config.yaml` picks a source per package, and the rule is fixed: apt if Debian 12 — the oldest
  host — ships a usable version, otherwise upstream. Say which one and why when adding an entry.
- Third-party apt repositories go in `apt.repos`, keyed on `{id}`/`{codename}`/`{arch}` from the
  host so one entry covers every distribution. A repository is probed for a suite matching the
  running release before it is written: an upstream that has not caught up with a fresh release
  must warn, never leave a stanza behind that breaks every later `apt-get update` on the host.
- `provision.py` only ever removes what its own state file says it installed. Keep that property:
  a new source section needs both an install path and a removal path keyed off state, and a
  line in `set_aside()` so a run without gui leaves that section's gui entries alone.
- Two ways to place a dotfile, and the choice matters:
  - `files` — rewritten on every run. Only for files the application never touches (wezterm,
    starship).
  - `seeds` — copied once, then left writable and alone. For files the application rewrites
    itself (Codex, Claude). `lines` is the same idea for one key inside a foreign config.
- Every item a section installs prints one `[N/M] name ... status` line. Long steps show
  `in progress` first so a silent minute is attributable to a package.
- Comments explain *why*, and are most valuable where the tool meets the host distribution
  (fontconfig paths, ALSA/PipeWire, apt quirks). Keep that habit rather than restating the code.
- No `~/.config/git/config` is managed here; git identity is set per repository.
- Scripts: `set -euo pipefail`, idempotent, safe to re-run, silent unless `--verbose` or a failure.
- Commits: one logical change each, imperative subject, no body. History is linear on `master`.

## Hosts

One `config.yaml` covers every machine: an Ubuntu 26.04 workstation, a Debian 12 WSL instance
and a Debian 13 netinstall server, with more servers expected. There is no per-host file, so
any change reaches all of them:

- Keep `common` host-agnostic. Nothing there may assume a graphical session, a desktop-only
  service, a specific distribution or a path outside `$HOME`.
- Desktop-only entries belong in `gui`, which never reaches a headless host. Desktop detection
  keys off installed software, never off `systemctl get-default` — Debian leaves that at
  `graphical.target` on a server with no X and no display manager.
- Everything lands under `$HOME`. Check the target host has room: `$HOME` is a small separate
  partition on some of them, and the Go toolchain plus npm globals alone run past a gigabyte.
- `provision.py` installs nothing system-wide except apt packages, *unless* it is run with
  `--workstation`. Snaps, vendor installers and desktop-only system packages go in the
  `workstation` section, which only `./workstation.sh` reaches and which keeps its
  own state bucket: an ordinary `./apply.sh` never declares any of it, and must not read
  that silence as an instruction to uninstall it.

## Distribution quirks worth remembering

- Ubuntu 26.04: upstream `.deb`s that declare pre-t64 dependency names no longer resolve.
  Prefer a self-contained payload over forcing dpkg.
- Ubuntu 26.04: an application bundling its own alsa-lib cannot load the host ALSA→PipeWire
  plugin, so its ALSA output fails — DeaDBeeF's static build hits this. Use the PulseAudio
  output instead; it reaches `pipewire-pulse` directly.
- Endpoint security can deny reads of a downloaded file by content hash: the download succeeds
  and every later `open` fails with `EPERM`. Verify the checksum before believing the file is
  bad, then pin a different version. Go was pinned around 1.27.0 for this reason until 1.27.1
  came out, which reads fine.
- The anonymous GitHub API allows 60 calls an hour per address. Set `GITHUB_TOKEN` where several
  hosts share an egress address.
