# flutter-overlay

Flutter for Nix on the **stable** and **beta** channels, updated automatically.

nixpkgs ships Flutter stable releases a while after they come out and never ships beta. This
flake packages the current release of each channel within hours of its publication, built exactly
the way nixpkgs builds Flutter (its `mkFlutter` and `wrapFlutter` builders, its update script and
its patches), so the result behaves like `pkgs.flutter`.

## Usage

### Flake

```nix
{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flutter-overlay.url = "github:gabber235/flutter-overlay";
  };

  outputs = { nixpkgs, flutter-overlay, ... }:
    let
      system = "aarch64-darwin";
      pkgs = import nixpkgs {
        inherit system;
        overlays = [ flutter-overlay.overlays.default ];
      };
    in
    {
      devShells.${system}.default = pkgs.mkShell {
        packages = [ pkgs.flutterChannels.beta ];
      };
    };
}
```

Or skip the overlay and take a package directly:
`flutter-overlay.packages.${system}.beta`.

Try it without installing anything:

```sh
nix run github:gabber235/flutter-overlay#beta -- --version
```

### devenv

`devenv.yaml`:

```yaml
inputs:
  nixpkgs:
    url: github:NixOS/nixpkgs/nixpkgs-unstable
  flutter-overlay:
    url: github:gabber235/flutter-overlay
    inputs:
      nixpkgs:
        follows: nixpkgs
```

`devenv.nix`:

```nix
{ pkgs, inputs, ... }:
let
  flutter = inputs.flutter-overlay.packages.${pkgs.system}.beta;
in
{
  packages = [ flutter ];
  env.FLUTTER_ROOT = "${flutter.sdk}";
}
```

