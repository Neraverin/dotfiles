#!/usr/bin/env python3
"""Provision a machine from config.yaml using the distribution's own tooling.

It does what this repository actually needs: install a declared set of packages,
place a few config files, and extend the shell, using the distribution's own
tooling. There is no atomic switch and no rollback, which is deliberate — the
cost of those was the whole reason to leave the previous setup.

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
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from host import (
    BIN_DIR,
    DESKTOP_DIR,
    FONT_DIR,
    HOME,
    NPM_PREFIX,
    OPT_DIR,
    REPO_ROOT,
    SHELL_MARKER,
    SHELL_SNIPPET,
    STATE_PATH,
    USER_AGENT,
    child_env,
    counts,
    detail,
    ensure_sudo,
    expand,
    extract_archive,
    fetch,
    fetch_text,
    find_binary,
    install_as_root,
    link_bin,
    load_state,
    log,
    note_installed,
    note_removed,
    note_skip,
    note_warning,
    read_root_file,
    run,
    save_state,
    staging_area,
    step_end,
    step_start,
    sudo,
    username,
    warn,
)

try:
    import yaml
except ImportError:  # pragma: no cover - environment problem, not a code path
    sys.exit("PyYAML is missing. Install it with: sudo apt-get install -y python3-yaml")



# ------------------------------------------------------------------ gui detection


def detect_gui() -> tuple[bool, str]:
    """Guess whether this host has a desktop.

    Evidence that desktop software is actually installed decides this; the
    systemd default target does not. Debian leaves default.target at
    graphical.target on a plain netinstall server with no X, no Wayland and no
    display manager, so trusting it drags fonts and a music player onto hosts
    that can never show a window. WSL is treated as headless: WSLg can display
    windows, but none of the desktop entries here make sense there.
    """
    version_file = Path("/proc/version")
    if version_file.exists() and "microsoft" in version_file.read_text().lower():
        return False, "WSL kernel"

    if os.environ.get("XDG_CURRENT_DESKTOP"):
        return True, f"XDG_CURRENT_DESKTOP={os.environ['XDG_CURRENT_DESKTOP']}"

    for package in (
        "gnome-shell",
        "xserver-xorg-core",
        "sway",
        "kde-plasma-desktop",
        "xwayland",
    ):
        if dpkg_installed(package):
            return True, f"{package} is installed"

    for manager in ("gdm3", "sddm", "lightdm", "xdm"):
        if dpkg_installed(manager):
            return True, f"{manager} is installed"

    # Only ever used to rule a desktop out, never to conclude there is one.
    if shutil.which("systemctl"):
        result = subprocess.run(
            ["systemctl", "get-default"], capture_output=True, text=True
        )
        target = result.stdout.strip()
        if target and target != "graphical.target":
            return False, f"systemd default target is {target}"

    return False, "no desktop software installed"


# ------------------------------------------------------------- apt repositories

APT_KEYRING_DIR = Path("/etc/apt/keyrings")
APT_SOURCES_DIR = Path("/etc/apt/sources.list.d")


def apt_key_path(name: str) -> Path:
    # Kept ASCII-armoured: apt reads either form as long as the extension says
    # which one it is, and .asc means no gpg --dearmor step and no gnupg on the
    # host at all.
    return APT_KEYRING_DIR / f"{name}.asc"


def apt_sources_path(name: str) -> Path:
    return APT_SOURCES_DIR / f"{name}.sources"


def host_facts() -> dict:
    """What a third-party apt repository has to be addressed by on this host.

    Upstreams publish one tree per distribution and one suite per release, so the
    URL cannot be spelled out in config.yaml: the same entry has to resolve to
    debian/bookworm on the WSL instance and ubuntu/resolute on the workstation.
    """
    values: dict[str, str] = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key] = value.strip().strip('"')
    except OSError:
        pass

    architecture = subprocess.run(
        ["dpkg", "--print-architecture"], capture_output=True, text=True
    ).stdout.strip()

    return {
        "id": values.get("ID", ""),
        "codename": values.get("VERSION_CODENAME", ""),
        "arch": architecture,
    }


def apt_repo_field(value, facts: dict) -> str:
    items = value if isinstance(value, list) else [value]
    return " ".join(str(item).format(**facts) for item in items)


def render_apt_repo(name: str, spec: dict, facts: dict) -> str:
    """One deb822 stanza — the format apt prefers, and free of quoting rules."""
    fields = {
        "Types": apt_repo_field(spec.get("types", "deb"), facts),
        "URIs": apt_repo_field(spec["uri"], facts),
        "Suites": apt_repo_field(spec.get("suites", "{codename}"), facts),
        "Components": apt_repo_field(spec.get("components", "main"), facts),
        "Architectures": apt_repo_field(spec.get("architectures", "{arch}"), facts),
        "Signed-By": str(apt_key_path(name)),
    }
    return "".join(f"{key}: {value}\n" for key, value in fields.items())


def key_fingerprint(path: Path) -> str:
    """Primary key fingerprint, or "" when the file holds no usable key."""
    result = subprocess.run(
        ["gpg", "--show-keys", "--with-colons", str(path)],
        capture_output=True,
        text=True,
    )
    for line in result.stdout.splitlines():
        fields = line.split(":")
        if fields[0] == "fpr":
            return fields[9]
    return ""


def apt_repo_reachable(spec: dict, facts: dict) -> bool:
    """Does the repository actually carry a suite for this release?

    A repository with no directory for a fresh distribution release breaks every
    later `apt-get update` on the host, not just its own packages, so nothing is
    written until the suite is known to exist.
    """
    suites = spec.get("suites", "{codename}")
    suite = apt_repo_field(suites, facts).split(" ")[0]
    if suite.endswith("/"):  # a flat repository has no dists/ tree to probe
        return True

    url = f"{apt_repo_field(spec['uri'], facts).rstrip('/')}/dists/{suite}/Release"
    request = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(request, timeout=30):
            return True
    except (urllib.error.URLError, OSError):
        return False


def sync_apt_repos(desired: dict, bucket: dict, *, dry_run: bool, verbose: bool) -> bool:
    """Write the declared repositories. True when apt has to re-read its lists."""
    owned = list(bucket["repos"])
    stale = [name for name in owned if name not in desired]

    if not desired and not stale:
        return False

    facts = host_facts()
    changed = False
    keep = [name for name in owned if name in desired]

    for index, (name, spec) in enumerate(desired.items(), start=1):
        if not facts["id"] or not facts["codename"]:
            warn(
                f"{name}: /etc/os-release names no distribution and release; "
                "repository skipped"
            )
            continue

        stanza = render_apt_repo(name, spec, facts)
        sources = apt_sources_path(name)
        current = read_root_file(sources)

        # Files some packages read *before* they are configured — the one case
        # so far is a package whose postinst registers a second copy of this
        # repository unless told not to. They belong to the repository entry
        # rather than to the package, because they have to exist before apt
        # runs, and they are recorded in state so dropping the entry can take
        # them away again.
        declared_files = {
            path: content for path, content in (spec.get("files") or {}).items()
        }
        for path, content in declared_files.items():
            target = Path(path)
            if read_root_file(target) == content:
                continue
            if dry_run:
                detail(f"{name}: would write {target}")
            else:
                ensure_sudo(dry_run=False)
                install_as_root(content.encode(), target, verbose=verbose)
                detail(f"{name}: wrote {target}")
            changed = True
        bucket["repo_files"][name] = sorted(declared_files)

        if current == stanza and apt_key_path(name).exists():
            detail(f"{name}: repository already configured")
            note_skip()
            if name not in keep:
                keep.append(name)
            continue

        # Same rule as for packages: a file this tool did not write is not ours
        # to rewrite, and never becomes ours to remove.
        if current and name not in owned:
            warn(f"{name}: {sources} was not written here; left alone")
            continue

        if not apt_repo_reachable(spec, facts):
            warn(
                f"{name}: no {facts['codename']} suite at "
                f"{apt_repo_field(spec['uri'], facts)}; repository skipped"
            )
            continue

        if dry_run:
            detail(f"{name}: would add {apt_repo_field(spec['uri'], facts)}")
            note_installed(f"{name} repository")
            changed = True
            continue

        ensure_sudo(dry_run=False)
        label = step_start(index, len(desired), f"{name} repository")
        try:
            with staging_area(STATE_PATH.parent) as staging:
                key = staging / f"{name}.asc"
                fetch(apt_repo_field(spec["key"], facts), key)
                # A wrong or truncated key only surfaces later, as NO_PUBKEY on
                # every apt-get update on the host. Upstreams that publish a
                # fingerprint let us catch it while it is still attributable.
                expected = spec.get("fingerprint")
                if expected:
                    actual = key_fingerprint(key)
                    if actual != expected:
                        step_end(label, "failed")
                        raise SystemExit(
                            f"{name}: key fingerprint {actual or 'none'}, "
                            f"expected {expected}"
                        )
                install_as_root(key.read_bytes(), apt_key_path(name), verbose=verbose)
            install_as_root(stanza.encode(), sources, verbose=verbose)
        except SystemExit:
            step_end(label, "failed")
            raise
        step_end(label, "added")
        note_installed(f"{name} repository")
        if name not in keep:
            keep.append(name)
        changed = True

    for name in stale:
        paths = [apt_sources_path(name), apt_key_path(name)]
        paths += [Path(p) for p in bucket["repo_files"].pop(name, [])]
        if not any(path.exists() for path in paths):
            continue
        if dry_run:
            detail(f"{name}: would remove repository")
        else:
            ensure_sudo(dry_run=False)
            run(
                sudo(["rm", "-f", *[str(path) for path in paths]]),
                dry_run=False,
                verbose=verbose,
            )
            detail(f"{name}: repository removed")
        note_removed(f"{name} repository")
        changed = True

    bucket["repos"] = sorted(keep)
    return changed


# --------------------------------------------------------------------------- apt


def dpkg_installed(package: str) -> bool:
    result = subprocess.run(
        ["dpkg-query", "-W", "-f=${Status}", package], capture_output=True, text=True
    )
    return result.stdout.startswith("install ok installed")


def apt_version(package: str) -> str:
    result = subprocess.run(
        ["dpkg-query", "-W", "-f=${Version}", package], capture_output=True, text=True
    )
    return result.stdout.strip() or "unknown"


def sync_apt(
    desired: dict, bucket: dict, *, dry_run: bool, verbose: bool, heading: str = "apt"
) -> None:
    packages = desired.get("packages") or []
    links = desired.get("links") or {}
    repos = desired.get("repos") or {}

    if not packages and not bucket["packages"] and not repos:
        return

    log(heading)

    # Repositories first: a package below may only exist in one of them.
    repos_changed = sync_apt_repos(repos, bucket, dry_run=dry_run, verbose=verbose)

    missing = [p for p in packages if not dpkg_installed(p)]
    stale = [
        p
        for p in bucket["packages"]
        if p not in packages and dpkg_installed(p)
    ]

    if missing or stale:
        ensure_sudo(dry_run=dry_run)

    if missing or repos_changed:
        run(sudo(["apt-get", "update"]), dry_run=dry_run, verbose=verbose)

    # One apt-get call per package. Slower than a single batched call — apt
    # re-reads its state each time — but a batch is opaque: it either prints
    # nothing for a minute or fails without saying which package broke.
    for index, package in enumerate(missing, start=1):
        if dry_run:
            detail(f"[{index}/{len(missing)}] {package} ... would install")
            note_installed(package)
            continue

        label = step_start(index, len(missing), package)
        try:
            run(
                sudo(["apt-get", "install", "-y", package]),
                dry_run=False,
                verbose=verbose,
            )
        except SystemExit:
            step_end(label, "failed")
            raise
        step_end(label, f"done ({apt_version(package)})")
        note_installed(package)

    # Only packages this tool installed are ever removed; anything the
    # distribution or the user brought in is left alone.
    for index, package in enumerate(stale, start=1):
        if dry_run:
            detail(f"[{index}/{len(stale)}] {package} ... would remove")
            note_removed(package)
            continue

        label = step_start(index, len(stale), package)
        try:
            run(
                sudo(["apt-get", "remove", "-y", package]),
                dry_run=False,
                verbose=verbose,
            )
        except SystemExit:
            step_end(label, "failed")
            raise
        step_end(label, "removed")
        note_removed(package)

    current = len(packages) - len(missing)
    if current:
        detail(f"{current} already current")
    note_skip(current)

    # Record only what we installed, so a package that predates this tool is
    # never treated as ours on a later run.
    owned = set(bucket["packages"]) | set(missing)
    bucket["packages"] = sorted(owned & set(packages))

    sync_apt_links(links, bucket, dry_run=dry_run)


def sync_apt_links(links: dict, bucket: dict, *, dry_run: bool) -> None:
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
        note_installed(wanted)

    for wanted in [w for w in bucket["links"].values() if w not in links.values()]:
        path = BIN_DIR / wanted
        if path.is_symlink():
            if not dry_run:
                path.unlink()
            detail(f"{wanted}: link removed")
            note_removed(wanted)

    bucket["links"] = dict(links)


# ---------------------------------------------- snap, vendor debs, vendor scripts
#
# Three ways desktop software arrives that apt cannot describe on its own. They
# are declared like every other source, but unlike the rest of this tool they
# install system-wide, so they are reached only through the `workstation`
# section — never from a plain run.


def snap_version(name: str) -> str:
    result = subprocess.run(["snap", "list", name], capture_output=True, text=True)
    if result.returncode != 0:
        return ""
    lines = result.stdout.splitlines()
    return lines[1].split()[1] if len(lines) > 1 else ""


def sync_snap(desired: list, bucket: dict, *, dry_run: bool, verbose: bool) -> None:
    owned = list(bucket["snap"])
    stale = [name for name in owned if name not in desired]

    if not desired and not stale:
        return

    log("snap")

    for index, name in enumerate(desired, start=1):
        version = snap_version(name)
        if version:
            detail(f"{name}: already at {version}")
            note_skip()
            if name not in owned:
                owned.append(name)
            continue

        if dry_run:
            detail(f"{name}: would install")
            note_installed(name)
            continue

        ensure_sudo(dry_run=False)
        label = step_start(index, len(desired), name)
        run(sudo(["snap", "install", name]), dry_run=False, verbose=verbose)
        step_end(label, f"installed {snap_version(name) or 'unknown'}")
        note_installed(name)
        if name not in owned:
            owned.append(name)

    for name in stale:
        if not snap_version(name):
            owned.remove(name)
            continue
        if dry_run:
            detail(f"{name}: would remove")
        else:
            ensure_sudo(dry_run=False)
            run(sudo(["snap", "remove", name]), dry_run=False, verbose=verbose)
            detail(f"{name}: removed")
        note_removed(name)
        owned.remove(name)

    bucket["snap"] = sorted(set(owned) & set(desired)) if not dry_run else owned
    log("")


def deb_version(package: str) -> str:
    result = subprocess.run(
        ["dpkg-query", "-W", "-f=${Version}", package], capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def sync_deb(
    desired: dict, bucket: dict, *, dry_run: bool, verbose: bool, upgrade: bool
) -> None:
    """Packages published as a .deb on a GitHub release and nowhere else."""
    owned = dict(bucket["deb"])
    stale = [name for name in owned if name not in desired]

    if not desired and not stale:
        return

    log("deb")
    facts = host_facts()

    for index, (name, spec) in enumerate(desired.items(), start=1):
        package = spec.get("package", name)
        # Upstreams label their assets by their own architecture names.
        asset_arch = (spec.get("arch") or {}).get(facts["arch"])
        if asset_arch is None:
            note_warning(f"{name}: no build for {facts['arch']}; skipped")
            continue

        present = deb_version(package)
        known = owned.get(name)

        # Same rule as the github source: an installed package is left alone
        # until --upgrade, which keeps an ordinary run offline and fast.
        if known and present and not upgrade:
            detail(f"{name}: already at {present}")
            note_skip()
            continue

        try:
            tag = spec.get("version") or github_latest_tag(spec["repo"])
        except UpstreamUnavailable as error:
            if present:
                note_warning(f"{name}: {error}; keeping {present}")
                continue
            raise SystemExit(str(error)) from error

        # Some upstreams add a build suffix (4.1.1-312) the release tag lacks.
        if present and present.split("-")[0] == tag.lstrip("v"):
            detail(f"{name}: already at {present}")
            note_skip()
            owned[name] = {"version": tag, "package": package}
            continue

        asset = spec["asset"].format(
            version=tag, version_strip=tag.lstrip("v"), arch=asset_arch
        )
        url = f"https://github.com/{spec['repo']}/releases/download/{tag}/{asset}"

        if dry_run:
            detail(f"{name}: would fetch {tag} from {url}")
            note_installed(name)
            continue

        ensure_sudo(dry_run=False)
        label = step_start(index, len(desired), name)
        with staging_area(STATE_PATH.parent) as staging:
            payload = staging / asset
            fetch(url, payload)
            run(
                sudo(["apt-get", "install", "-y", str(payload)]),
                dry_run=False,
                verbose=verbose,
            )
        step_end(label, f"installed {deb_version(package) or tag}")
        note_installed(name)
        owned[name] = {"version": tag, "package": package}

    for name in stale:
        package = owned[name]["package"]
        if deb_version(package):
            if dry_run:
                detail(f"{name}: would remove")
            else:
                ensure_sudo(dry_run=False)
                run(
                    sudo(["apt-get", "remove", "-y", package]),
                    dry_run=False,
                    verbose=verbose,
                )
                detail(f"{name}: removed")
            note_removed(name)
        owned.pop(name)

    bucket["deb"] = owned
    log("")


def sync_script(desired: dict, bucket: dict, *, dry_run: bool, verbose: bool) -> None:
    """Upstreams whose only supported install is a shell script they host.

    The script is run once, when the command it provides is missing. Anything
    under `post` is re-applied on every run instead: it is cheap, and a package
    reinstall or a logout drops it.
    """
    owned = dict(bucket["script"])
    stale = [name for name in owned if name not in desired]

    if not desired and not stale:
        return

    log("script")

    for index, (name, spec) in enumerate(desired.items(), start=1):
        probe = spec.get("probe", name)

        if shutil.which(probe):
            detail(f"{name}: already installed")
            note_skip()
        elif dry_run:
            detail(f"{name}: would run {spec['url']}")
            note_installed(name)
        else:
            ensure_sudo(dry_run=False)
            label = step_start(index, len(desired), name)
            with staging_area(STATE_PATH.parent) as staging:
                installer = staging / "install.sh"
                fetch(spec["url"], installer)
                installer.chmod(0o755)
                run(sudo([str(installer)]), dry_run=False, verbose=verbose)
            step_end(label, "installed")
            note_installed(name)

        owned[name] = {"probe": probe}

        for command in spec.get("post") or []:
            rendered = [part.format(user=username()) for part in command]
            run(rendered, dry_run=dry_run, verbose=verbose)

    for name in stale:
        # A vendor installer carries no uninstall, and guessing at one would
        # mean deleting files this tool never saw. Say so and leave it.
        note_warning(
            f"{name}: installed by a vendor script; remove it by hand if unwanted"
        )
        owned.pop(name)

    bucket["script"] = owned
    log("")


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
) -> str:
    """Install one release asset and return the status for its step line."""
    names = spec.get("binaries") or [spec["binary"]]
    known = state["github"].get(name)
    present = all((BIN_DIR / b).exists() for b in names)

    # Without --upgrade an installed tool is left alone, which keeps ordinary
    # runs offline and fast; the API is only consulted when something may change.
    if known and present and not upgrade:
        note_skip()
        return f"already at {known['version']}"

    try:
        tag = spec.get("version") or github_latest_tag(spec["repo"])
    except UpstreamUnavailable as error:
        if known:
            note_warning(f"{name}: {error}; keeping {known['version']}")
            return f"kept {known['version']} (upstream unavailable)"
        raise SystemExit(str(error)) from error

    asset = format_asset(spec["asset"], tag)
    url = f"https://github.com/{spec['repo']}/releases/download/{tag}/{asset}"

    if known and known["version"] == tag and present:
        note_skip()
        return f"already at {tag}"

    if dry_run:
        note_installed(name)
        return f"would fetch {tag} from {url}"

    with staging_area(BIN_DIR) as tmp:
        tmp_path = tmp
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
    note_installed(name)
    return f"done ({tag})"


def sync_github(desired: dict, state: dict, *, dry_run: bool, upgrade: bool) -> None:
    if not desired and not state["github"]:
        return

    log("github releases")

    for index, (name, spec) in enumerate(desired.items(), start=1):
        label = step_start(index, len(desired), name)
        try:
            status = install_github(
                name, spec, state, dry_run=dry_run, upgrade=upgrade
            )
        except SystemExit:
            step_end(label, "failed")
            raise
        step_end(label, status)

    for name in [n for n in list(state["github"]) if n not in desired]:
        for path in state["github"][name]["files"]:
            if dry_run:
                detail(f"would remove {path}")
            else:
                Path(path).unlink(missing_ok=True)
        note_removed(name)
        if not dry_run:
            detail(f"{name}: removed")
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

    Nothing puts ~/.local/opt on XDG_DATA_DIRS, so the entries have to land in
    ~/.local/share/applications and spell out full paths.
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
) -> str:
    """Install one tarball and return the status for its step line."""
    known = state["archive"].get(name)
    target = OPT_DIR / name
    present = target.exists() and all((BIN_DIR / b).exists() for b in spec["bin"])

    if known and present and not upgrade:
        note_skip()
        return f"already at {known['version']}"

    try:
        version = resolve_archive_version(spec)
    except UpstreamUnavailable as error:
        if known:
            note_warning(f"{name}: {error}; keeping {known['version']}")
            return f"kept {known['version']} (upstream unavailable)"
        raise SystemExit(str(error)) from error

    if known and known["version"] == version and present:
        note_skip()
        return f"already at {version}"

    url = spec["url"].format(version=version, version_strip=version.lstrip("v"))
    archive_name = (spec.get("archive_name") or url.rsplit("/", 1)[-1]).format(
        version=version, version_strip=version.lstrip("v")
    )

    if dry_run:
        note_installed(name)
        return f"would fetch {version} from {url}"

    with staging_area(OPT_DIR) as tmp:
        payload = tmp / archive_name
        fetch(url, payload)
        unpacked = tmp / "unpacked"
        extract_archive(payload, unpacked, strip=int(spec.get("strip") or 0))
        payload.unlink()

        # Replace the tree wholesale: an upgrade that merged into the old one
        # would leave files the new release no longer ships. Same filesystem,
        # so this is a rename and cannot half-finish.
        if target.exists():
            shutil.rmtree(target)
        unpacked.rename(target)

    links = []
    for binary in spec["bin"]:
        links.append(str(link_bin(find_binary(target, binary), binary)))

    state["archive"][name] = {
        "version": version,
        "dir": str(target),
        "links": links,
        "desktop": install_desktop_entries(name, target, spec),
    }
    note_installed(name)
    return f"done ({version})"


def sync_archive(desired: dict, state: dict, *, dry_run: bool, upgrade: bool) -> None:
    if not desired and not state["archive"]:
        return

    log("archives")

    for index, (name, spec) in enumerate(desired.items(), start=1):
        label = step_start(index, len(desired), name)
        try:
            status = install_archive(
                name, spec, state, dry_run=dry_run, upgrade=upgrade
            )
        except SystemExit:
            step_end(label, "failed")
            raise
        step_end(label, status)

    for name in [n for n in list(state["archive"]) if n not in desired]:
        entry = state["archive"][name]
        if dry_run:
            detail(f"would remove {entry['dir']}")
            note_removed(name)
            continue
        for path in entry.get("links", []) + entry.get("desktop", []):
            Path(path).unlink(missing_ok=True)
        shutil.rmtree(entry["dir"], ignore_errors=True)
        detail(f"{name}: removed")
        note_removed(name)
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

    for index, package in enumerate(desired, start=1):
        current = npm_installed_version(package)
        label = step_start(index, len(desired), package)

        if current and not upgrade:
            step_end(label, f"already at {current}")
            note_skip()
            state["npm"][package] = current
            continue

        latest = npm_latest_version(package) if current else None
        if current and latest == current:
            step_end(label, f"already at {current}")
            note_skip()
            continue

        if dry_run:
            step_end(label, "would install")
            note_installed(package)
            continue

        try:
            run(
                [
                    "npm", "install", "-g",
                    "--prefix", str(NPM_PREFIX),
                    f"{package}@latest",
                ],
                dry_run=False,
                verbose=verbose,
            )
        except SystemExit:
            step_end(label, "failed")
            raise

        version = npm_installed_version(package) or "unknown"
        step_end(label, f"done ({version})")
        note_installed(package)
        state["npm"][package] = version

    for package in [p for p in list(state["npm"]) if p not in desired]:
        run(
            ["npm", "uninstall", "-g", "--prefix", str(NPM_PREFIX), package],
            dry_run=dry_run,
            verbose=verbose,
            check=False,
        )
        if not dry_run:
            detail(f"{package}: removed")
        note_removed(package)
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

    for index, (name, package) in enumerate(desired.items(), start=1):
        binary = BIN_DIR / name
        label = step_start(index, len(desired), name)

        if binary.exists() and not upgrade:
            step_end(label, "already installed")
            note_skip()
            state["go"][name] = package
            continue

        if dry_run:
            step_end(label, f"would go install {package}")
            note_installed(name)
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
            step_end(label, "failed")
            note_warning(f"{name}: go install failed")
            if not verbose and result.stderr:
                for line in result.stderr.splitlines():
                    detail(f"    {line}")
            continue

        step_end(label, "done")
        note_installed(name)
        state["go"][name] = package

    for name in [n for n in list(state["go"]) if n not in desired]:
        if dry_run:
            detail(f"would remove {BIN_DIR / name}")
            note_removed(name)
            continue
        (BIN_DIR / name).unlink(missing_ok=True)
        detail(f"{name}: removed")
        note_removed(name)
        del state["go"][name]


# ------------------------------------------------------------------------- fonts


def sync_fonts(desired: dict, state: dict, *, dry_run: bool, upgrade: bool) -> None:
    if not desired and not state["fonts"]:
        return

    log("fonts")

    touched = False

    for index, (name, spec) in enumerate(desired.items(), start=1):
        known = state["fonts"].get(name)
        target = FONT_DIR / name
        label = step_start(index, len(desired), name)

        if known and target.exists() and not upgrade:
            step_end(label, f"already at {known['version']}")
            note_skip()
            continue

        try:
            tag = spec.get("version") or github_latest_tag(spec["repo"])
        except UpstreamUnavailable as error:
            if known:
                step_end(label, f"kept {known['version']} (upstream unavailable)")
                note_warning(f"{name}: {error}; keeping {known['version']}")
                continue
            step_end(label, "failed")
            raise SystemExit(str(error)) from error

        asset = format_asset(spec["asset"], tag)
        url = f"https://github.com/{spec['repo']}/releases/download/{tag}/{asset}"

        if known and known["version"] == tag and target.exists():
            step_end(label, f"already at {tag}")
            note_skip()
            continue

        if dry_run:
            step_end(label, f"would fetch {tag} from {url}")
            note_installed(name)
            continue

        with staging_area(FONT_DIR) as tmp:
            payload = tmp / asset
            fetch(url, payload)
            staging = tmp / "staging"
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
        step_end(label, f"done ({tag})")
        note_installed(name)
        touched = True

    for name in [n for n in list(state["fonts"]) if n not in desired]:
        if dry_run:
            detail(f"would remove {state['fonts'][name]['dir']}")
            note_removed(name)
            continue
        shutil.rmtree(state["fonts"][name]["dir"], ignore_errors=True)
        detail(f"{name}: removed")
        note_removed(name)
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
        note_installed(target)

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
        note_installed(target)

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
        note_installed(target)

    for path in [p for p in state["files"] if p not in managed]:
        if dry_run:
            detail(f"would remove {path}")
        else:
            Path(path).unlink(missing_ok=True)
            detail(f"{path}: removed")
        note_removed(path)

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
        note_installed(str(SHELL_SNIPPET))

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
        note_installed("~/.bashrc")


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
        "repos": {**(base.get("repos") or {}), **(extra.get("repos") or {})},
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


def run_workstation(
    spec: dict,
    config_path: str,
    *,
    sections: set | None,
    dry_run: bool,
    verbose: bool,
    upgrade: bool,
) -> None:
    """Reconcile the system-wide desktop software, and nothing else.

    Kept apart from the ordinary run on purpose. These entries install outside
    $HOME and exist on one host, so an ordinary `./apply.sh` — which never
    declares them — must not read their absence as an instruction to remove
    them. They get their own state bucket for the same reason.
    """
    log(f"Config:  {config_path}")
    log(f"Mode:    {'dry run' if dry_run else 'apply'}")
    log("Scope:   workstation")
    log("")

    state = load_state()
    bucket = state["workstation"]

    def wanted(section: str) -> bool:
        return sections is None or section in sections

    try:
        if wanted("apt"):
            sync_apt(
                spec.get("apt") or {},
                bucket,
                dry_run=dry_run,
                verbose=verbose,
                heading="apt",
            )
        if wanted("snap"):
            sync_snap(spec.get("snap") or [], bucket, dry_run=dry_run, verbose=verbose)
        if wanted("deb"):
            sync_deb(
                spec.get("deb") or {},
                bucket,
                dry_run=dry_run,
                verbose=verbose,
                upgrade=upgrade,
            )
        if wanted("script"):
            sync_script(
                spec.get("script") or {}, bucket, dry_run=dry_run, verbose=verbose
            )
    finally:
        save_state(state, dry_run=dry_run)

    changed, current, gone, problems = counts()
    if dry_run:
        changed_verb, removed_verb = "would change", "would be removed"
    else:
        changed_verb, removed_verb = "changed", "removed"
    log("")
    log(
        f"Done. {changed} {changed_verb}, {current} already current, "
        f"{gone} {removed_verb}, {problems} warning(s)."
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
        choices=[
            "apt",
            "github",
            "archive",
            "npm",
            "go",
            "fonts",
            "files",
            "shell",
            # --workstation only
            "snap",
            "deb",
            "script",
        ],
        help="run just these sections (repeatable)",
    )
    parser.add_argument(
        "--workstation",
        action="store_true",
        help="reconcile the workstation section instead of the usual ones",
    )
    args = parser.parse_args()

    check_platform()

    config = yaml.safe_load(Path(args.config).read_text()) or {}
    common = config.get("common") or {}
    gui_section = config.get("gui") or {}

    if args.workstation:
        run_workstation(
            config.get("workstation") or {},
            args.config,
            sections=set(args.only) if args.only else None,
            dry_run=args.dry_run,
            verbose=args.verbose,
            upgrade=args.upgrade,
        )
        return

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
                state["apt"],
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
    if args.dry_run:
        changed_verb, removed_verb = "would change", "would be removed"
    else:
        changed_verb, removed_verb = "changed", "removed"
    changed, current, gone, problems = counts()
    log(
        f"Done. {changed} {changed_verb}, {current} already current, "
        f"{gone} {removed_verb}, {problems} warning(s)."
    )


if __name__ == "__main__":
    main()
