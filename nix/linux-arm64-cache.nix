# bin/cache for aarch64 Linux, where Flutter publishes no release archive.
#
# Flutter supports ARM Linux hosts from a git checkout of the release; the x64 Linux archive holds
# that checkout next to its x64 bin/cache. This builds the bin/cache Flutter's tool would download
# on an ARM Linux host, from the files the updater recorded (`cache.files`, each pinned by the
# sha256 of the published file), unpacked where the tool unpacks them and stamped as the tool
# stamps them, so it finds everything up to date.
#
# The tool itself runs from bin/cache/flutter_tools.snapshot, which `bin/internal/shared.sh`
# builds with `pub upgrade` unless the stamp matches. The archive's snapshot is for x64, so this
# builds it the way shared.sh does, from the archive's own sources and the package versions its
# lock records (`toolPackages`), resolved offline. The `tool` output is the tool's .dart_tool,
# whose package_config.json points at those packages in the `packages` output (pub writes into
# its cache while it resolves, so it gets a copy of its own); the archive's points at Google's
# builder.
{
  lib,
  stdenv,
  fetchurl,
  runCommand,
  unzip,
  gnutar,
  xz,
  git,
  patchelf,
  which,
  version,
  archive,
  source,
}:
let
  inherit (source) cache;

  # The tool's packages as pub keeps them in its cache, with the hashes pub verifies.
  pubCache = runCommand "flutter-${version}-tool-packages" { } (
    ''
      mkdir -p $out/hosted/pub.dev $out/hosted-hashes/pub.dev
    ''
    + lib.concatMapStrings (package: ''
      dir=$out/hosted/pub.dev/${package.name}-${package.version}
      mkdir -p $dir
      tar -xzf ${
        fetchurl {
          url = "https://pub.dev/api/archives/${package.name}-${package.version}.tar.gz";
          inherit (package) sha256;
        }
      } -C $dir
      # pub keeps the bare hash, without a newline.
      printf %s ${package.sha256} > $out/hosted-hashes/pub.dev/${package.name}-${package.version}.sha256
    '') source.toolPackages
  );

  unpack =
    file:
    let
      src = fetchurl { inherit (file) url sha256; };
      into = "$out/${file.into}";
    in
    ''
      mkdir -p ${into}
      ${
        {
          zip = "unzip -o -q ${src} -d ${into}";
          tgz = "tar -xzf ${src} -C ${into}";
          file = "cp ${src} ${into}/${baseNameOf file.url}";
        }
        .${file.unpack}
      }
      ${lib.concatMapStrings (path: "rm -f ${into}/${path}\n") (file.remove or [ ])}
      ${lib.optionalString (file.executable or false) ''
        # As the tool's _makeFilesExecutable.
        chmod a+r,a+x ${into}
        find ${into} -type f \( -perm -u+x -o -name flutter_tester \) -exec chmod a+r,a+x {} +
      ''}
    '';
in
stdenv.mkDerivation {
  pname = "flutter-bin-cache";
  inherit version;
  outputs = [
    "out"
    "tool"
    "packages"
  ];
  dontUnpack = true;
  nativeBuildInputs = [
    unzip
    gnutar
    xz
    git
    patchelf
    # The tool looks things up with it when it starts.
    which
  ];
  # The output holds upstream's binaries as published: no patchelf, no stripping, no shebang
  # rewriting.
  dontFixup = true;

  buildPhase = ''
    runHook preBuild

    ${lib.concatMapStrings unpack cache.files}
    # As shared.sh before it fetches Dart, then update_dart_sdk.sh.
    touch $out/.dartignore
    find $out/dart-sdk -type d -exec chmod 755 {} +
    find $out/dart-sdk -type f -perm -u+x -exec chmod a+x,a+r {} +
    ${lib.concatStrings (
      lib.mapAttrsToList (
        name: value: "printf %s ${lib.escapeShellArg value} > $out/${name}.stamp\n"
      ) cache.stamps
    )}

    # The checkout, with this bin/cache, to build the tool in.
    tar -xJf ${archive} --exclude=flutter/bin/cache -C "$NIX_BUILD_TOP"
    root=$NIX_BUILD_TOP/flutter
    cp -R $out $root/bin/cache
    chmod -R u+w $root/bin/cache
    # The Dart SDK expects the standard dynamic loader, which the build sandbox lacks. Only this
    # throwaway copy is pointed at Nix's loader to build the tool; the output keeps the original.
    for file in $root/bin/cache/dart-sdk/bin/* $root/bin/cache/dart-sdk/bin/utils/*; do
      if [ -f "$file" ] && isELF "$file"; then
        patchelf --set-interpreter "$(cat $NIX_CC/nix-support/dynamic-linker)" "$file"
      fi
    done
    dart=$root/bin/cache/dart-sdk/bin/dart
    cp -R ${pubCache} $packages
    chmod -R u+w $packages
    export HOME=$NIX_BUILD_TOP/home FLUTTER_ROOT=$root PUB_CACHE=$packages
    mkdir -p $HOME
    git config --global --add safe.directory '*'

    # As shared.sh before it builds the tool: writes bin/cache/engine.stamp and engine.realm.
    bash $root/bin/internal/update_engine_version.sh
    cd $root/packages/flutter_tools
    "$dart" pub get --offline --enforce-lockfile --suppress-analytics
    # The command shared.sh compiles the tool with, FLUTTER_TOOL_ARGS empty.
    "$dart" --verbosity=error --snapshot=$out/flutter_tools.snapshot --snapshot-kind=app-jit \
      --packages=.dart_tool/package_config.json --no-enable-mirrors bin/flutter_tools.dart > /dev/null
    cp -R .dart_tool $tool
    # pub's record of the projects that use its cache, which here is only the build directory.
    rm -rf $packages/active_roots

    runHook postBuild
  '';

  passthru = { inherit pubCache; };
  meta.platforms = [ "aarch64-linux" ];
}
