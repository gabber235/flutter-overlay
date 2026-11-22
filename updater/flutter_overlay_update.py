"""Keeps the Flutter releases in this repository in step with Flutter's release channels.

Flutter publishes every release archive, with its sha256, in one manifest per operating system.
`update` copies one release's archives from those manifests into
`versions/<channel>/<version>.json` and records each channel's newest kept release in
`versions/channels.json`; the Nix packages fetch the archive for their system from there.
Nothing is built.

Flutter publishes no archive for aarch64 Linux, so for that system `update` also records the
files Flutter's tool would download there itself (see `linux_arm64`), each with the sha256 of
the file as published, and the pub packages the tool is built from.

Commands:
  plan     Print a JSON list of {channel, version} releases that are missing.
  update   Add one release of a channel (its current one by default) and prune old releases.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tarfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

MANIFEST_URL = "https://storage.googleapis.com/flutter_infra_release/releases/releases_{os}.json"

# Manifests to read, and the Nix system each (os, dart_sdk_arch) archive runs on. Every release
# needs all of them. nixpkgs dropped x86_64-darwin.
SYSTEMS = {
    ("macos", "arm64"): "aarch64-darwin",
    ("linux", "x64"): "x86_64-linux",
}
LINUX_ARM64 = "aarch64-linux"
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

    @property
    def heads_file(self) -> Path:
        return self.versions / "channels.json"

    def releases(self) -> set[tuple[str, str]]:
        """Kept releases as (channel, version)."""
        return {(path.parent.name, path.stem) for path in self.versions.glob("*/*.json")}

    def heads(self) -> dict[str, str]:
        """The newest kept release of each channel, as recorded in channels.json."""
        return json.loads(self.heads_file.read_text()) if self.heads_file.exists() else {}


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
    """One release with its framework revision and the archive URL and sha256 for every system
    Flutter publishes it for."""
    archives = {}
    revisions = set()
    for os, manifest in manifests.items():
        for entry in manifest["releases"]:
            system = SYSTEMS.get((os, entry.get("dart_sdk_arch", "x64")))
            if entry["channel"] == channel and entry["version"] == version and system:
                revisions.add(entry["hash"])
                archives.setdefault(
                    system,
                    {"url": f"{manifest['base_url']}/{entry['archive']}", "sha256": entry["sha256"]},
                )
    if not archives:
        raise SystemExit(f"Flutter's manifests list no {channel} release {version}")
    missing = sorted(set(SYSTEMS.values()) - set(archives))
    if missing:
        raise SystemExit(f"{channel} {version} has no archive for {', '.join(missing)} yet")
    if len(revisions) != 1:
        raise SystemExit(f"{channel} {version} has archives of different revisions: {sorted(revisions)}")
    order = list(SYSTEMS.values())
    return {
        "version": version,
        "channel": channel,
        "revision": revisions.pop(),
        "archives": dict(sorted(archives.items(), key=lambda item: order.index(item[0]))),
    }


# aarch64 Linux
#
# Flutter supports ARM Linux hosts from a git checkout of the release. The x64 Linux archive holds
# that git checkout (its .git and framework, the same on every platform) next to bin/cache, the
# files Flutter's tool downloaded for x64. For aarch64 Linux the package takes the checkout from
# the x64 archive and fills bin/cache from the files below, which are what the tool downloads on
# an ARM Linux host: the same URLs, unpacked into the same directories, with the same stamps, so
# the tool finds everything up to date and downloads nothing. Each file is pinned by the sha256 of
# the file as Google publishes it.

STORAGE = "https://storage.googleapis.com"
PUB_ARCHIVE = "https://pub.dev/api/archives/{name}-{version}.tar.gz"

# Files read from the x64 archive: the engine and artifact versions the tool reads, and the lock
# of the tool's own packages, which `bin/internal/shared.sh` resolved when Google built the
# archive (it is generated, so the git tag does not have it).
ENGINE_VERSION = "flutter/bin/internal/engine.version"
MATERIAL_FONTS_VERSION = "flutter/bin/internal/material_fonts.version"
GRADLE_WRAPPER_VERSION = "flutter/bin/internal/gradle_wrapper.version"
TOOL_LOCK = "flutter/packages/flutter_tools/pubspec.lock"
FROM_ARCHIVE = (ENGINE_VERSION, MATERIAL_FONTS_VERSION, GRADLE_WRAPPER_VERSION, TOOL_LOCK)


class Fetch(Protocol):
    def sha256(self, url: str) -> str:
        """The sha256 of the file at `url`."""

    def archive_files(self, url: str, sha256: str, names: tuple[str, ...]) -> dict[str, str]:
        """The text of the named members of the tar.xz at `url`, which must hash to `sha256`."""


def linux_arm64_cache(revision: str, engine: str, material_fonts: str, gradle_wrapper: str) -> dict:
    """bin/cache for aarch64 Linux as Flutter's tool lays it out, as files to fetch and stamps.

    Each file is unpacked into `into` (relative to bin/cache): `zip` and `tgz` unpack there, `file`
    is copied there. The rest mirrors what the tool does after its own download: `executable`
    marks engine binary directories, where the tool makes every user executable file and
    flutter_tester readable and executable by all; `remove` lists files the tool deletes.
    Stamps are bin/cache/<name>.stamp with the version the tool compares them with.
    """
    engine_url = f"{STORAGE}/flutter_infra_release/flutter/{engine}"

    def file(url: str, into: str, unpack: str = "zip", **extra) -> dict:
        return {"url": url, "into": into, "unpack": unpack, **extra}

    files = [
        # bin/internal/update_dart_sdk.sh
        file(f"{engine_url}/dart-sdk-linux-arm64.zip", "."),
        # flutter_cache.dart, in the order the tool updates them: FlutterEngineStamp,
        # MaterialFonts, GradleWrapper, FlutterWebSdk, FlutterSdk, LinuxEngineArtifacts and
        # FontSubsetArtifacts.
        file(f"{engine_url}/engine_stamp.json", ".", "file"),
        file(f"{STORAGE}/{material_fonts}", "artifacts/material_fonts"),
        file(
            f"{STORAGE}/{gradle_wrapper}",
            "artifacts/gradle_wrapper",
            "tgz",
            remove=["gradle/wrapper/gradle-wrapper.properties", "NOTICE"],
        ),
        file(f"{engine_url}/flutter-web-sdk.zip", "flutter_web_sdk"),
        file(f"{engine_url}/sky_engine.zip", "pkg"),
        file(f"{engine_url}/flutter_gpu.zip", "pkg"),
        file(f"{engine_url}/flutter_patched_sdk.zip", "artifacts/engine/common", executable=True),
        file(f"{engine_url}/flutter_patched_sdk_product.zip", "artifacts/engine/common", executable=True),
        file(f"{engine_url}/linux-arm64/artifacts.zip", "artifacts/engine/linux-arm64", executable=True),
        *(
            file(
                f"{engine_url}/linux-arm64-{mode}/linux-arm64-flutter-gtk.zip",
                f"artifacts/engine/linux-arm64{'' if mode == 'debug' else '-' + mode}",
                executable=True,
            )
            for mode in ("debug", "profile", "release")
        ),
        file(f"{engine_url}/linux-arm64/font-subset.zip", "artifacts/engine/linux-arm64", executable=True),
    ]
    stamps = {
        "engine-dart-sdk": engine,
        "engine_stamp": engine,
        "material_fonts": material_fonts,
        "gradle_wrapper": gradle_wrapper,
        "flutter_web_sdk": engine,
        "flutter_sdk": engine,
        "linux-sdk": engine,
        "font-subset": engine,
        # bin/internal/shared.sh rebuilds the tool unless this matches `<git HEAD>:<tool args>`.
        "flutter_tools": f"{revision}:",
    }
    return {"files": files, "stamps": stamps}


def hosted_packages(lock: str) -> list[dict]:
    """Every package of a pubspec.lock, which must all come from pub.dev, with its sha256."""
    packages: list[dict] = []
    current: dict | None = None
    for line in lock.splitlines():
        if match := re.fullmatch(r"  ([A-Za-z0-9_]+):", line):
            current = {"name": match[1]}
            packages.append(current)
        elif current is not None and (match := re.fullmatch(r" {4,6}(\w+): \"?([^\"]*)\"?", line)):
            key, value = match.groups()
            if key in ("sha256", "version", "source", "url"):
                current[key] = value
        elif line and not line.startswith(" "):
            current = None
    for package in packages:
        if package.get("source") != "hosted" or package.get("url") != "https://pub.dev":
            raise SystemExit(f"the tool's package {package['name']} does not come from pub.dev")
    return [{key: p[key] for key in ("name", "version", "sha256")} for p in packages]


def linux_arm64(release: dict, fetch: Fetch) -> dict:
    """The aarch64 Linux entry of a release: the x64 Linux archive for its git checkout, the
    files of bin/cache with their sha256, and the packages to build the tool from."""
    x64 = release["archives"]["x86_64-linux"]
    texts = fetch.archive_files(x64["url"], x64["sha256"], FROM_ARCHIVE)
    cache = linux_arm64_cache(
        release["revision"],
        texts[ENGINE_VERSION].strip(),
        texts[MATERIAL_FONTS_VERSION].strip(),
        texts[GRADLE_WRAPPER_VERSION].strip(),
    )
    for file in cache["files"]:
        file["sha256"] = fetch.sha256(file["url"])
    return {**x64, "cache": cache, "toolPackages": hosted_packages(texts[TOOL_LOCK])}


class _Hashing:
    """A readable stream that hashes what is read from it."""

    def __init__(self, stream: BinaryIO):
        self.stream = stream
        self.hash = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        data = self.stream.read(size)
        self.hash.update(data)
        return data


class Network:
    def sha256(self, url: str) -> str:
        with urllib.request.urlopen(url, timeout=120) as response:
            stream = _Hashing(response)
            while stream.read(1 << 20):
                pass
        return stream.hash.hexdigest()

    def archive_files(self, url: str, sha256: str, names: tuple[str, ...]) -> dict[str, str]:
        found = {}
        with urllib.request.urlopen(url, timeout=120) as response:
            stream = _Hashing(response)
            with tarfile.open(fileobj=stream, mode="r|xz") as archive:
                for member in archive:
                    if member.name in names:
                        found[member.name] = archive.extractfile(member).read().decode()
            while stream.read(1 << 20):
                pass
        if stream.hash.hexdigest() != sha256:
            raise SystemExit(f"{url} does not match the sha256 Flutter's manifest gives")
        missing = sorted(set(names) - set(found))
        if missing:
            raise SystemExit(f"{url} lacks {', '.join(missing)}")
        return found


# Planning and adding releases


def branch_name(channel: str, version: str) -> str:
    return f"update/{channel}-{version}"


def plan(current: dict[str, str], kept: set[tuple[str, str]], pending: set[str]) -> list[dict]:
    """Releases to add: each channel's current version unless it is kept or a branch is pending."""
    return [
        {"channel": channel, "version": version}
        for channel, version in current.items()
        if (channel, version) not in kept and branch_name(channel, version) not in pending
    ]


