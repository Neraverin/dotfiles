#!/usr/bin/env python3
"""How this repository talks to the host it is provisioning.

provision.py and workstation.py drive the same host in the same way — the same
sudo handling, the same numbered step lines, the same staging rules — and
neither is the natural owner of that code. It lives here so that importing one tool does not drag in
the other, and so the counters a run reports are kept in one place rather than
passed around.

Nothing here knows about config.yaml or about any particular kind of package.
"""

from __future__ import annotations

import contextlib
import json
import os
import pwd
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
USER_AGENT = "dotfiles provision (+https://github.com/neraverin/dotfiles)"

installed: list[str] = []
removed: list[str] = []
warnings: list[str] = []
skipped = 0
step_open = False


def note_installed(name: str) -> None:
    installed.append(name)


def note_removed(name: str) -> None:
    removed.append(name)


def counts() -> tuple[int, int, int, int]:
    """(changed, already current, removed, warnings) for the closing summary."""
    return len(installed), skipped, len(removed), len(warnings)


# --------------------------------------------------------------------------- io


def log(message: str) -> None:
    print(message)


def detail(message: str) -> None:
    print(f"  {message}")


def warn(message: str) -> None:
    warnings.append(message)
    print(f"  warning: {message}")


def note_warning(message: str) -> None:
    """Record a warning whose text is already on the item's status line."""
    warnings.append(message)


def step_start(index: int, total: int, name: str) -> str:
    """Open a numbered line for one item and leave it unfinished.

    On a terminal the line reads "in progress" until step_end overwrites it, so
    a long apt install shows which package it is on. Piped to a log there is no
    cursor to rewrite, so the line is simply completed in place.
    """
    global step_open
    step_open = True
    label = f"  [{index}/{total}] {name}"
    print(f"{label} ... in progress" if sys.stdout.isatty() else f"{label} ... ",
          end="", flush=True)
    return label


def step_end(label: str, status: str) -> None:
    global step_open
    was_open, step_open = step_open, False

    if not was_open:
        # Command output was printed underneath, so repeat the item's name;
        # a bare "done" several screens below its heading says nothing.
        print(f"{label} ... {status}", flush=True)
    elif sys.stdout.isatty():
        # Pad to wipe whatever "in progress" left behind when status is shorter.
        print(f"\r{label} ... {status}".ljust(len(label) + 20))
    else:
        print(status, flush=True)


def break_step_line() -> None:
    """Move off an unfinished step line before something else writes.

    Only --verbose and a failing command print underneath an open item; both
    would otherwise run into the trailing "... in progress".
    """
    global step_open
    if step_open:
        print(flush=True)
        step_open = False


def note_skip(count: int = 1) -> None:
    global skipped
    skipped += count


def username() -> str:
    """The invoking user. Not os.getlogin(): that needs a controlling terminal."""
    return pwd.getpwuid(os.getuid()).pw_name


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
        break_step_line()
        # A child writes straight to fd 1; anything still sitting in Python's
        # buffer would surface after it and land out of order.
        sys.stdout.flush()
        return subprocess.run(cmd, check=check, env=child_env()).returncode

    result = subprocess.run(cmd, capture_output=True, text=True, env=child_env())

    if result.returncode != 0 and check:
        break_step_line()
        detail(f"command failed: {' '.join(cmd)}")
        for line in (result.stdout + result.stderr).splitlines():
            detail(f"    {line}")
        raise SystemExit(1)

    return result.returncode


def sudo(cmd: list[str]) -> list[str]:
    return cmd if os.geteuid() == 0 else ["sudo", *cmd]


def ensure_sudo(*, dry_run: bool) -> None:
    """Take the sudo password now, while a prompt can still be answered.

    Every other command runs under capture_output, which swallows sudo's prompt
    while sudo still waits on /dev/tty — a silent hang with nothing on screen.
    Priming the timestamp here keeps the prompt visible and the later calls
    non-interactive. Over `ssh host ./migrate-from-nix.sh` there is no terminal
    at all, so an askpass helper is the only way through.
    """
    if dry_run or os.geteuid() == 0:
        return

    if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0:
        return

    if sys.stdin.isatty():
        detail("apt needs root; sudo will ask for your password")
        if subprocess.run(["sudo", "-v"]).returncode == 0:
            return
        raise SystemExit("sudo authentication failed")

    if os.environ.get("SUDO_ASKPASS"):
        detail("no terminal; asking sudo to use SUDO_ASKPASS")
        if subprocess.run(["sudo", "-A", "-v"], capture_output=True).returncode == 0:
            return
        raise SystemExit("sudo authentication through SUDO_ASKPASS failed")

    raise SystemExit(
        "apt needs root, but there is no terminal to ask for a password on.\n"
        "  Run this from an interactive shell, or point SUDO_ASKPASS at a helper,\n"
        "  or prime the timestamp first with: sudo -v"
    )


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


@contextlib.contextmanager
def staging_area(base: Path):
    """A scratch directory on the same filesystem as `base`.

    The system temp dir is the wrong place for these payloads: it is often a
    small tmpfs, and unpacking there means the finished tree has to be copied
    across a filesystem boundary — twice the peak space, and a half-written
    destination when the disk fills. Staging next to the target makes the final
    move a rename.
    """
    base.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(dir=base, prefix=".staging-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


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

# ------------------------------------------------------------------------- state
# --------------------------------------------------------------------------- state

EMPTY_STATE = {
    "apt": {"packages": [], "links": {}, "repos": [], "repo_files": {}},
    # System-wide desktop software, reconciled only by a --workstation run.
    # A separate bucket so an ordinary run, which never declares any of it,
    # does not read the emptiness as "remove all of this".
    "workstation": {
        "repos": [],
        "repo_files": {},
        "packages": [],
        "links": {},
        "snap": [],
        "deb": {},
        "script": {},
    },
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

    for key, default in EMPTY_STATE["apt"].items():
        state["apt"].setdefault(key, json.loads(json.dumps(default)))

    return state


def save_state(state: dict, *, dry_run: bool) -> None:
    if dry_run:
        return
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")

# -------------------------------------------------------------------- root writes


def install_as_root(content: bytes, destination: Path, *, verbose: bool) -> None:
    with staging_area(STATE_PATH.parent) as staging:
        source = staging / destination.name
        source.write_bytes(content)
        run(
            sudo(
                # -D creates /etc/apt/keyrings on hosts old enough not to have it.
                ["install", "-D", "-o", "root", "-g", "root", "-m", "0644"]
                + [str(source), str(destination)]
            ),
            dry_run=False,
            verbose=verbose,
        )


def read_root_file(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""
