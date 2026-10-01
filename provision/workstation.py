#!/usr/bin/env python3
"""Reconcile the workstation section of config.yaml.

A front door, not a tool: everything it installs is declared in config.yaml and
applied by provision.py. It exists so the system-wide desktop software has a
command of its own — `./apply.sh` has to stay safe to run on any host, and that
means never installing a snap or running a vendor's shell script.

Every flag provision.py takes works here too; --workstation is added for you.
"""

from __future__ import annotations

import sys

import provision


def main() -> None:
    sys.argv = [sys.argv[0], "--workstation", *sys.argv[1:]]
    provision.main()


if __name__ == "__main__":
    main()
