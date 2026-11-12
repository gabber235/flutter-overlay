"""Keeps the Flutter releases in this repository in step with Flutter's release channels.

Flutter publishes every release archive, with its sha256, in one manifest per operating system.
`update` copies one release's archives from those manifests into `versions/<version>.json`; the
Nix package fetches the archive for its system from there. Nothing is built.

Commands:
  plan     Print a JSON list of {channel, version} releases that are missing.
  update   Add one release of a channel (its current one by default) and prune old releases.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

MANIFEST_URL = "https://storage.googleapis.com/flutter_infra_release/releases/releases_{os}.json"

# Manifests to read, and the Nix system each (os, dart_sdk_arch) archive runs on. Flutter
# publishes no archive for aarch64 Linux.
SYSTEMS = {
    ("macos", "arm64"): "aarch64-darwin",
    ("macos", "x64"): "x86_64-darwin",
    ("linux", "x64"): "x86_64-linux",
}
OPERATING_SYSTEMS = sorted({os for os, _ in SYSTEMS})

# Channels that publish release archives. `dev` was retired in 2022 and `master` publishes none.
CHANNELS = ("stable", "beta")

# Releases kept per channel, newest first. The channel's current release is always among them.
KEEP_PER_CHANNEL = 3


@dataclass(frozen=True, order=True)
class Version:
    """A Flutter version such as 3.47.8 or 3.50.0-0.2.pre, ordered like Flutter orders them."""

    major: int
    minor: int
    patch: int
    # A release sorts after every prerelease of the same major.minor.patch.
    is_release: bool
    prerelease: tuple[int, ...]

    @staticmethod
    def parse(text: str) -> Version:
        match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-([\d.]+)\.pre)?", text)
        if not match:
            raise ValueError(f"not a Flutter version: {text}")
        major, minor, patch, pre = match.groups()
        return Version(
            int(major),
            int(minor),
            int(patch),
            pre is None,
            tuple(int(part) for part in pre.split(".")) if pre else (),
        )


@dataclass(frozen=True)
class Repo:
    root: Path

    @property
    def versions(self) -> Path:
        return self.root / "versions"

    def releases(self) -> dict[str, str]:
        """Kept releases as {version: channel}."""
        return {
            path.stem: json.loads(path.read_text())["channel"]
            for path in sorted(self.versions.glob("*.json"))
        }


# Reading the manifests


def fetch_manifests() -> dict[str, dict]:
    manifests = {}
    for os in OPERATING_SYSTEMS:
        with urllib.request.urlopen(MANIFEST_URL.format(os=os), timeout=60) as response:
            manifests[os] = json.load(response)
    return manifests


def current_releases(manifest: dict) -> dict[str, str]:
    """The current version of each channel in a Flutter release manifest."""
    by_hash = {(r["channel"], r["hash"]): r["version"] for r in reversed(manifest["releases"])}
    return {
        channel: by_hash[(channel, manifest["current_release"][channel])] for channel in CHANNELS
    }


def release_of(manifests: dict[str, dict], channel: str, version: str) -> dict:
    """One release with the archive URL and sha256 for every system Flutter publishes it for."""
    archives = {}
    for os, manifest in manifests.items():
        for entry in manifest["releases"]:
            system = SYSTEMS.get((os, entry.get("dart_sdk_arch", "x64")))
            if entry["channel"] == channel and entry["version"] == version and system:
                archives.setdefault(
                    system,
                    {"url": f"{manifest['base_url']}/{entry['archive']}", "sha256": entry["sha256"]},
                )
    if not archives:
        raise SystemExit(f"Flutter's manifests list no {channel} release {version}")
    order = list(SYSTEMS.values())
    return {
        "version": version,
        "channel": channel,
        "archives": dict(sorted(archives.items(), key=lambda item: order.index(item[0]))),
    }


# Planning and adding releases


def branch_name(channel: str, version: str) -> str:
    return f"update/{channel}-{version}"


def plan(current: dict[str, str], kept: dict[str, str], pending: set[str]) -> list[dict]:
    """Releases to add: each channel's current version unless it is kept or a branch is pending."""
    return [
        {"channel": channel, "version": version}
        for channel, version in current.items()
        if version not in kept and branch_name(channel, version) not in pending
    ]


def releases_to_prune(releases: dict[str, str], keep: int) -> list[str]:
    """Versions beyond the newest `keep` of their channel."""
    pruned = []
    for channel in set(releases.values()):
        versions = sorted(
            (v for v, c in releases.items() if c == channel), key=Version.parse, reverse=True
        )
        pruned.extend(versions[keep:])
    return sorted(pruned, key=Version.parse)


def add_release(repo: Repo, manifests: dict[str, dict], channel: str, version: str, keep: int) -> dict:
    release = release_of(manifests, channel, version)
    repo.versions.mkdir(parents=True, exist_ok=True)
    (repo.versions / f"{version}.json").write_text(json.dumps(release, indent=2) + "\n")
    removed = []
    for old in releases_to_prune(repo.releases(), keep):
        (repo.versions / f"{old}.json").unlink()
        removed.append(f"versions/{old}.json")
    return {"channel": channel, "version": version, "removed": removed}


def remote_branches(repo: Path, remote: str) -> set[str]:
    output = subprocess.run(
        ["git", "ls-remote", "--heads", remote], cwd=repo, check=True, text=True, capture_output=True
    ).stdout
    prefix = "refs/heads/"
    return {
        ref[len(prefix) :] for ref in (line.split()[1] for line in output.splitlines()) if ref.startswith(prefix)
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="repository root")
    commands = parser.add_subparsers(dest="command", required=True)

    plan_parser = commands.add_parser("plan")
    plan_parser.add_argument(
        "--skip-branches-on",
        metavar="REMOTE",
        help="skip releases whose update branch already exists on this git remote",
    )

    update_parser = commands.add_parser("update")
    update_parser.add_argument("--channel", choices=CHANNELS, required=True)
    update_parser.add_argument("--version", help="defaults to the channel's current release")
    update_parser.add_argument("--keep", type=int, default=KEEP_PER_CHANNEL)

    args = parser.parse_args(argv)
    repo = Repo(args.repo.resolve())
    manifests = fetch_manifests()

    if args.command == "plan":
        pending = remote_branches(repo.root, args.skip_branches_on) if args.skip_branches_on else set()
        print(json.dumps(plan(current_releases(manifests["linux"]), repo.releases(), pending)))
        return 0
    version = args.version or current_releases(manifests["linux"])[args.channel]
    print(json.dumps(add_release(repo, manifests, args.channel, version, args.keep), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
