# dotfiles

Nix + home-manager configuration for Linux/WSL development machines.

## Apply

On a server or under WSL:

```sh
./activate-server.sh
```

On a graphical workstation:

```sh
./activate-workstation.sh
```

Both are thin wrappers around `activate.sh`, which builds and activates the locked home-manager
generation from this repository. Existing managed files are backed up with the `.backup` extension
before Home Manager replaces them.

## Profiles

| Script | Configuration | Module | Use for |
| --- | --- | --- | --- |
| `activate-server.sh` | `neraverin@work-wsl` | `home/common.nix` | servers and WSL (base profile) |
| `activate-workstation.sh` | `neraverin@workstation` | `home/workstation.nix` | graphical workstations; imports `home/common.nix` and adds WezTerm and GNOME input-source keybindings |

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
