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
{
  packages = [ inputs.flutter-overlay.packages.${pkgs.system}.beta ];
}
```

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
the usual passthru (`buildFlutterApplication`, `dart`, `engineVersion`).

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
2. For each, `updater update` copies the pinned nixpkgs into a scratch directory, runs nixpkgs'
   `update.py --channel <channel> --version <version>` there, and stores the `data.json` it
   writes. The first release of a new series gets `patches/<series>` copied from the newest older
   series. Releases beyond the newest three of their channel are pruned.
3. It opens one pull request per release (`update/<channel>-<version>`), dispatches CI on it and
   enables auto merge.
4. The same workflow bumps the pinned nixpkgs in its own pull request (`update/nixpkgs`).

CI builds both channels on Linux (`ubuntu-latest`) and macOS (`macos-latest`, Apple silicon),
runs `flutter --version`, creates a project, runs `flutter test` and `flutter build web`. When a
build fails because a patch no longer applies, the pull request stays red and CI opens an issue
naming the patches.

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
same file name; it then replaces the global one for that series only. Check with
`nix build .#beta`, push, and CI reruns.

### Rebases carried for 3.50

| Patch | Why |
| --- | --- |
| `override-host-platform-base.patch` | `hostPlatform` now applies `FLUTTER_HOST_ARCH` and delegates to `defaultHostPlatform`; the Nix switch moved there. |
| `override-host-platform.patch` | `_PosixUtils` lost its `_hostPlatform` field; the Nix switch now seeds the field of `_MacOSUtils`, which probes `sysctl`. |
| `disable-auto-update.patch` | Hunks moved, and the runner dropped the `globals.` prefix. |
| `gradle-flutter-tools-wrapper.patch` | `gradle_utils.dart` no longer imports `globals`; it now uses the injected platform. |

## License

MIT, like the nixpkgs Flutter expressions and patches this builds on.
