#!/usr/bin/env python3
"""Compare latest upstream version against versions.json.

Writes to $GITHUB_OUTPUT when a new version is available:
    new_version=<semver>
    package=<name>
    registry=<pypi|npm>

Exit 0 always (version check failure should warn, not fail the pipeline).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx


def latest_pypi_version(package: str) -> str:
    resp = httpx.get(f"https://pypi.org/pypi/{package}/json", timeout=15)
    resp.raise_for_status()
    return str(resp.json()["info"]["version"])


def latest_npm_version(package: str) -> str:
    resp = httpx.get(f"https://registry.npmjs.org/{package}/latest", timeout=15)
    resp.raise_for_status()
    return str(resp.json()["version"])


def load_versions(path: Path) -> dict[str, str]:
    try:
        return dict(json.loads(path.read_text()))
    except FileNotFoundError:
        return {}


def write_github_output(key: str, value: str) -> None:
    output_file = os.environ.get("GITHUB_OUTPUT", "")
    if output_file:
        with open(output_file, "a") as f:
            f.write(f"{key}={value}\n")
    else:
        print(f"::set-output name={key}::{value}")  # fallback for older runners


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True)
    parser.add_argument("--registry", required=True, choices=["pypi", "npm"])
    parser.add_argument("--versions-file", default="versions.json")
    args = parser.parse_args()

    try:
        upstream = (
            latest_pypi_version(args.package)
            if args.registry == "pypi"
            else latest_npm_version(args.package)
        )
    except httpx.HTTPError as exc:
        print(f"Warning: registry fetch failed: {exc}", file=sys.stderr)
        return

    known = load_versions(Path(args.versions_file))
    key = f"{args.registry}/{args.package}"

    if known.get(key) == upstream:
        print(f"Already at {upstream}, skipping.", file=sys.stderr)
        return

    print(f"New version detected: {upstream}", file=sys.stderr)
    write_github_output("new_version", upstream)
    write_github_output("package", args.package)
    write_github_output("registry", args.registry)


if __name__ == "__main__":
    main()
