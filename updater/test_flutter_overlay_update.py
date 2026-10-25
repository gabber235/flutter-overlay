import json
import tempfile
import unittest
from pathlib import Path

from flutter_overlay_update import (
    Repo,
    Version,
    branch_name,
    current_releases,
    failed_patches,
    plan,
    prune,
    releases_to_prune,
    seed_series,
    series_of,
)


def write_release(repo: Path, version: str, channel: str) -> None:
    target = repo / "versions" / version
    target.mkdir(parents=True)
    (target / "data.json").write_text(json.dumps({"version": version, "channel": channel}))


def write_patch(directory: Path, name: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(f"{name}\n")


class VersionTest(unittest.TestCase):
    def test_orders_prereleases_before_their_release_and_numerically(self):
        versions = ["3.50.0", "3.50.0-0.10.pre", "3.47.8", "3.50.0-0.2.pre", "3.47.10"]
        self.assertEqual(
            sorted(versions, key=Version.parse),
            ["3.47.8", "3.47.10", "3.50.0-0.2.pre", "3.50.0-0.10.pre", "3.50.0"],
        )

    def test_rejects_non_versions(self):
        with self.assertRaises(ValueError):
            Version.parse("main")

    def test_series_is_major_and_minor(self):
        self.assertEqual(series_of("3.50.0-0.2.pre"), "3_50")
        self.assertEqual(series_of("3.47.8"), "3_47")


class PlanTest(unittest.TestCase):
    manifest = {
        "current_release": {"stable": "s2", "beta": "b1", "dev": "d1"},
        "releases": [
            {"hash": "b1", "channel": "beta", "version": "3.50.0-0.2.pre"},
            {"hash": "s2", "channel": "stable", "version": "3.47.8"},
            {"hash": "s1", "channel": "stable", "version": "3.47.7"},
            {"hash": "d1", "channel": "dev", "version": "2.13.0-0.2.pre"},
        ],
    }

    def test_reads_current_release_of_published_channels_only(self):
        self.assertEqual(
            current_releases(self.manifest), {"stable": "3.47.8", "beta": "3.50.0-0.2.pre"}
        )

    def test_plans_channels_whose_current_release_is_missing(self):
        current = current_releases(self.manifest)
        self.assertEqual(
            plan(current, {"3.47.8": "stable"}, set()),
            [{"channel": "beta", "version": "3.50.0-0.2.pre"}],
        )

    def test_skips_releases_with_a_pending_update_branch(self):
        current = current_releases(self.manifest)
        pending = {branch_name("beta", "3.50.0-0.2.pre"), branch_name("stable", "3.47.8")}
        self.assertEqual(plan(current, {}, pending), [])


class SeedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.nixpkgs_versions = self.root / "nixpkgs-versions"
        write_patch(self.nixpkgs_versions / "3_44" / "patches", "old.patch")
        write_patch(self.nixpkgs_versions / "3_47" / "patches", "nixpkgs.patch")
        self.repo = Repo(self.root / "repo")

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_series_starts_from_closest_older_series_in_repo(self):
        write_patch(self.repo.patches / "3_44", "a.patch")
        write_patch(self.repo.patches / "3_47", "b.patch")
        self.assertEqual(seed_series(self.repo, "3_50", self.nixpkgs_versions), "patches/3_47")
        self.assertEqual(
            sorted(p.name for p in (self.repo.patches / "3_50").iterdir()), ["b.patch"]
        )

    def test_first_series_starts_from_nixpkgs(self):
        self.assertIn("versions/3_47", seed_series(self.repo, "3_50", self.nixpkgs_versions))
        self.assertTrue((self.repo.patches / "3_50" / "nixpkgs.patch").is_file())

    def test_nixpkgs_patches_for_the_same_series_win(self):
        write_patch(self.repo.patches / "3_44", "a.patch")
        self.assertIn("versions/3_47", seed_series(self.repo, "3_47", self.nixpkgs_versions))
        self.assertTrue((self.repo.patches / "3_47" / "nixpkgs.patch").is_file())

    def test_existing_series_is_left_alone(self):
        write_patch(self.repo.patches / "3_50", "rebased.patch")
        self.assertIsNone(seed_series(self.repo, "3_50", self.nixpkgs_versions))
        self.assertEqual(
            [p.name for p in (self.repo.patches / "3_50").iterdir()], ["rebased.patch"]
        )


class PruneTest(unittest.TestCase):
    def test_keeps_newest_per_channel(self):
        releases = {
            "3.47.6": "stable",
            "3.47.7": "stable",
            "3.47.8": "stable",
            "3.49.0-0.2.pre": "beta",
            "3.50.0-0.2.pre": "beta",
        }
        self.assertEqual(releases_to_prune(releases, 2), ["3.47.6"])

    def test_removes_releases_and_unused_series(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repo(Path(tmp))
            write_release(repo.root, "3.49.0-0.2.pre", "beta")
            write_release(repo.root, "3.50.0-0.2.pre", "beta")
            write_release(repo.root, "3.47.8", "stable")
            for series in ("3_44", "3_47", "3_49", "3_50"):
                write_patch(repo.patches / series, "a.patch")
            removed = prune(repo, 1)
            self.assertEqual(removed, ["versions/3.49.0-0.2.pre", "patches/3_44", "patches/3_49"])
            self.assertEqual(repo.series(), ["3_47", "3_50"])


class BuildLogTest(unittest.TestCase):
    def test_names_patches_that_fail_to_apply(self):
        log = "\n".join(
            [
                "applying patch /nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-copy-without-perms.patch",
                "patching file packages/flutter_tools/lib/src/base/os.dart",
                "applying patch /nix/store/dddddddddddddddddddddddddddddddd-disable-auto-update.patch",
                "patching file packages/flutter_tools/lib/src/runner/flutter_command.dart",
                "Hunk #1 FAILED at 1693.",
                "Hunk #2 FAILED at 1700.",
                "applying patch /nix/store/gggggggggggggggggggggggggggggggg-override-host-platform-base.patch",
                "can't find file to patch at input line 3",
            ]
        )
        self.assertEqual(
            failed_patches(log),
            ["disable-auto-update.patch", "override-host-platform-base.patch"],
        )

    def test_other_failures_name_no_patch(self):
        self.assertEqual(failed_patches("error: hash mismatch in fixed-output derivation"), [])


if __name__ == "__main__":
    unittest.main()
