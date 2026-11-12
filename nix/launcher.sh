#!@bash@
# Runs upstream Flutter, unmodified, from a writable SDK.
#
# Flutter writes into its own SDK (bin/cache, engine artifacts, Gradle files), so it cannot run
# from the read only Nix store. On first use this unpacks the release archive Nix fetched into
# $FLUTTER_OVERLAY_HOME/<version> (default ~/.cache/flutter-overlay) and from then on runs
# `flutter` or `dart` from there, exactly like a manual install.
#
# Usage: launcher <flutter|dart> [arguments...]
#        launcher root    (unpack if needed and print the SDK root, for FLUTTER_ROOT)
set -euo pipefail

tool=$1
shift
version='@version@'
archive='@archive@'
home=${FLUTTER_OVERLAY_HOME:-${XDG_CACHE_HOME:-$HOME/.cache}/flutter-overlay}
root=$home/$version

# The version is pinned by Nix; changing it inside the copy would make the two disagree.
if [ "$tool" = flutter ]; then
  case "${1:-}" in
    upgrade | downgrade | channel)
      if [ "$1" != channel ] || [ $# -gt 1 ]; then
        echo "flutter-overlay: Flutter $version is pinned by Nix. Change the version in your flake instead of running 'flutter $1'." >&2
        exit 1
      fi
      ;;
  esac
fi

if [ ! -x "$root/bin/flutter" ]; then
  echo "flutter-overlay: unpacking Flutter $version into $root (first run only)" >&2
  mkdir -p "$home"
  scratch=$(mktemp -d "$home/.unpack-$version.XXXXXX")
  case $archive in
    *.zip) '@unzip@' -q "$archive" -d "$scratch" ;;
    *) '@xz@' -dc "$archive" | '@tar@' -x -C "$scratch" ;;
  esac
  # A concurrent first run may have finished already; the first complete SDK wins.
  if [ ! -e "$root" ]; then
    mv "$scratch/flutter" "$root"
  fi
  rm -rf "$scratch"
fi

if [ "$tool" = root ]; then
  echo "$root"
  exit 0
fi
exec "$root/bin/$tool" "$@"
