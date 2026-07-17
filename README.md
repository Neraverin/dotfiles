# dotfiles

Nix + home-manager configuration for Linux/WSL development machines.

## Apply

```sh
./activate.sh
```

The activation script builds and activates the locked home-manager generation from this repository.
Existing managed files are backed up with the `.backup` extension before Home Manager replaces them.

To use another home-manager configuration from the flake:

```sh
CONFIG_NAME=neraverin@work-wsl ./activate.sh
```

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