def newest_first(releases: set[tuple[str, str]], channel: str) -> list[str]:
    return sorted((v for c, v in releases if c == channel), key=Version.parse, reverse=True)


def releases_to_prune(releases: set[tuple[str, str]], keep: int) -> list[tuple[str, str]]:
    """Releases beyond the newest `keep` of their channel, in channel order."""
    return [
        (channel, version)
        for channel in CHANNELS
        for version in sorted(newest_first(releases, channel)[keep:], key=Version.parse)
    ]


def add_release(
    repo: Repo, manifests: dict[str, dict], channel: str, version: str, keep: int, fetch: Fetch
) -> dict:
    release = release_of(manifests, channel, version)
    release["archives"][LINUX_ARM64] = linux_arm64(release, fetch)
    (repo.versions / channel).mkdir(parents=True, exist_ok=True)
    (repo.versions / channel / f"{version}.json").write_text(json.dumps(release, indent=2) + "\n")
    removed = []
    for old_channel, old in releases_to_prune(repo.releases(), keep):
        (repo.versions / old_channel / f"{old}.json").unlink()
        removed.append(f"versions/{old_channel}/{old}.json")
    # Nix picks each channel's package from here, so it never has to order Flutter versions.
    kept = repo.releases()
    heads = {c: newest_first(kept, c)[0] for c in CHANNELS if newest_first(kept, c)}
    repo.heads_file.write_text(json.dumps(heads, indent=2) + "\n")
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
    print(json.dumps(add_release(repo, manifests, args.channel, version, args.keep, Network()), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
