#!/usr/bin/env python3
"""Provision a machine from config.yaml using the distribution's own tooling.

The replacement for the Nix/home-manager path in this repository. It covers what
that setup was actually used for: install a declared set of packages, place a few
config files, and extend the shell. There is no atomic switch and no rollback,
which is deliberate — the cost of those was the whole reason to leave.

State lives in ~/.local/state/dotfiles/state.json so that dropping an entry from
the YAML removes what it installed, and only what *this* tool installed. Anything
that predates the tool is left where it is.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - environment problem, not a code path
    sys.exit("PyYAML is missing. Install it with: sudo apt-get install -y python3-yaml")

REPO_ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()
STATE_PATH = HOME / ".local/state/dotfiles/state.json"
BIN_DIR = HOME / ".local/bin"
OPT_DIR = HOME / ".local/opt"
FONT_DIR = HOME / ".local/share/fonts"
DESKTOP_DIR = HOME / ".local/share/applications"
NPM_PREFIX = HOME / ".local"
SHELL_SNIPPET = HOME / ".bashrc.d/50-dotfiles.sh"
SHELL_MARKER = "# >>> dotfiles provision >>>"
USER_AGENT = "provision.py (+https://github.com/neraverin/dotfiles)"

installed: list[str] = []
removed: list[str] = []
warnings: list[str] = []
skipped = 0


# --------------------------------------------------------------------------- io


def log(message: str) -> None:
    print(message)


def detail(message: str) -> None:
    print(f"  {message}")


def warn(message: str) -> None:
    warnings.append(message)
    print(f"  warning: {message}")


def note_skip(count: int = 1) -> None:
    global skipped
    skipped += count


def child_env() -> dict:
    """Environment for child processes, with ~/.local/bin ahead of everything.

    Sections run in order, so `go` must find the toolchain `archive` just placed;
    the parent shell's PATH predates this run and cannot be relied on.
    """
    env = dict(os.environ)
    env["PATH"] = f"{BIN_DIR}:{env.get('PATH', '')}"
    return env


def run(cmd: list[str], *, dry_run: bool, verbose: bool, check: bool = True) -> int:
    """Run a command, hiding its output unless it fails or --verbose is set."""
    if dry_run:
        detail(f"would run: {' '.join(cmd)}")
        return 0

    if verbose:
        return subprocess.run(cmd, check=check, env=child_env()).returncode

    result = subprocess.run(cmd, capture_output=True, text=True, env=child_env())

    if result.returncode != 0 and check:
        detail(f"command failed: {' '.join(cmd)}")
        for line in (result.stdout + result.stderr).splitlines():
            detail(f"    {line}")
        raise SystemExit(1)

    return result.returncode


def sudo(cmd: list[str]) -> list[str]:
    return cmd if os.geteuid() == 0 else ["sudo", *cmd]


def ensure_sudo(*, dry_run: bool) -> None:
    """Take the sudo password now, while a prompt can still be seen.

    Every other command runs under capture_output, which swallows sudo's prompt
    while sudo still waits on /dev/tty — a silent hang with nothing on screen.
    Priming the timestamp here keeps the prompt visible and the later calls
    non-interactive.
    """
    if dry_run or os.geteuid() == 0:
        return

    if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0:
        return

    detail("apt needs root; sudo will ask for your password")
    if subprocess.run(["sudo", "-v"]).returncode != 0:
        raise SystemExit("sudo authentication failed")


def fetch(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        with destination.open("wb") as handle:
            shutil.copyfileobj(response, handle)

    # A host-level scanner (IMA/EVM, or a corporate endpoint agent) can deny
    # reads of a payload it dislikes, by content hash, after the write lands.
    # The open fails with EPERM far away from here, so say what happened while
    # the URL is still in hand.
    try:
        with destination.open("rb") as handle:
            handle.read(1)
    except PermissionError as error:
        raise SystemExit(
            f"{destination.name} downloaded but cannot be read back: {error.strerror}.\n"
            f"  Source: {url}\n"
            "  A local security policy is blocking this exact content — the download\n"
            "  itself succeeded. Verify the checksum, then pin a different version or\n"
            "  ask whoever runs endpoint security to allow it."
        ) from error


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode()


def expand(path: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(path)))


def extract_archive(archive: Path, target: Path, *, strip: int = 0) -> None:
    """Unpack into `target`, optionally dropping `strip` leading path components.

    Upstream tarballs habitually wrap everything in a single versioned directory;
    stripping it keeps ~/.local/opt/<name> stable across upgrades.
    """
    target.mkdir(parents=True, exist_ok=True)

    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as handle:
            if not strip:
                handle.extractall(target)
                return
            for member in handle.infolist():
                parts = Path(member.filename).parts[strip:]
                if not parts:
                    continue
                out = target.joinpath(*parts)
                if member.is_dir():
                    out.mkdir(parents=True, exist_ok=True)
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                with handle.open(member) as source, out.open("wb") as sink:
                    shutil.copyfileobj(source, sink)
                if member.external_attr >> 16 & stat.S_IXUSR:
                    out.chmod(0o755)
        return

    with tarfile.open(archive) as handle:
        members = []
        for member in handle.getmembers():
            parts = Path(member.name).parts[strip:]
            if not parts:
                continue
            member.name = str(Path(*parts))
            members.append(member)
        # filter="data" refuses absolute paths and traversal; Python 3.14 makes
        # it the default, older interpreters need it spelled out.
        try:
            handle.extractall(target, members=members, filter="data")
        except TypeError:  # pragma: no cover - Python < 3.12
            handle.extractall(target, members=members)


def find_binary(root: Path, name: str) -> Path:
    """Locate a binary by name; upstream archives disagree about nesting."""
    matches = [p for p in root.rglob(name) if p.is_file()]
    if not matches:
        raise SystemExit(f"{name} was not found inside the downloaded archive")
    return min(matches, key=lambda p: len(p.parts))


def link_bin(source: Path, name: str) -> Path:
    """Point ~/.local/bin/<name> at `source`, replacing whatever was there."""
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    destination = BIN_DIR / name
    if destination.is_symlink() or destination.exists():
        destination.unlink()
    destination.symlink_to(source)
    return destination


# ------------------------------------------------------------------ gui detection


def detect_gui() -> tuple[bool, str]:
    """Guess whether this host has a desktop.

    Ordered from the most reliable signal to the weakest. WSL is treated as
    headless: WSLg can show windows, but none of the desktop packages here make
    sense there.
    """
    version_file = Path("/proc/version")
    if version_file.exists() and "microsoft" in version_file.read_text().lower():
        return False, "WSL kernel"

    if os.environ.get("XDG_CURRENT_DESKTOP"):
        return True, f"XDG_CURRENT_DESKTOP={os.environ['XDG_CURRENT_DESKTOP']}"

    if shutil.which("systemctl"):
        result = subprocess.run(
            ["systemctl", "get-default"], capture_output=True, text=True
        )
        target = result.stdout.strip()
        if target == "graphical.target":
            return True, "systemd default target is graphical"
        if target:
            return False, f"systemd default target is {target}"

    for package in ("gnome-shell", "xserver-xorg-core", "sway", "kde-plasma-desktop"):
        if dpkg_installed(package):
            return True, f"{package} is installed"

    return False, "no desktop signals found"


# --------------------------------------------------------------------------- state

EMPTY_STATE = {
    "apt": {"packages": [], "links": {}},
    "github": {},
    "archive": {},
    "npm": {},
    "go": {},
    "fonts": {},
    "files": [],
}


def load_state() -> dict:
    state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}

    for key, default in EMPTY_STATE.items():
        state.setdefault(key, json.loads(json.dumps(default)))

    # The prototype recorded apt as a bare list; keep those entries owned.
    if isinstance(state["apt"], list):
        state["apt"] = {"packages": state["apt"], "links": {}}

    return state


def save_state(state: dict, *, dry_run: bool) -> None:
    if dry_run:
        return
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


# --------------------------------------------------------------------------- apt


def dpkg_installed(package: str) -> bool:
    result = subprocess.run(
        ["dpkg-query", "-W", "-f=${Status}", package], capture_output=True, text=True
    )
    return result.stdout.startswith("install ok installed")


def sync_apt(desired: dict, state: dict, *, dry_run: bool, verbose: bool) -> None:
    packages = desired.get("packages") or []
    links = desired.get("links") or {}

    if not packages and not state["apt"]["packages"]:
        return

    log("apt")

    missing = [p for p in packages if not dpkg_installed(p)]
    stale = [
        p
        for p in state["apt"]["packages"]
        if p not in packages and dpkg_installed(p)
    ]

    if missing or stale:
        ensure_sudo(dry_run=dry_run)

    if missing:
        run(sudo(["apt-get", "update"]), dry_run=dry_run, verbose=verbose)
        run(
            sudo(["apt-get", "install", "-y", *missing]),
            dry_run=dry_run,
            verbose=verbose,
        )
        for package in missing:
            detail(f"{'would install' if dry_run else 'installed'}: {package}")
            installed.append(package)

    if stale:
        # Only packages this tool installed are ever removed; anything the
        # distribution or the user brought in is left alone.
        run(sudo(["apt-get", "remove", "-y", *stale]), dry_run=dry_run, verbose=verbose)
        for package in stale:
            detail(f"{'would remove' if dry_run else 'removed'}: {package}")
            removed.append(package)

    note_skip(len(packages) - len(missing))

    # Record only what we installed, so a package that predates this tool is
    # never treated as ours on a later run.
    owned = set(state["apt"]["packages"]) | set(missing)
    state["apt"]["packages"] = sorted(owned & set(packages))

    sync_apt_links(links, state, dry_run=dry_run)


def sync_apt_links(links: dict, state: dict, *, dry_run: bool) -> None:
    for distro_name, wanted in links.items():
        source = shutil.which(distro_name)
        destination = BIN_DIR / wanted

        if not source:
            if destination.is_symlink() and not destination.exists():
                if not dry_run:
                    destination.unlink()
                detail(f"{wanted}: dangling link removed")
            continue

        if destination.is_symlink() and destination.resolve() == Path(source).resolve():
            detail(f"{wanted}: already linked to {distro_name}")
            note_skip()
            continue

        if dry_run:
            detail(f"would link {wanted} -> {source}")
            continue

        link_bin(Path(source), wanted)
        detail(f"{wanted}: linked to {source}")
        installed.append(wanted)

    for wanted in [w for w in state["apt"]["links"].values() if w not in links.values()]:
        path = BIN_DIR / wanted
        if path.is_symlink():
            if not dry_run:
                path.unlink()
            detail(f"{wanted}: link removed")
            removed.append(wanted)

    state["apt"]["links"] = dict(links)


# ------------------------------------------------------------------------ github


class UpstreamUnavailable(RuntimeError):
    """GitHub would not say what the latest release is, right now."""


def github_latest_tag(repo: str) -> str:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT},
    )
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        # Anonymous API calls are rate-limited per address, which bites when
        # several hosts sit behind one NAT. A token raises the ceiling.
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)["tag_name"]
    except urllib.error.HTTPError as error:
        # 403/429 is the anonymous rate limit — 60 calls an hour per address,
        # which several hosts behind one NAT reach easily. Not a config error.
        if error.code in (403, 429):
            raise UpstreamUnavailable(
                f"GitHub rate-limited the request for {repo}; "
                "set GITHUB_TOKEN to raise the ceiling"
            ) from error
        raise SystemExit(f"GitHub API returned {error.code} for {repo}") from error
    except urllib.error.URLError as error:
        raise UpstreamUnavailable(f"GitHub is unreachable: {error.reason}") from error


def format_asset(template: str, tag: str) -> str:
    return template.format(version=tag, version_strip=tag.lstrip("v"))


def install_github(
    name: str, spec: dict, state: dict, *, dry_run: bool, upgrade: bool
) -> None:
    names = spec.get("binaries") or [spec["binary"]]
    known = state["github"].get(name)
    present = all((BIN_DIR / b).exists() for b in names)

    # Without --upgrade an installed tool is left alone, which keeps ordinary
    # runs offline and fast; the API is only consulted when something may change.
    if known and present and not upgrade:
        detail(f"{name}: already at {known['version']}")
        note_skip()
        return

    try:
        tag = spec.get("version") or github_latest_tag(spec["repo"])
    except UpstreamUnavailable as error:
        if known:
            warn(f"{name}: {error}; keeping {known['version']}")
            return
        raise SystemExit(str(error)) from error

    asset = format_asset(spec["asset"], tag)
    url = f"https://github.com/{spec['repo']}/releases/download/{tag}/{asset}"

    if known and known["version"] == tag and present:
        detail(f"{name}: already at {tag}")
        note_skip()
        return

    if dry_run:
        detail(f"would fetch {name} {tag} from {url}")
        return

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        payload = tmp_path / asset
        fetch(url, payload)

        placed = []

        if spec.get("binary"):
            # The asset is the executable itself, not an archive around one.
            destination = BIN_DIR / spec["binary"]
            BIN_DIR.mkdir(parents=True, exist_ok=True)
            if destination.is_symlink():
                destination.unlink()
            shutil.copyfile(payload, destination)
            destination.chmod(0o755)
            placed.append(str(destination))
        else:
            unpacked = tmp_path / "unpacked"
            extract_archive(payload, unpacked)
            BIN_DIR.mkdir(parents=True, exist_ok=True)
            for binary in spec["binaries"]:
                destination = BIN_DIR / binary
                if destination.is_symlink():
                    destination.unlink()
                shutil.copyfile(find_binary(unpacked, binary), destination)
                destination.chmod(0o755)
                placed.append(str(destination))

    state["github"][name] = {"version": tag, "files": placed}
    detail(f"{name}: installed {tag}")
    installed.append(name)


def sync_github(desired: dict, state: dict, *, dry_run: bool, upgrade: bool) -> None:
    if not desired and not state["github"]:
        return

    log("github releases")

    for name, spec in desired.items():
        install_github(name, spec, state, dry_run=dry_run, upgrade=upgrade)

    for name in [n for n in list(state["github"]) if n not in desired]:
        for path in state["github"][name]["files"]:
            if dry_run:
                detail(f"would remove {path}")
            else:
                Path(path).unlink(missing_ok=True)
        detail(f"{name}: removed")
        removed.append(name)
        if not dry_run:
            del state["github"][name]


# ----------------------------------------------------------------------- archive


def resolve_archive_version(spec: dict) -> str:
    if spec.get("version"):
        return str(spec["version"])
    if spec.get("version_url"):
        try:
            return fetch_text(spec["version_url"]).strip().splitlines()[0]
        except urllib.error.URLError as error:
            raise UpstreamUnavailable(
                f"{spec['version_url']} is unreachable: {error.reason}"
            ) from error
    raise SystemExit("an archive entry needs either version or version_url")


def write_desktop_entry(name: str, root: Path, entry: dict) -> str:
    """Generate a .desktop file for an archive that ships none.

    Upstream static builds are often just a binary and its data, with no
    packaging metadata — DeaDBeeF's is. The launcher still has to exist, and
    every path in it has to be absolute because nothing puts ~/.local/opt on
    XDG_DATA_DIRS.
    """
    DESKTOP_DIR.mkdir(parents=True, exist_ok=True)
    destination = DESKTOP_DIR / f"{name}.desktop"

    fields = {
        "Type": "Application",
        "Name": entry.get("name", name),
        "Exec": f"{find_binary(root, entry['exec'])} %U",
        "Terminal": "false",
    }
    if entry.get("icon"):
        icon = next((p for p in root.rglob(entry["icon"]) if p.is_file()), None)
        if icon:
            fields["Icon"] = str(icon)
    for key in ("Comment", "Categories", "MimeType", "GenericName"):
        if entry.get(key.lower()):
            fields[key] = entry[key.lower()]

    body = "\n".join(f"{k}={v}" for k, v in fields.items())
    destination.write_text(f"[Desktop Entry]\n{body}\n")
    return str(destination)


def install_desktop_entries(name: str, root: Path, spec: dict) -> list[str]:
    """Place .desktop files, absolutising Exec and Icon.

    Nix put these on XDG_DATA_DIRS through the profile; without a profile the
    entries have to land in ~/.local/share/applications and name full paths.
    """
    desktop = spec.get("desktop")
    if isinstance(desktop, dict):
        return [write_desktop_entry(name, root, desktop)]

    placed = []

    for relative in desktop or []:
        source = root / relative
        if not source.exists():
            warn(f"{name}: {relative} is missing, no desktop entry installed")
            continue

        DESKTOP_DIR.mkdir(parents=True, exist_ok=True)
        destination = DESKTOP_DIR / source.name
        lines = []

        for line in source.read_text().splitlines():
            if line.startswith("Exec="):
                command = line[len("Exec=") :].split(" ", 1)
                binary = root / "bin" / Path(command[0]).name
                if binary.exists():
                    rest = f" {command[1]}" if len(command) > 1 else ""
                    line = f"Exec={binary}{rest}"
            elif line.startswith("Icon=") and "/" not in line:
                icon = next(
                    (p for p in (root / "share/icons").rglob(f"{line[5:]}.*")), None
                )
                if icon:
                    line = f"Icon={icon}"
            lines.append(line)

        destination.write_text("\n".join(lines) + "\n")
        placed.append(str(destination))

    return placed


def install_archive(
    name: str, spec: dict, state: dict, *, dry_run: bool, upgrade: bool
) -> None:
    known = state["archive"].get(name)
    target = OPT_DIR / name
    present = target.exists() and all((BIN_DIR / b).exists() for b in spec["bin"])

    if known and present and not upgrade:
        detail(f"{name}: already at {known['version']}")
        note_skip()
        return

    try:
        version = resolve_archive_version(spec)
    except UpstreamUnavailable as error:
        if known:
            warn(f"{name}: {error}; keeping {known['version']}")
            return
        raise SystemExit(str(error)) from error

    if known and known["version"] == version and present:
        detail(f"{name}: already at {version}")
        note_skip()
        return

    url = spec["url"].format(version=version, version_strip=version.lstrip("v"))
    archive_name = (spec.get("archive_name") or url.rsplit("/", 1)[-1]).format(
        version=version, version_strip=version.lstrip("v")
    )

    if dry_run:
        detail(f"would fetch {name} {version} from {url}")
        return

    with tempfile.TemporaryDirectory() as tmp:
        payload = Path(tmp) / archive_name
        fetch(url, payload)
        staging = Path(tmp) / "staging"
        extract_archive(payload, staging, strip=int(spec.get("strip") or 0))

        # Replace the tree wholesale: an upgrade that merged into the old one
        # would leave files the new release no longer ships.
        OPT_DIR.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(staging), str(target))

    links = []
    for binary in spec["bin"]:
        links.append(str(link_bin(find_binary(target, binary), binary)))

    state["archive"][name] = {
        "version": version,
        "dir": str(target),
        "links": links,
        "desktop": install_desktop_entries(name, target, spec),
    }
    detail(f"{name}: installed {version}")
    installed.append(name)


def sync_archive(desired: dict, state: dict, *, dry_run: bool, upgrade: bool) -> None:
    if not desired and not state["archive"]:
        return

    log("archives")

    for name, spec in desired.items():
        install_archive(name, spec, state, dry_run=dry_run, upgrade=upgrade)

    for name in [n for n in list(state["archive"]) if n not in desired]:
        entry = state["archive"][name]
        if dry_run:
            detail(f"would remove {entry['dir']}")
            continue
        for path in entry.get("links", []) + entry.get("desktop", []):
            Path(path).unlink(missing_ok=True)
        shutil.rmtree(entry["dir"], ignore_errors=True)
        detail(f"{name}: removed")
        removed.append(name)
        del state["archive"][name]


# --------------------------------------------------------------------------- npm


def npm_installed_version(package: str) -> str | None:
    manifest = NPM_PREFIX / "lib/node_modules" / package / "package.json"
    if not manifest.exists():
        return None
    try:
        return json.loads(manifest.read_text()).get("version")
    except json.JSONDecodeError:
        return None


def npm_latest_version(package: str) -> str | None:
    try:
        return json.loads(fetch_text(f"https://registry.npmjs.org/{package}/latest"))[
            "version"
        ]
    except (urllib.error.URLError, KeyError, json.JSONDecodeError):
        return None


def sync_npm(
    desired: list, state: dict, *, dry_run: bool, verbose: bool, upgrade: bool
) -> None:
    if not desired and not state["npm"]:
        return

    log("npm")

    if not shutil.which("npm"):
        warn("npm is not on PATH; skipping the npm section")
        return

    for package in desired:
        current = npm_installed_version(package)

        if current and not upgrade:
            detail(f"{package}: already at {current}")
            note_skip()
            state["npm"][package] = current
            continue

        latest = npm_latest_version(package) if current else None
        if current and latest == current:
            detail(f"{package}: already at {current}")
            note_skip()
            continue

        if dry_run:
            detail(f"would install {package}")
            continue

        run(
            ["npm", "install", "-g", "--prefix", str(NPM_PREFIX), f"{package}@latest"],
            dry_run=False,
            verbose=verbose,
        )
        version = npm_installed_version(package) or "unknown"
        detail(f"{package}: installed {version}")
        installed.append(package)
        state["npm"][package] = version

    for package in [p for p in list(state["npm"]) if p not in desired]:
        run(
            ["npm", "uninstall", "-g", "--prefix", str(NPM_PREFIX), package],
            dry_run=dry_run,
            verbose=verbose,
            check=False,
        )
        detail(f"{package}: removed")
        removed.append(package)
        if not dry_run:
            del state["npm"][package]


# ---------------------------------------------------------------------------- go


def sync_go(
    desired: dict, state: dict, *, dry_run: bool, verbose: bool, upgrade: bool
) -> None:
    if not desired and not state["go"]:
        return

    log("go install")

    go_binary = shutil.which("go", path=child_env()["PATH"])
    if not go_binary and not dry_run:
        warn("go is not on PATH; skipping the go section")
        return

    for name, package in desired.items():
        binary = BIN_DIR / name

        if binary.exists() and not upgrade:
            detail(f"{name}: already installed")
            note_skip()
            state["go"][name] = package
            continue

        if dry_run:
            detail(f"would go install {package}")
            continue

        env = child_env()
        env["GOBIN"] = str(BIN_DIR)
        BIN_DIR.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [go_binary, "install", package],
            capture_output=not verbose,
            text=True,
            env=env,
        )
        if result.returncode != 0:
            warn(f"{name}: go install failed")
            if not verbose and result.stderr:
                for line in result.stderr.splitlines():
                    detail(f"    {line}")
            continue

        detail(f"{name}: installed")
        installed.append(name)
        state["go"][name] = package

    for name in [n for n in list(state["go"]) if n not in desired]:
        if dry_run:
            detail(f"would remove {BIN_DIR / name}")
            continue
        (BIN_DIR / name).unlink(missing_ok=True)
        detail(f"{name}: removed")
        removed.append(name)
        del state["go"][name]


# ------------------------------------------------------------------------- fonts


def sync_fonts(desired: dict, state: dict, *, dry_run: bool, upgrade: bool) -> None:
    if not desired and not state["fonts"]:
        return

    log("fonts")

    touched = False

    for name, spec in desired.items():
        known = state["fonts"].get(name)
        target = FONT_DIR / name

        if known and target.exists() and not upgrade:
            detail(f"{name}: already at {known['version']}")
            note_skip()
            continue

        try:
            tag = spec.get("version") or github_latest_tag(spec["repo"])
        except UpstreamUnavailable as error:
            if known:
                warn(f"{name}: {error}; keeping {known['version']}")
                continue
            raise SystemExit(str(error)) from error

        asset = format_asset(spec["asset"], tag)
        url = f"https://github.com/{spec['repo']}/releases/download/{tag}/{asset}"

        if known and known["version"] == tag and target.exists():
            detail(f"{name}: already at {tag}")
            note_skip()
            continue

        if dry_run:
            detail(f"would fetch {name} {tag} from {url}")
            continue

        with tempfile.TemporaryDirectory() as tmp:
            payload = Path(tmp) / asset
            fetch(url, payload)
            staging = Path(tmp) / "staging"
            extract_archive(payload, staging)

            FONT_DIR.mkdir(parents=True, exist_ok=True)
            if target.exists():
                shutil.rmtree(target)
            target.mkdir(parents=True)
            # Nerd Fonts archives carry licences and READMEs beside the faces;
            # fontconfig only cares about the faces.
            for face in list(staging.rglob("*.ttf")) + list(staging.rglob("*.otf")):
                shutil.copy2(face, target / face.name)

        state["fonts"][name] = {"version": tag, "dir": str(target)}
        detail(f"{name}: installed {tag}")
        installed.append(name)
        touched = True

    for name in [n for n in list(state["fonts"]) if n not in desired]:
        if dry_run:
            detail(f"would remove {state['fonts'][name]['dir']}")
            continue
        shutil.rmtree(state["fonts"][name]["dir"], ignore_errors=True)
        detail(f"{name}: removed")
        removed.append(name)
        del state["fonts"][name]
        touched = True

    if touched and not dry_run and shutil.which("fc-cache"):
        subprocess.run(["fc-cache", "-f", str(FONT_DIR)], capture_output=True)
        detail("font cache rebuilt")


# ------------------------------------------------------------------------- files


def seed_spec(value) -> tuple[str, int]:
    """A seed is either a plain source path or {source, mode}."""
    if isinstance(value, dict):
        mode = value.get("mode", 0o644)
        return value["source"], int(str(mode), 8) if isinstance(mode, str) else mode
    return value, 0o644


def sync_files(
    files: dict, seeds: dict, lines: dict, state: dict, *, dry_run: bool
) -> None:
    if not (files or seeds or lines or state["files"]):
        return

    log("files")

    managed = []

    for target, source in files.items():
        destination = expand(target)
        origin = REPO_ROOT / source
        managed.append(str(destination))

        if destination.exists() and destination.read_bytes() == origin.read_bytes():
            detail(f"{target}: up to date")
            note_skip()
            continue

        if dry_run:
            detail(f"would write {target}")
            continue

        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, destination)
        destination.chmod(0o644)
        detail(f"{target}: written")
        installed.append(target)

    for target, value in seeds.items():
        destination = expand(target)
        source, mode = seed_spec(value)

        if destination.exists():
            detail(f"{target}: left alone (seeded earlier)")
            note_skip()
            continue

        if dry_run:
            detail(f"would seed {target}")
            continue

        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / source, destination)
        destination.chmod(mode)
        detail(f"{target}: seeded")
        installed.append(target)

    for target, entries in lines.items():
        destination = expand(target)
        existing = destination.read_text() if destination.exists() else ""
        additions = []

        for entry in entries:
            if re.search(entry["match"], existing, flags=re.MULTILINE):
                detail(f"{target}: {entry['line'].split()[0]} already set")
                note_skip()
                continue
            additions.append(entry["line"])

        if not additions:
            continue

        if dry_run:
            detail(f"would append {len(additions)} line(s) to {target}")
            continue

        destination.parent.mkdir(parents=True, exist_ok=True)
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        with destination.open("a") as handle:
            handle.write(prefix + "\n".join(additions) + "\n")
        detail(f"{target}: appended {len(additions)} line(s)")
        installed.append(target)

    for path in [p for p in state["files"] if p not in managed]:
        if dry_run:
            detail(f"would remove {path}")
        else:
            Path(path).unlink(missing_ok=True)
        detail(f"{path}: removed")
        removed.append(path)

    state["files"] = sorted(managed)


# ------------------------------------------------------------------------- shell


def render_shell(shell: dict) -> str:
    lines = [
        SHELL_MARKER,
        "# Generated by provision.py — edits here are lost on the next run.",
        "",
    ]

    for name, value in (shell.get("env") or {}).items():
        lines.append(f'export {name}="{value}"')

    for entry in shell.get("path") or []:
        # Guarded so repeated sourcing (a login shell inside a login shell)
        # does not grow PATH without bound.
        lines.append(f'case ":$PATH:" in *":{entry}:"*) ;; *) PATH="{entry}:$PATH";; esac')

    if shell.get("path"):
        lines.append("export PATH")

    lines += [
        f"alias {name}='{value}'" for name, value in (shell.get("aliases") or {}).items()
    ]
    lines += [
        f'command -v {command.split()[0]} >/dev/null 2>&1 && eval "$({command})"'
        for command in shell.get("init") or []
    ]
    lines += list(shell.get("snippets") or [])
    lines += ["", "# <<< dotfiles provision <<<"]

    return "\n".join(lines) + "\n"


def sync_shell(shell: dict, *, dry_run: bool) -> None:
    if not shell:
        return

    log("shell")

    content = render_shell(shell)

    if SHELL_SNIPPET.exists() and SHELL_SNIPPET.read_text() == content:
        detail("snippet: up to date")
        note_skip()
    elif dry_run:
        detail(f"would write {SHELL_SNIPPET}")
    else:
        SHELL_SNIPPET.parent.mkdir(parents=True, exist_ok=True)
        SHELL_SNIPPET.write_text(content)
        detail(f"snippet: written to {SHELL_SNIPPET}")
        installed.append(str(SHELL_SNIPPET))

    # The distribution's ~/.bashrc keeps its own content; it only gains one
    # guarded line, which is why no backup of the original is ever needed.
    bashrc = HOME / ".bashrc"
    hook = f'[ -f "{SHELL_SNIPPET}" ] && . "{SHELL_SNIPPET}"'
    existing = bashrc.read_text() if bashrc.exists() else ""

    if hook in existing:
        detail("~/.bashrc: already sources the snippet")
        note_skip()
    elif dry_run:
        detail("would append the source line to ~/.bashrc")
    else:
        with bashrc.open("a") as handle:
            handle.write(f"\n{hook}\n")
        detail("~/.bashrc: source line appended")
        installed.append("~/.bashrc")


# -------------------------------------------------------------------------- main


def merge(common: dict, gui: dict, key: str, empty):
    """Overlay the gui section on top of common for one key."""
    base = common.get(key) or empty
    extra = gui.get(key) or empty

    if isinstance(empty, list):
        return list(base) + [item for item in extra if item not in base]

    merged = dict(base)
    for name, value in extra.items():
        if isinstance(value, dict) and isinstance(merged.get(name), dict):
            merged[name] = {**merged[name], **value}
        else:
            merged[name] = value
    return merged


def merge_apt(common: dict, gui: dict) -> dict:
    base = common.get("apt") or {}
    extra = gui.get("apt") or {}
    packages = list(base.get("packages") or [])
    packages += [p for p in (extra.get("packages") or []) if p not in packages]
    return {
        "packages": packages,
        "links": {**(base.get("links") or {}), **(extra.get("links") or {})},
    }


def check_platform() -> None:
    if sys.platform != "linux":
        raise SystemExit("provision.py targets Linux only")
    if not shutil.which("dpkg-query"):
        raise SystemExit(
            "provision.py needs a Debian or Ubuntu host; dpkg-query is missing"
        )
    if os.geteuid() == 0:
        raise SystemExit(
            "Run provision.py as your own user; it calls sudo where root is needed"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=str(Path(__file__).parent / "config.yaml"))
    parser.add_argument(
        "--gui",
        dest="gui",
        action="store_true",
        default=None,
        help="force the gui section on",
    )
    parser.add_argument(
        "--no-gui", dest="gui", action="store_false", help="force the gui section off"
    )
    parser.add_argument(
        "--upgrade", action="store_true", help="re-check upstream versions"
    )
    parser.add_argument("--dry-run", action="store_true", help="only report actions")
    parser.add_argument("--verbose", action="store_true", help="stream command output")
    parser.add_argument(
        "--only",
        action="append",
        choices=["apt", "github", "archive", "npm", "go", "fonts", "files", "shell"],
        help="run just these sections (repeatable)",
    )
    args = parser.parse_args()

    check_platform()

    config = yaml.safe_load(Path(args.config).read_text()) or {}
    common = config.get("common") or {}
    gui_section = config.get("gui") or {}

    if args.gui is None:
        want_gui, reason = detect_gui()
        origin = f"detected ({reason})"
    else:
        want_gui, origin = args.gui, "forced by flag"

    gui = gui_section if want_gui else {}
    sections = set(args.only) if args.only else None

    def wanted(section: str) -> bool:
        return sections is None or section in sections

    log(f"Config:  {args.config}")
    log(f"GUI:     {'yes' if want_gui else 'no'} — {origin}")
    log(f"Mode:    {'dry run' if args.dry_run else 'apply'}")
    log("")

    state = load_state()

    # A section that aborts — an unreachable upstream, a failed apt — must not
    # throw away the record of what the earlier sections already installed, or
    # the next run would treat those as foreign and refuse to manage them.
    try:
        if wanted("apt"):
            sync_apt(
                merge_apt(common, gui),
                state,
                dry_run=args.dry_run,
                verbose=args.verbose,
            )
        if wanted("github"):
            sync_github(
                merge(common, gui, "github", {}),
                state,
                dry_run=args.dry_run,
                upgrade=args.upgrade,
            )
        if wanted("archive"):
            sync_archive(
                merge(common, gui, "archive", {}),
                state,
                dry_run=args.dry_run,
                upgrade=args.upgrade,
            )
        if wanted("npm"):
            sync_npm(
                merge(common, gui, "npm", []),
                state,
                dry_run=args.dry_run,
                verbose=args.verbose,
                upgrade=args.upgrade,
            )
        if wanted("go"):
            sync_go(
                merge(common, gui, "go", {}),
                state,
                dry_run=args.dry_run,
                verbose=args.verbose,
                upgrade=args.upgrade,
            )
        if wanted("fonts"):
            sync_fonts(
                merge(common, gui, "fonts", {}),
                state,
                dry_run=args.dry_run,
                upgrade=args.upgrade,
            )
        if wanted("files"):
            sync_files(
                merge(common, gui, "files", {}),
                merge(common, gui, "seeds", {}),
                merge(common, gui, "lines", {}),
                state,
                dry_run=args.dry_run,
            )
        if wanted("shell"):
            sync_shell(merge(common, gui, "shell", {}), dry_run=args.dry_run)
    finally:
        state["gui"] = want_gui
        save_state(state, dry_run=args.dry_run)

    log("")
    verb = "would change" if args.dry_run else "changed"
    log(
        f"Done. {len(installed)} {verb}, {skipped} already current, "
        f"{len(removed)} removed, {len(warnings)} warning(s)."
    )


if __name__ == "__main__":
    main()
