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
                entry("stable", "3.47.9", "stable/macos/flutter_macos_arm64_3.47.9-stable.zip", "arm64", "e" * 64, "s0"),
                entry("beta", "3.51.0-0.1.pre", "beta/macos/flutter_macos_arm64_3.51.0-0.1.pre-beta.zip", "arm64", "c" * 64, "b1"),
                entry("beta", "3.52.0", "beta/macos/flutter_macos_arm64_3.52.0-beta.zip", "arm64", "2" * 64, "b2"),
            ],
        },
        "linux": {
            "base_url": BASE_URL,
            "current_release": {"stable": "s1", "beta": "b1"},
            "releases": [
                entry("stable", "3.50.0", "stable/linux/flutter_linux_3.50.0-stable.tar.xz", "x64", "d" * 64, "s1"),
                entry("stable", "3.47.9", "stable/linux/flutter_linux_3.47.9-stable.tar.xz", "x64", "f" * 64, "s0"),
                entry("beta", "3.52.0", "beta/linux/flutter_linux_3.52.0-beta.tar.xz", "x64", "1" * 64, "b2"),
            ],
        },
    }


def write_release(repo: Path, version: str, channel: str) -> None:
    repo.joinpath("versions", channel).mkdir(parents=True, exist_ok=True)
    (repo / "versions" / channel / f"{version}.json").write_text(json.dumps({"version": version, "channel": channel}))


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
                    "x86_64-linux": {
                        "url": f"{BASE_URL}/stable/linux/flutter_linux_3.50.0-stable.tar.xz",
                        "sha256": "d" * 64,
                    },
                },
            },
        )

    def test_rejects_a_release_not_yet_published_for_every_system(self):
        # 3.51.0-0.1.pre has a macOS archive but no Linux one yet; adding it now would leave
        # Linux behind for good, so it waits for the next run.
        with self.assertRaises(SystemExit):
            release_of(manifests(), "beta", "3.51.0-0.1.pre")

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
            plan(current, {("stable", "3.50.0")}, set()),
            [{"channel": "beta", "version": "3.51.0-0.1.pre"}],
        )
        self.assertEqual(
            plan(current, set(), {branch_name("beta", "3.51.0-0.1.pre")}),
            [{"channel": "stable", "version": "3.50.0"}],
        )


class AddReleaseTest(unittest.TestCase):
    def test_writes_the_release_prunes_its_channel_and_records_each_channel_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for version in ["3.47.6", "3.47.7", "3.47.8"]:
                write_release(root, version, "stable")
            write_release(root, "3.50.0-0.3.pre", "beta")

            summary = add_release(Repo(root), manifests(), "stable", "3.50.0", keep=3)

            self.assertEqual(
                summary,
                {"channel": "stable", "version": "3.50.0", "removed": ["versions/stable/3.47.6.json"]},
            )
            self.assertEqual(
                Repo(root).releases(),
                {("stable", "3.47.7"), ("stable", "3.47.8"), ("stable", "3.50.0"), ("beta", "3.50.0-0.3.pre")},
            )
            written = json.loads((root / "versions" / "stable" / "3.50.0.json").read_text())
            self.assertEqual(written, release_of(manifests(), "stable", "3.50.0"))
            self.assertEqual(Repo(root).heads(), {"stable": "3.50.0", "beta": "3.50.0-0.3.pre"})

    def test_a_beta_release_outranks_its_prereleases_and_can_share_a_version_with_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_release(root, "3.52.0-0.3.pre", "beta")
            write_release(root, "3.52.0", "stable")

            add_release(Repo(root), manifests(), "beta", "3.52.0", keep=3)

            self.assertEqual(Repo(root).heads(), {"stable": "3.52.0", "beta": "3.52.0"})
            self.assertIn(("stable", "3.52.0"), Repo(root).releases())

    def test_an_older_release_never_replaces_the_channel_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            add_release(Repo(root), manifests(), "stable", "3.50.0", keep=3)
            add_release(Repo(root), manifests(), "stable", "3.47.9", keep=3)
            self.assertEqual(Repo(root).heads(), {"stable": "3.50.0"})

    def test_prunes_per_channel(self):
        releases = {("stable", "3.47.9"), ("stable", "3.50.0"), ("beta", "3.50.0-0.1.pre"), ("beta", "3.50.0-0.2.pre")}
        self.assertEqual(releases_to_prune(releases, keep=1), [("stable", "3.47.9"), ("beta", "3.50.0-0.1.pre")])


if __name__ == "__main__":
    unittest.main()
