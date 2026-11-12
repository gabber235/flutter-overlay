# Every kept release (`versions/<version>.json`) that has an archive for the host system, and the
# newest one of each channel.
{ pkgs }:
let
  inherit (pkgs) lib;
  system = pkgs.stdenv.hostPlatform.system;

  releases = lib.filter (release: release.archives ? ${system}) (
    map (name: lib.importJSON (../versions + "/${name}")) (
      lib.attrNames (
        lib.filterAttrs (name: type: type == "regular" && lib.hasSuffix ".json" name) (
          builtins.readDir ../versions
        )
      )
    )
  );

  versions = lib.listToAttrs (
    map (
      release: lib.nameValuePair release.version (pkgs.callPackage ./flutter.nix { inherit release; })
    ) releases
  );

  # The newest kept release of a channel. Channels never mix releases and prereleases, so Nix's
  # version order is Flutter's.
  newest =
    channel:
    let
      candidates = map (release: release.version) (
        lib.filter (release: release.channel == channel) releases
      );
    in
    if candidates == [ ] then
      throw "flutter-overlay: no ${channel} release kept for ${system}"
    else
      versions.${lib.last (lib.sort (a: b: builtins.compareVersions a b < 0) candidates)};
in
{
  inherit versions;
  stable = newest "stable";
  beta = newest "beta";
}
