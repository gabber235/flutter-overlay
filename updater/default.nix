# The release updater: a stdlib Python script plus PyYAML, which nixpkgs' update script imports.
{
  lib,
  python3,
  makeWrapper,
  stdenvNoCC,
  nix,
  git,
}:
let
  python = python3.withPackages (ps: [ ps.pyyaml ]);
in
stdenvNoCC.mkDerivation {
  pname = "flutter-overlay-updater";
  version = "1";
  src = ./flutter_overlay_update.py;
  dontUnpack = true;
  nativeBuildInputs = [ makeWrapper ];
  installPhase = ''
    install -Dm644 $src $out/libexec/flutter_overlay_update.py
    makeWrapper ${lib.getExe python} $out/bin/flutter-overlay-update \
      --add-flags $out/libexec/flutter_overlay_update.py \
      --prefix PATH : ${lib.makeBinPath [ git ]} \
      --suffix PATH : ${lib.makeBinPath [ nix ]}
  '';
  meta.mainProgram = "flutter-overlay-update";
}
