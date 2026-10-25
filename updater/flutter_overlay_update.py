"""Keeps the Flutter releases in this repository in step with Flutter's release channels.

Flutter publishes the current release of each channel in its release manifest. For every channel
whose current release is not yet in `versions/`, `update` runs nixpkgs' own Flutter update script
(pkgs/development/compilers/flutter/update/update.py) against the pinned nixpkgs and stores the
`data.json` it writes as `versions/<version>/data.json`.

Commands:
  plan                 Print a JSON list of {channel, version} releases that are missing.
  update               Add the current release of one channel, seed its patch series, prune.
  classify-build-log   Print the patches a failed build could not apply (exit 1 if none).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

MANIFEST_URL = "https://storage.googleapis.com/flutter_infra_release/releases/releases_linux.json"

# Channels that publish release archives. `dev` was retired in 2022 and `master` publishes none.
CHANNELS = ("stable", "beta")

# Releases kept per channel, newest first. The channel's current release is always among them.
KEEP_PER_CHANNEL = 3

FLUTTER_DIR = "pkgs/development/compilers/flutter"


# Versions


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


def series_of(version: str) -> str:
    """The patch series a version belongs to: "3.50.0-0.2.pre" -> "3_50"."""
    parsed = Version.parse(version)
    return f"{parsed.major}_{parsed.minor}"


def series_key(series: str) -> tuple[int, int]:
    major, minor = series.split("_")
    return int(major), int(minor)


# Repository state


@dataclass(frozen=True)
class Repo:
    root: Path

    @property
    def versions(self) -> Path:
        return self.root / "versions"

    @property
    def patches(self) -> Path:
        return self.root / "patches"

    def releases(self) -> dict[str, str]:
        """Kept releases as {version: channel}."""
        releases = {}
        if not self.versions.is_dir():
            return releases
        for data in self.versions.glob("*/data.json"):
            releases[data.parent.name] = json.loads(data.read_text())["channel"]
        return releases

    def series(self) -> list[str]:
        if not self.patches.is_dir():
            return []
        return sorted((d.name for d in self.patches.iterdir() if d.is_dir()), key=series_key)


# Planning


def current_releases(manifest: dict) -> dict[str, str]:
    """The current version of each channel in a Flutter release manifest."""
    by_hash = {}
    for release in manifest["releases"]:
        by_hash.setdefault((release["channel"], release["hash"]), release["version"])
    return {
        channel: by_hash[(channel, manifest["current_release"][channel])] for channel in CHANNELS
    }


def plan(current: dict[str, str], kept: dict[str, str], pending: set[str]) -> list[dict]:
    """Releases to add: each channel's current version unless it is kept or a branch is pending."""
    return [
        {"channel": channel, "version": version}
        for channel, version in current.items()
        if version not in kept and branch_name(channel, version) not in pending
    ]


def branch_name(channel: str, version: str) -> str:
    return f"update/{channel}-{version}"


def fetch_manifest() -> dict:
    with urllib.request.urlopen(MANIFEST_URL, timeout=60) as response:
        return json.load(response)


def remote_branches(repo: Path, remote: str) -> set[str]:
    output = run(["git", "ls-remote", "--heads", remote], cwd=repo, capture=True)
    prefix = "refs/heads/"
    return {
        line.split()[1][len(prefix) :]
        for line in output.splitlines()
        if line.split()[1].startswith(prefix)
    }


# Adding a release


def seed_series(repo: Repo, series: str, nixpkgs_versions: Path) -> str | None:
    """Create patches/<series> from the closest patch set that exists.

    nixpkgs' own patches for the same series win. Otherwise the newest older series is used,
    preferring this repository's copy (it carries earlier rebases) over nixpkgs'. Returns where
    the patches came from, or None when the series already existed.
    """
    target = repo.patches / series
    if target.is_dir():
        return None
    candidates = [(series_key(s), 1, repo.patches / s, f"patches/{s}") for s in repo.series()]
    candidates += [
        (series_key(d.name), 0, d / "patches", f"nixpkgs {FLUTTER_DIR}/versions/{d.name}/patches")
        for d in nixpkgs_versions.iterdir()
        if (d / "patches").is_dir()
    ]
    same = [c for c in candidates if c[0] == series_key(series) and c[1] == 0]
    older = sorted(c for c in candidates if c[0] < series_key(series))
    chosen = same[0] if same else older[-1] if older else None
    if chosen is None:
        target.mkdir(parents=True)
        return "nothing"
    shutil.copytree(chosen[2], target)
    return chosen[3]


