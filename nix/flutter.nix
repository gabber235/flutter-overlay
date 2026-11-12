# One Flutter release: the upstream archive, unmodified, behind a launcher that runs it from a
# writable copy (see launcher.sh).
{
  lib,
  stdenvNoCC,
  fetchurl,
  replaceVarsWith,
  bash,
  flock,
  unzip,
  xz,
  gnutar,
  libepoxy,
  fontconfig,
  release,
}:
let
  inherit (release) version channel;

  archive = fetchurl { inherit (release.archives.${stdenvNoCC.hostPlatform.system}) url sha256; };

  launcher = replaceVarsWith {
    src = ./launcher.sh;
    isExecutable = true;
    replacements = {
      inherit version archive;
      bash = lib.getExe bash;
      flock = lib.getExe flock;
      unzip = lib.getExe unzip;
      xz = lib.getExe' xz "xz";
      tar = lib.getExe gnutar;
    };
  };

  # The Linux engine (libflutter_linux_gtk.so) finds its libraries only next to itself or in the
  # system's. A Linux app built in a Nix shell loads GTK and its stack itself, but not these two,
  # so it fails to start without them on the library path.
  engineLibraries = lib.optionalString stdenvNoCC.hostPlatform.isLinux ''
    export LD_LIBRARY_PATH="${
      lib.makeLibraryPath [
        libepoxy
        fontconfig
      ]
    }''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  '';
in
stdenvNoCC.mkDerivation {
  pname = "flutter";
  inherit version;
  dontUnpack = true;
  installPhase = ''
    mkdir -p $out/bin
    for tool in flutter dart; do
      printf '#!%s\nexec %s %s "$@"\n' ${lib.getExe bash} ${launcher} $tool > $out/bin/$tool
      chmod +x $out/bin/$tool
    done
  '';
  passthru = {
    inherit channel archive;
    # Unpacks the SDK if needed and exports FLUTTER_ROOT, for IDEs and tools that look for the
    # SDK there, and on Linux puts the engine's libraries on the library path for the apps the
    # shell builds. Use it as a mkShell `shellHook` or a devenv `enterShell`.
    shellHook = ''
      export FLUTTER_ROOT="$(${launcher} root)"
      ${engineLibraries}'';
  };
  meta = {
    description = "Flutter ${version} (${channel} channel), the unmodified upstream release";
    homepage = "https://flutter.dev";
    license = lib.licenses.bsd3;
    mainProgram = "flutter";
    platforms = builtins.attrNames release.archives;
  };
}
