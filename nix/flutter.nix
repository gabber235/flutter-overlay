# One Flutter release: the upstream archive, unmodified, behind a launcher that runs it from a
# writable copy (see launcher.sh). On Linux the launcher runs inside an FHS environment, so the
# prebuilt binaries in the archive and the engine artifacts Flutter downloads work on NixOS too.
{
  lib,
  stdenv,
  stdenvNoCC,
  fetchurl,
  replaceVarsWith,
  bash,
  unzip,
  xz,
  gnutar,
  buildFHSEnv,
  release,
}:
let
  inherit (release) version channel;
  system = stdenv.hostPlatform.system;

  archive = fetchurl { inherit (release.archives.${system}) url sha256; };

  launcher = replaceVarsWith {
    src = ./launcher.sh;
    isExecutable = true;
    replacements = {
      inherit version archive;
      bash = lib.getExe bash;
      unzip = lib.getExe unzip;
      xz = lib.getExe' xz "xz";
      tar = lib.getExe gnutar;
    };
  };

  # What a typical Linux machine provides and Flutter expects to find.
  fhs = buildFHSEnv {
    name = "flutter-${version}-env";
    targetPkgs =
      pkgs: with pkgs; [
        bash
        coreutils
        curl
        file
        git
        gnutar
        unzip
        which
        xz
        zlib
        stdenv.cc.cc.lib
      ];
    runScript = launcher;
  };

  run = if stdenv.hostPlatform.isLinux then lib.getExe fhs else launcher;
in
stdenvNoCC.mkDerivation {
  pname = "flutter";
  inherit version;
  dontUnpack = true;
  installPhase = ''
    mkdir -p $out/bin
    for tool in flutter dart; do
      printf '#!%s\nexec %s %s "$@"\n' ${lib.getExe bash} ${run} $tool > $out/bin/$tool
      chmod +x $out/bin/$tool
    done
  '';
  passthru = {
    inherit channel archive;
    # Unpacks the SDK if needed and exports FLUTTER_ROOT, for IDEs and tools that look for the
    # SDK there. Use it as a mkShell `shellHook` or a devenv `enterShell`.
    shellHook = ''export FLUTTER_ROOT="$(${launcher} root)"'';
  };
  meta = {
    description = "Flutter ${version} (${channel} channel), the unmodified upstream release";
    homepage = "https://flutter.dev";
    license = lib.licenses.bsd3;
    mainProgram = "flutter";
    platforms = builtins.attrNames release.archives;
  };
}