def releases_to_prune(releases: dict[str, str], keep: int) -> list[str]:
    """Versions beyond the newest `keep` of their channel."""
    pruned = []
    for channel in set(releases.values()):
        versions = sorted(
            (v for v, c in releases.items() if c == channel), key=Version.parse, reverse=True
        )
        pruned.extend(versions[keep:])
    return sorted(pruned, key=Version.parse)


def prune(repo: Repo, keep: int) -> list[str]:
    """Drop old releases and the patch series no kept release uses. Returns what was removed."""
    removed = []
    for version in releases_to_prune(repo.releases(), keep):
        shutil.rmtree(repo.versions / version)
        removed.append(f"versions/{version}")
    used = {series_of(v) for v in repo.releases()}
    for series in repo.series():
        if series not in used:
            shutil.rmtree(repo.patches / series)
            removed.append(f"patches/{series}")
    return removed


def nixpkgs_source(repo: Path) -> Path:
    """The store path of the nixpkgs revision flake.lock pins."""
    archive = json.loads(run(["nix", "flake", "archive", "--json"], cwd=repo, capture=True))
    return Path(archive["inputs"]["nixpkgs"]["path"])


def run_nixpkgs_update(nixpkgs: Path, channel: str, version: str, scratch: Path) -> Path:
    """Run nixpkgs' update script in a writable copy of `nixpkgs`; return the data.json it wrote.

    The script locates nixpkgs with `git rev-parse` and writes into that checkout, so it runs in a
    scratch copy that is a git repository.
    """
    checkout = scratch / "nixpkgs"
    run(["cp", "-R", str(nixpkgs), str(checkout)])
    run(["chmod", "-R", "u+w", str(checkout)])
    run(["git", "init", "--quiet"], cwd=checkout)
    run(
        [
            sys.executable,
            f"{FLUTTER_DIR}/update/update.py",
            "--channel",
            channel,
            "--version",
            version,
        ],
        cwd=checkout,
    )
    data = checkout / FLUTTER_DIR / "versions" / series_of(version) / "data.json"
    written = json.loads(data.read_text())
    if written.get("version") != version or not written.get("artifactHashes"):
        raise SystemExit(f"the nixpkgs update script did not finish {version}: {data}")
    return data


def update(repo: Repo, channel: str, version: str | None, keep: int) -> dict:
    if version is None:
        version = current_releases(fetch_manifest())[channel]
    Version.parse(version)
    with tempfile.TemporaryDirectory(prefix="flutter-overlay-") as scratch:
        data = run_nixpkgs_update(nixpkgs_source(repo.root), channel, version, Path(scratch))
        target = repo.versions / version
        target.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(data, target / "data.json")
        seeded = seed_series(repo, series_of(version), data.parent.parent)
    removed = prune(repo, keep)
    return {
        "channel": channel,
        "version": version,
        "series": series_of(version),
        "seededFrom": seeded,
        "removed": removed,
    }


# Build failures

PATCH_FAILURE = re.compile(
    r"^(?:applying patch (?P<applying>\S+)|"
    r"(?P<failed>Hunk #\d+ FAILED.*|can't find file to patch.*|"
    r"Reversed \(or previously applied\) patch detected.*))$",
    re.MULTILINE,
)


STORE_HASH = re.compile(r"^[0-9a-z]{32}-")


def failed_patches(log: str) -> list[str]:
    """Patches a build log shows failing to apply, in order, without duplicates."""
    current = None
    failed: list[str] = []
    for match in PATCH_FAILURE.finditer(log):
        if match["applying"]:
            current = STORE_HASH.sub("", Path(match["applying"]).name)
        elif current is not None and current not in failed:
            failed.append(current)
        elif current is None and "unknown patch" not in failed:
            failed.append("unknown patch")
    return failed


# Command line


def run(command: list[str], cwd: Path | None = None, capture: bool = False) -> str:
    result = subprocess.run(
        command, cwd=cwd, check=True, text=True, stdout=subprocess.PIPE if capture else None
    )
    return result.stdout if capture else ""


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

    classify_parser = commands.add_parser("classify-build-log")
    classify_parser.add_argument("log", type=Path)

    args = parser.parse_args(argv)
    repo = Repo(args.repo.resolve())

    if args.command == "plan":
        pending = remote_branches(repo.root, args.skip_branches_on) if args.skip_branches_on else set()
        print(json.dumps(plan(current_releases(fetch_manifest()), repo.releases(), pending)))
        return 0
    if args.command == "update":
        print(json.dumps(update(repo, args.channel, args.version, args.keep), indent=2))
        return 0
    if args.command == "classify-build-log":
        failed = failed_patches(args.log.read_text(errors="replace"))
        print("\n".join(failed))
        return 0 if failed else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
