# flutter-overlay

Flutter for Nix on the **stable** and **beta** channels, updated automatically, with **no
patches**: every package is the unmodified upstream release archive.

nixpkgs ships Flutter stable releases a while after they come out, never ships beta, and patches
Flutter so it can run from the read only Nix store. Those patches break on every new release
series. This flake instead pins Flutter's own release archive by its published sha256 and runs it
from a writable copy, so a new release needs nothing but its hash.

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
      flutter = pkgs.flutterChannels.beta;
    in
    {
      devShells.${system}.default = pkgs.mkShell {
        packages = [ flutter ];
        shellHook = flutter.shellHook;
      };
    };
}
```

Or skip the overlay and take a package directly: `flutter-overlay.packages.${system}.beta`.

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
  flutter = inputs.flutter-overlay.packages.${pkgs.stdenv.hostPlatform.system}.beta;
in
{
  packages = [ flutter ];
  enterShell = flutter.shellHook;
}
```

## How it works

```
versions/<channel>/<version>.json   archive URL and sha256 per system, from Flutter's manifests
versions/channels.json              the newest kept release of each channel
nix/flutter.nix                     fetches the archive and puts a launcher on bin/flutter and bin/dart
nix/launcher.sh                     unpacks the archive on first run and runs upstream flutter or dart
updater/                            the updater (Python, standard library only) and its tests
```

Flutter writes into its own SDK directory (`bin/cache`, engine artifacts, Gradle files) and does
not support a read only install ([flutter/flutter#44526](https://github.com/flutter/flutter/issues/44526)).
So Nix only fetches and verifies the archive; the first `flutter` or `dart` run unpacks it into
`~/.cache/flutter-overlay/<version>-<archive id>` and every run after that executes upstream's own
`bin/flutter` or `bin/dart` there, exactly like a manual install. Flutter downloads its engine
artifacts into that copy as usual.

- **`FLUTTER_OVERLAY_HOME`** moves the copies elsewhere (default `$XDG_CACHE_HOME/flutter-overlay`,
  or `~/.cache/flutter-overlay`). Copies are never removed for you: delete a version's directory
  to reclaim its space, and the next run unpacks it again.
- **`shellHook`** (passthru) unpacks the SDK if needed and exports `FLUTTER_ROOT`, which IDEs and
  tools started outside `flutter` use to find the SDK. It replaces the `passthru.sdk` of earlier
  versions of this flake: change `FLUTTER_ROOT = "${flutter.sdk}"` to `enterShell =
  flutter.shellHook` (devenv) or `shellHook = flutter.shellHook` (mkShell).
- **`flutter upgrade`, `downgrade` and `channel <name>`** are refused by the launcher: the
  version is pinned by Nix, so change it in your flake instead. Tools that run
  `$FLUTTER_ROOT/bin/flutter` directly bypass this check.
- **NixOS** needs [`programs.nix-ld.enable = true`](https://github.com/nix-community/nix-ld):
  upstream's prebuilt binaries expect a standard dynamic loader, for the command line and IDEs
  alike. Other Linux distributions and macOS need nothing.
- **Not provided**: `buildFlutterApplication` and the other nixpkgs passthru attributes. Building
  an app inside the Nix sandbox needs nixpkgs' patched Flutter; use nixpkgs' own `flutter` for
  that.

## Outputs

Systems: `aarch64-darwin`, `x86_64-linux`. Flutter publishes no archive for aarch64 Linux, and
nixpkgs no longer supports x86_64-darwin.

| Output | Contents |
| --- | --- |
| `packages.<system>.stable` | Current stable release |
| `packages.<system>.beta` | Current beta release |
| `packages.<system>.default` | Same as `stable` |
| `legacyPackages.<system>.versions."<version>"` | Every kept release by exact version, for example `versions."3.47.8"` |
| `overlays.default` | Adds `pkgs.flutterChannels.{stable,beta,versions}` |
| `checks.<system>.*` | Builds of `stable` and `beta` and the updater unit tests |
| `packages.<system>.updater` | The release updater (see below) |

Each channel keeps its newest three releases. Every package carries `passthru.shellHook`,
`passthru.channel` and `passthru.archive` (the fetched upstream archive).

## Channels

Flutter publishes release archives, listed in
[`releases_macos.json`](https://storage.googleapis.com/flutter_infra_release/releases/releases_macos.json)
and `releases_linux.json`, for two channels that are still alive:

| Channel | Packaged | Why |
| --- | --- | --- |
| `stable` | yes | |
| `beta` | yes | |
| `dev` | no | Retired by Flutter in 2022. The manifest still lists it, but its last release is from April 2022. |
| `main` (`master`) | no | Has no release archives, only a moving branch, so there is nothing reproducible to pin. |

## Updates

The `Update` workflow runs every 12 hours and on demand:

1. `updater plan` reads the release manifests and lists each channel whose current release is not
   in `versions/` and has no open update branch.
2. For each, `updater update` writes `versions/<channel>/<version>.json` with the archive URL and
   sha256 of every system straight from the manifests, prunes releases beyond the newest three of
   the channel, and records each channel's newest release in `versions/channels.json`. A release
   not yet published for every system waits for the next run. Nothing is built.
3. It opens one pull request per release (`update/<channel>-<version>`), approves the CI run
   GitHub holds for pull requests opened by a workflow, and enables auto merge.
4. The same workflow bumps the pinned nixpkgs in its own pull request (`update/nixpkgs`).

CI runs on Linux (`ubuntu-latest`) and macOS (`macos-latest`, Apple silicon). For both channels it
unpacks a fresh SDK, checks the shell hook and the pinned version guard, creates a project, runs
`flutter test` and `flutter build web` (plus `flutter build macos --debug` on macOS), and generates
freezed code with `dart run build_runner build`, failing on any `InvalidType`.

Run the updater locally:

```sh
nix run .#updater -- plan
nix run .#updater -- update --channel beta
```

## License

MIT.