`FLUTTER_ROOT` tells tools started outside the `flutter` command, such as `dart run build_runner`
or an IDE, which Flutter SDK to use. `flutter.sdk` is the SDK root, the same store path as the
package itself. See [SDK layout](#sdk-layout) for why `dart` finds Flutter on its own.

Following your own nixpkgs keeps one nixpkgs in your closure but builds Flutter against it, so the
binary cache below only hits while your nixpkgs matches the one pinned here. Drop the `follows`
to always hit the cache.

## Outputs

Systems: `x86_64-linux`, `aarch64-linux`, `aarch64-darwin`.

| Output | Contents |
| --- | --- |
| `packages.<system>.stable` | Current stable release |
| `packages.<system>.beta` | Current beta release |
| `packages.<system>.default` | Same as `stable` |
| `legacyPackages.<system>.versions."<version>"` | Every kept release by exact version, for example `versions."3.47.8"` |
| `overlays.default` | Adds `pkgs.flutterChannels.{stable,beta,versions}`, built with the nixpkgs you apply it to |
| `checks.<system>.*` | Builds of `stable` and `beta` and the updater unit tests |
| `packages.<system>.updater` | The release updater (see below) |

`nix build '.#versions."3.47.8"'` works because Nix looks in `legacyPackages` too; the versions
live there because `packages` may only hold derivations, not nested sets. Each channel keeps its
newest three releases.

Every package is the wrapped Flutter SDK, so `flutter` and `dart` are on its `bin`, and it carries
the usual passthru (`buildFlutterApplication`, `dart`, `engineVersion`, and `sdk`, the SDK root to
use as `FLUTTER_ROOT`).

### SDK layout

The SDK is laid out like an upstream Flutter install: `bin/cache/dart-sdk` holds real copies of
the Dart executables and `bin/dart` points at them. In nixpkgs `bin/cache/dart-sdk` links to the
standalone Dart SDK, so a running `dart` resolves its own path outside Flutter, and tools that
locate Flutter from that path miss it. build_runner, for one, looks for dart:ui at
`bin/cache/pkg/sky_engine` beside the resolved Dart SDK; without it, generators such as freezed
write `InvalidType` for types like `Color` (see
[dart-lang/build#3596](https://github.com/dart-lang/build/issues/3596)). The CI smoke test
(`.github/scripts/smoke-test.sh`) generates a freezed union with a `Color` field on every channel
to keep this working.

`buildFlutterApplication` keeps building with the nixpkgs layout, as it does in nixpkgs.

## Channels

Flutter publishes release archives, listed in
[`releases_linux.json`](https://storage.googleapis.com/flutter_infra_release/releases/releases_linux.json)
and `releases_macos.json`, for two channels that are still alive:

| Channel | Packaged | Why |
| --- | --- | --- |
| `stable` | yes | |
| `beta` | yes | |
| `dev` | no | Retired by Flutter in 2022. The manifest still lists it, but its last release is from April 2022. |
| `main` (`master`) | no | Has no release archives or release tags, only a moving branch, so there is nothing reproducible to pin. |

### Known issue: `flutter build macos` on 3.47.x

On macOS 15.4 and later, `flutter build macos` with 3.47.x fails: `/usr/bin/rsync` is openrsync,
which ignores `--chmod`, so the framework copied out of the read only store stays read only and
`lipo` cannot thin it ([flutter/flutter#195257](https://github.com/flutter/flutter/issues/195257)).
Flutter 3.50 contains the fix, so this goes away once stable reaches 3.50; use `beta` until then.

## Binary cache

CI pushes every build to the public Cachix cache `gabber235`. Add it to `nix.conf`:

```conf
extra-substituters = https://gabber235.cachix.org
extra-trusted-public-keys = gabber235.cachix.org-1:sXlzXx2H2hsyx+SA4NkiXhAVD4q0IgnditjhCEsBgXQ=
```

or run `cachix use gabber235`. In devenv, add the same to `devenv.yaml`:

```yaml
nixConfig:
  extra-substituters:
    - https://gabber235.cachix.org
  extra-trusted-public-keys:
    - gabber235.cachix.org-1:sXlzXx2H2hsyx+SA4NkiXhAVD4q0IgnditjhCEsBgXQ=
```

## How it works

```
versions/<version>/data.json   what nixpkgs' update script generates for a release
patches/<major>_<minor>/       patches for one release series, such as patches/3_50
nix/default.nix                builds every release with nixpkgs' mkFlutter and wrapFlutter
updater/                       the updater (Python, standard library only) and its tests
```

A release's patch set is the nixpkgs global Flutter patches
(`pkgs/development/compilers/flutter/patches`) followed by `patches/<series>`. A series patch with
the same file name as a global patch replaces it. The channel packages are the newest kept release
whose `data.json` names that channel.

## Updates

The `Update` workflow runs every 12 hours and on demand:

1. `updater plan` reads the release manifest and lists each channel whose current release is not
   in `versions/` and has no open update branch.
2. For each, `updater update` first gives a new series its `patches/<series>`: nixpkgs' own
   patches for that series if it has them, otherwise a copy of the newest older series. It then
   copies the pinned nixpkgs into a scratch directory, swaps in this repository's patch set, runs
   nixpkgs' `update.py --channel <channel> --version <version>` there, and stores the
   `data.json` it writes. Releases beyond the newest three of their channel are pruned.
3. It opens one pull request per release (`update/<channel>-<version>`), approves the CI run
   GitHub holds for pull requests opened by a workflow, and enables auto merge.
4. The same workflow bumps the pinned nixpkgs in its own pull request (`update/nixpkgs`).

CI builds both channels on Linux (`ubuntu-latest`) and macOS (`macos-latest`, Apple silicon), runs
`flutter --version`, creates a project, runs `flutter test` and `flutter build web` (plus `flutter
build macos --debug` on macOS for 3.50 and later), and generates freezed code with `dart run build_runner build`,
failing on any `InvalidType`. When a build fails because a patch no longer applies, the pull request
stays red and CI opens an issue naming the patches.

Run the updater locally:

```sh
nix run .#updater -- plan
nix run .#updater -- update --channel beta
```

## Rebasing a patch

When a new series moves code a patch touches, the build fails with `Hunk #1 FAILED` for that
patch, or the patch applies but the Dart compile of `flutter-tools` fails. Also read the
`succeeded ... with fuzz` lines in the build log: a fuzzy hunk can land in the wrong place and
still compile. On the update branch:

```sh
overlay=$PWD
series=3_50
git clone --depth 1 --branch 3.50.0-0.2.pre https://github.com/flutter/flutter /tmp/flutter
cd /tmp/flutter
patch -p1 --merge < "$overlay/patches/$series/disable-auto-update.patch"
# resolve the conflict markers and fix the code until it is what the patch intends, then
git add --intent-to-add . && git diff > "$overlay/patches/$series/disable-auto-update.patch"
```

`--intent-to-add` keeps files the patch creates in the diff. Keep any description above the first
`diff` line of the old patch.

To rebase a global nixpkgs patch, write the rebased version into `patches/<series>` under the
same file name; it then replaces the global one for that series only.

The artifact hashes in `data.json` come from running the patched `flutter precache`, so they
depend on the patch set. After changing patches, regenerate the data for every kept release of
the series, then build and push; CI reruns:

```sh
nix run .#updater -- update --channel beta --version 3.50.0-0.2.pre
nix build .#beta
```

A local build can pass with stale hashes when your store already holds artifacts under the old
hashes; CI starts from an empty store and catches it.

### Rebases carried for 3.50

| Patch | Why |
| --- | --- |
| `override-host-platform-base.patch` | `hostPlatform` now applies `FLUTTER_HOST_ARCH` and delegates to `defaultHostPlatform`; the Nix switch moved there. |
| `override-host-platform.patch` | `_PosixUtils` lost its `_hostPlatform` field; the Nix switch now seeds the field of `_MacOSUtils`, which probes `sysctl`. |
| `disable-auto-update.patch` | Hunks moved, and the runner dropped the `globals.` prefix. |
| `gradle-flutter-tools-wrapper.patch` | `gradle_utils.dart` no longer imports `globals`; it now uses the injected platform. |

## License

MIT, like the nixpkgs Flutter expressions and patches this builds on.
