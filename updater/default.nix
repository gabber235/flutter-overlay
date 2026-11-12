# The release updater: a standard library Python script.
{
  lib,
  python3,
  makeWrapper,
  stdenvNoCC,
  git,
}:
stdenvNoCC.mkDerivation {
  pname = "flutter-overlay-updater";
  version = "1";
  src = ./flutter_overlay_update.py;
  dontUnpack = true;
  nativeBuildInputs = [ makeWrapper ];
  installPhase = ''
    install -Dm644 $src $out/libexec/flutter_overlay_update.py
    makeWrapper ${lib.getExe python3} $out/bin/flutter-overlay-update \
      --add-flags $out/libexec/flutter_overlay_update.py \
      --prefix PATH : ${lib.makeBinPath [ git ]}
  '';
  meta.mainProgram = "flutter-overlay-update";
}
