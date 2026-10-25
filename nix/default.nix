# Builds every release in ../versions with the Flutter builders of the given nixpkgs.
#
# Each release is a `data.json` written by nixpkgs' Flutter update script. Its patch set is the
# nixpkgs global Flutter patches followed by this repository's patches for the release series
# (`../patches/<major>_<minor>`). A series patch with the same file name as a global patch replaces
# it, which is how a global patch gets rebased for a series that moved the patched code.
{ pkgs }:
let
  inherit (pkgs) lib;
  inherit (pkgs.flutterPackages) mkFlutter wrapFlutter;

  globalPatchDir = pkgs.path + "/pkgs/development/compilers/flutter/patches";

  # Patch files in `dir`, keyed by file name.
  patchesIn =
    dir:
    lib.optionalAttrs (builtins.pathExists dir) (
      lib.mapAttrs (name: _: dir + "/${name}") (
        lib.filterAttrs (name: type: type == "regular" && lib.hasSuffix ".patch" name) (
          builtins.readDir dir
        )
      )
    );

  # "3.50.0-0.2.pre" -> "3_50"
  seriesOf = version: lib.concatStringsSep "_" (lib.take 2 (lib.splitString "." version));

  patchesFor =
    version:
    let
      series = patchesIn (../patches + "/${seriesOf version}");
      global = removeAttrs (patchesIn globalPatchDir) (builtins.attrNames series);
    in
    lib.attrValues global ++ lib.attrValues series;

  # Lays out the wrapped SDK like an upstream Flutter install, so its Dart executables live inside
  # the Flutter tree.
  #
  # nixpkgs links `bin/cache/dart-sdk` to the standalone Dart SDK, so a running `dart` resolves its
  # own path (`Platform.resolvedExecutable`) outside Flutter. Tools that find Flutter relative to
  # that path then miss it: build_runner looks for dart:ui at `bin/cache/pkg/sky_engine` next to
  # the resolved Dart SDK, and without it generators such as freezed write `InvalidType` for types
  # like `Color` (dart-lang/build#3596). Here `bin/cache/dart-sdk` becomes a directory of links to
  # the Dart SDK that holds real copies of its executables, and `bin/dart` points at that copy.
  #
  # The wrapped SDK is a symlinkJoin whose `postBuild` is already baked into `buildCommand`, so the
  # layout step extends `buildCommand`. `sdk` points at the result, as it does in nixpkgs.
  upstreamLayout =
    wrapped:
    wrapped.overrideAttrs (
      finalAttrs: prev: {
        buildCommand = prev.buildCommand + ''
          shopt -s dotglob
          dartSdk=$(readlink --canonicalize "$out/bin/cache/dart-sdk")
          rm "$out/bin/cache/dart-sdk" "$out/bin/dart"
          mkdir --parents "$out/bin/cache/dart-sdk/bin"
          for entry in "$dartSdk"/*; do
            if [ "''${entry##*/}" != bin ]; then
              ln --symbolic "$entry" "$out/bin/cache/dart-sdk/"
            fi
          done
          for entry in "$dartSdk"/bin/*; do
            if [ -f "$entry" ] && [ -x "$entry" ]; then
              cp "$entry" "$out/bin/cache/dart-sdk/bin/"
            else
              ln --symbolic "$entry" "$out/bin/cache/dart-sdk/bin/"
            fi
          done
          ln --symbolic cache/dart-sdk/bin/dart "$out/bin/dart"
        '';
        passthru = prev.passthru // {
          sdk = finalAttrs.finalPackage;
        };
      }
    );

  build =
    data:
    upstreamLayout (
      wrapFlutter (
        mkFlutter (
          data
          // {
            patches = patchesFor data.version;
          }
        )
      )
    );

  releases = lib.mapAttrs (version: _: lib.importJSON (../versions + "/${version}/data.json")) (
    lib.filterAttrs (_: type: type == "directory") (builtins.readDir ../versions)
  );

  versions = lib.mapAttrs (_: build) releases;

  # The newest kept release of a channel.
  newest =
    channel:
    let
      candidates = builtins.attrNames (lib.filterAttrs (_: data: data.channel == channel) releases);
      sorted = lib.sort (a: b: builtins.compareVersions a b < 0) candidates;
    in
    if candidates == [ ] then
      throw "flutter-overlay: no release kept for channel ${channel}"
    else
      versions.${lib.last sorted};
in
{
  inherit versions;
  stable = newest "stable";
  beta = newest "beta";
}
