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

  build =
    data:
    wrapFlutter (
      mkFlutter (
        data
        // {
          patches = patchesFor data.version;
        }
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
