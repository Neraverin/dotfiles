# dotfiles

Nix + home-manager configuration for Linux/WSL development machines.

## Apply

```sh
./activate-server.sh
```

It is a thin wrapper around `activate.sh`, which builds and activates the locked home-manager
generation from this repository. Existing managed files are backed up with the `.backup` extension
before Home Manager replaces them.

## Profiles

| Script | Configuration | Module | Use for |
| --- | --- | --- | --- |
| `activate-server.sh` | `neraverin@server` | `home/common.nix` | servers, WSL and workstations |

To use another backup extension:

```sh
BACKUP_EXT=hm-backup ./activate.sh
```

## Bootstrap

On a fresh Debian/Ubuntu or RHEL-like server:

```sh
./bootstrap-nix.sh
./activate.sh
```

## Check

```sh
nix flake check
```
