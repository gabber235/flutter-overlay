# Every kept release (`versions/<channel>/<version>.json`) and each channel's newest one, as
# `versions/channels.json` records it.
{ pkgs }:
let
  inherit (pkgs) lib;

  heads = lib.importJSON ../versions/channels.json;

  # { <version> = package; } for one channel.
  channel =
    name:
    lib.mapAttrs' (
      file: _:
      let
        release = lib.importJSON (../versions + "/${name}/${file}");
      in
      lib.nameValuePair release.version (pkgs.callPackage ./flutter.nix { inherit release; })
    ) (builtins.readDir (../versions + "/${name}"));

  byChannel = lib.mapAttrs (name: _: channel name) heads;
in
{
  # A version published on both channels is the same release; the stable build wins.
  versions = byChannel.beta // byChannel.stable;
  stable = byChannel.stable.${heads.stable};
  beta = byChannel.beta.${heads.beta};
}
