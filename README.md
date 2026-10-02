# dotfiles

Machine configuration for Linux/WSL development machines, applied with the distribution's own
tooling: apt packages, upstream release binaries, npm globals, dotfiles and a bash snippet, all
declared in `provision/config.yaml`.

## Apply

```sh
./apply.sh --dry-run     # report what would change
./apply.sh                  # apply
./apply.sh --upgrade     # also re-check upstream versions
```

`apply.sh` is a one-line wrapper around `provision/provision.py`, which takes the same flags
and can be called directly; either works from any directory.

Desktop-only entries are applied when a graphical session is detected; force the decision
with `--gui` / `--no-gui`. Without a desktop the section is only skipped: nothing it installed is
ever removed by `--no-gui` or by a missed detection, only by deleting the entry from
`config.yaml`. `--only <section>` limits the run to one of `apt`, `github`, `archive`, `npm`,
`go`, `fonts`, `files`, `shell`. `--verbose` streams command output.

What it installed is recorded in `~/.local/state/dotfiles/state.json`. Removing an entry from
`config.yaml` removes what that entry installed on the next run — and nothing else, so packages
that predate the tool are never touched.

Requires a Debian or Ubuntu host, `python3-yaml`, and `sudo` for the `apt` section only.

The `apt` section also owns third-party repositories — Docker's, currently: the keyring lands in
`/etc/apt/keyrings/<name>.asc` and the deb822 stanza in `/etc/apt/sources.list.d/<name>.sources`,
addressed at the running distribution and release. Docker's engine needs a group to be usable
without sudo, which is not managed here: `sudo usermod -aG docker $USER`, then log in again.

Version lookups use the anonymous GitHub API, which allows 60 calls an hour per address. Set
`GITHUB_TOKEN` if several hosts share one address; without it a rate-limited run keeps the
installed version and warns, and only fails outright when the tool is not installed yet.

## Bootstrap

On a fresh Debian/Ubuntu host:

```sh
sudo apt-get install -y python3-yaml
./apply.sh
```

`workstation.sh` applies the `workstation` section of `config.yaml` — the
system-wide desktop software an ordinary run stays away from: wezterm-nightly,
telegram-desktop, happ, claude-desktop and tailscale. It takes the same flags as
`apply.sh` (`--dry-run`, `--verbose`, `--only`, `--upgrade`). `./tailscale-up.sh`
then joins the tailnet.

## Migrating a host off Nix

Earlier versions of this repository used a Nix flake with home-manager. On a host still
running it:

```sh
./migrate-from-nix.sh --dry-run    # walk through it without changing anything
./migrate-from-nix.sh              # deactivate home-manager, restore dotfiles, provision
```

That leaves Nix installed but unused, which is the point to open a new shell and check the
tools you rely on. When satisfied:

```sh
./migrate-from-nix.sh --purge-nix
```

which stops the daemon, deletes `/nix`, the build users and the installer's shell hooks.
That step is destructive and this script does not undo it.

Over SSH there is no terminal for `sudo` to prompt on, so point `SUDO_ASKPASS` at a helper or
prime the timestamp with `sudo -v` first.

The flake and its home-manager module are no longer in the working tree; check out the commit
before their removal if you need them back.
