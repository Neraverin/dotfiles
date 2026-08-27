# dotfiles

Machine configuration for Linux/WSL development machines. Two implementations of the same
setup live here side by side while the migration runs:

| Path | Approach |
| --- | --- |
| `provision/` | Distribution packages, upstream releases and npm, driven by `provision/config.yaml`. |
| `flake.nix`, `home/` | The original Nix + home-manager configuration, kept until every host has moved. |

## Provision

```sh
./provision/provision.py --dry-run     # report what would change
./provision/provision.py               # apply
./provision/provision.py --upgrade     # also re-check upstream versions
```

Desktop-only entries are applied when a graphical session is detected; force the decision
with `--gui` / `--no-gui`. `--only <section>` limits the run to one of `apt`, `github`,
`archive`, `npm`, `go`, `fonts`, `files`, `shell`.

What it installed is recorded in `~/.local/state/dotfiles/state.json`. Removing an entry from
`config.yaml` removes what that entry installed on the next run — and nothing else, so packages
that predate the tool are never touched.

Requires a Debian or Ubuntu host, `python3-yaml`, and `sudo` for the `apt` section only.

Version lookups use the anonymous GitHub API, which allows 60 calls an hour per address. Set
`GITHUB_TOKEN` if several hosts share one address; without it a rate-limited run keeps the
installed version and warns, and only fails outright when the tool is not installed yet.

## Migrate off Nix

On a host still running home-manager:

```sh
./migrate-from-nix.sh --dry-run    # walk through it without changing anything
./migrate-from-nix.sh              # deactivate home-manager, then provision
```

That leaves Nix installed but unused, which is the point to open a new shell and check the
tools you rely on. When satisfied:

```sh
./migrate-from-nix.sh --purge-nix
```

which stops the daemon, deletes `/nix`, the build users and the installer's shell hooks.
That step is destructive and this script does not undo it.

## Bootstrap

On a fresh Debian/Ubuntu host:

```sh
./bootstrap-nix.sh    # only if you still want the Nix path
./provision/provision.py
```

`bootstrap-workstation.sh` adds the Ubuntu-desktop software neither path manages:
wezterm-nightly, telegram-desktop, happ and tailscale.

## Nix (legacy)

```sh
./activate.sh       # build and activate the locked home-manager generation
nix flake check
```

`BACKUP_EXT=hm-backup ./activate.sh` changes the extension used for displaced files.

| Configuration | Module | Use for |
| --- | --- | --- |
| `neraverin@server` | `home/common.nix` | servers, WSL and workstations |
