import json
import tempfile
import unittest
from pathlib import Path

from flutter_overlay_update import (
    Repo,
    Version,
    add_release,
    branch_name,
    current_releases,
    plan,
    release_of,
    releases_to_prune,
)

BASE_URL = "https://storage.googleapis.com/flutter_infra_release/releases"


def entry(channel: str, version: str, archive: str, arch: str, sha: str, hash_: str) -> dict:
    return {
        "hash": hash_,
        "channel": channel,
        "version": version,
        "archive": archive,
        "dart_sdk_arch": arch,
        "sha256": sha,
    }


def manifests() -> dict[str, dict]:
    """Manifests shaped like Flutter's releases_<os>.json."""
    return {
        "macos": {
            "base_url": BASE_URL,
            "current_release": {"stable": "s1", "beta": "b1"},
            "releases": [
                entry("stable", "3.50.0", "stable/macos/flutter_macos_arm64_3.50.0-stable.zip", "arm64", "a" * 64, "s1"),
                entry("stable", "3.50.0", "stable/macos/flutter_macos_3.50.0-stable.zip", "x64", "b" * 64, "s1"),
                entry("beta", "3.51.0-0.1.pre", "beta/macos/flutter_macos_arm64_3.51.0-0.1.pre-beta.zip", "arm64", "c" * 64, "b1"),
            ],
        },
        "linux": {
            "base_url": BASE_URL,
            "current_release": {"stable": "s1", "beta": "b1"},
            "releases": [
                entry("stable", "3.50.0", "stable/linux/flutter_linux_3.50.0-stable.tar.xz", "x64", "d" * 64, "s1"),
            ],
        },
    }


def write_release(repo: Path, version: str, channel: str) -> None:
    repo.joinpath("versions").mkdir(parents=True, exist_ok=True)
    (repo / "versions" / f"{version}.json").write_text(json.dumps({"version": version, "channel": channel}))


class VersionTest(unittest.TestCase):
    def test_orders_prereleases_before_their_release_and_numerically(self):
        versions = ["3.50.0", "3.50.0-0.10.pre", "3.47.8", "3.50.0-0.2.pre", "3.47.10"]
        self.assertEqual(
            sorted(versions, key=Version.parse),
            ["3.47.8", "3.47.10", "3.50.0-0.2.pre", "3.50.0-0.10.pre", "3.50.0"],
        )

    def test_rejects_non_versions(self):
        with self.assertRaises(ValueError):
            Version.parse("v3.50")


class ReleaseOfTest(unittest.TestCase):
    def test_collects_every_system_archive_with_its_url_and_hash(self):
        self.assertEqual(
            release_of(manifests(), "stable", "3.50.0"),
            {
                "version": "3.50.0",
                "channel": "stable",
                "archives": {
                    "aarch64-darwin": {
                        "url": f"{BASE_URL}/stable/macos/flutter_macos_arm64_3.50.0-stable.zip",
                        "sha256": "a" * 64,
                    },
                    "x86_64-darwin": {
                        "url": f"{BASE_URL}/stable/macos/flutter_macos_3.50.0-stable.zip",
                        "sha256": "b" * 64,
                    },
                    "x86_64-linux": {
                        "url": f"{BASE_URL}/stable/linux/flutter_linux_3.50.0-stable.tar.xz",
                        "sha256": "d" * 64,
                    },
                },
            },
        )

    def test_keeps_only_the_systems_that_have_an_archive(self):
        release = release_of(manifests(), "beta", "3.51.0-0.1.pre")
        self.assertEqual(list(release["archives"]), ["aarch64-darwin"])

    def test_rejects_a_release_the_manifests_do_not_list(self):
        with self.assertRaises(SystemExit):
            release_of(manifests(), "stable", "9.9.9")


class PlanTest(unittest.TestCase):
    def test_reads_the_current_release_of_each_channel(self):
        self.assertEqual(
            current_releases(manifests()["macos"]),
            {"stable": "3.50.0", "beta": "3.51.0-0.1.pre"},
        )

    def test_skips_kept_releases_and_pending_branches(self):
        current = {"stable": "3.50.0", "beta": "3.51.0-0.1.pre"}
        self.assertEqual(
            plan(current, {"3.50.0": "stable"}, set()),
            [{"channel": "beta", "version": "3.51.0-0.1.pre"}],
        )
        self.assertEqual(
            plan(current, {}, {branch_name("beta", "3.51.0-0.1.pre")}),
            [{"channel": "stable", "version": "3.50.0"}],
        )


class AddReleaseTest(unittest.TestCase):
    def test_writes_the_release_file_and_prunes_old_releases_of_its_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for version in ["3.47.8", "3.47.9", "3.47.10"]:
                write_release(root, version, "stable")
            write_release(root, "3.50.0-0.3.pre", "beta")

            summary = add_release(Repo(root), manifests(), "stable", "3.50.0", keep=3)

            self.assertEqual(
                summary,
                {"channel": "stable", "version": "3.50.0", "removed": ["versions/3.47.8.json"]},
            )
            self.assertEqual(
                Repo(root).releases(),
                {"3.47.9": "stable", "3.47.10": "stable", "3.50.0": "stable", "3.50.0-0.3.pre": "beta"},
            )
            written = json.loads((root / "versions" / "3.50.0.json").read_text())
            self.assertEqual(written, release_of(manifests(), "stable", "3.50.0"))

    def test_prunes_per_channel(self):
        releases = {"3.47.9": "stable", "3.50.0": "stable", "3.50.0-0.1.pre": "beta", "3.50.0-0.2.pre": "beta"}
        self.assertEqual(releases_to_prune(releases, keep=1), ["3.47.9", "3.50.0-0.1.pre"])


if __name__ == "__main__":
    unittest.main()
