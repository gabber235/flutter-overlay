#!@bash@
# Runs upstream Flutter, unmodified, from a writable SDK.
#
# Flutter writes into its own SDK (bin/cache, engine artifacts, Gradle files), so it cannot run
# from the read only Nix store. On first use this unpacks the release archive Nix fetched into
# $FLUTTER_OVERLAY_HOME/<version>-<archive id> (default ~/.cache/flutter-overlay) and from then
# on runs `flutter` or `dart` from there, exactly like a manual install.
#
# Usage: launcher <flutter|dart> [arguments...]
#        launcher root    (unpack if needed and print the SDK root, for FLUTTER_ROOT)
set -euo pipefail

tool=$1
shift
version='@version@'
archive='@archive@'
home=${FLUTTER_OVERLAY_HOME:-${XDG_CACHE_HOME:-$HOME/.cache}/flutter-overlay}
# The archive's store hash names the copy, so another platform or archive never reuses it.
id=${archive##*/}
root=$home/$version-${id:0:12}
marker=$root/.flutter-overlay-unpacked

# The version is pinned by Nix; changing it inside the copy would make the two disagree.
if [ "$tool" = flutter ]; then
  words=()
  for arg in "$@"; do
    [[ $arg == -* ]] || words+=("$arg")
  done
  case "${words[0]:-}" in
    upgrade | downgrade) refused=${words[0]} ;;
    channel) [ ${#words[@]} -gt 1 ] && refused="channel ${words[1]}" ;;
  esac
  if [ -n "${refused:-}" ]; then
    echo "flutter-overlay: Flutter $version is pinned by Nix. Change the version in your flake instead of running 'flutter $refused'." >&2
    exit 1
  fi
fi

if [ ! -e "$marker" ]; then
  mkdir -p "$home"
  # One unpack at a time; a run that waited finds the marker and skips it.
  exec 9>"$root.lock"
  '@flock@' 9
  if [ ! -e "$marker" ]; then
    echo "flutter-overlay: unpacking Flutter $version into $root (first run only)" >&2
    rm -rf "$root"
    scratch=$(mktemp -d "$root.unpack.XXXXXX")
    trap 'rm -rf "$scratch"' EXIT
    case $archive in
      *.zip) '@unzip@' -q "$archive" -d "$scratch" ;;
      *) '@xz@' -dc "$archive" | '@tar@' -x -C "$scratch" ;;
    esac
    mv "$scratch/flutter" "$root"
    touch "$marker"
    rm -rf "$scratch"
    trap - EXIT
  fi
  exec 9>&-
fi

if [ "$tool" = root ]; then
  echo "$root"
  exit 0
fi
exec "$root/bin/$tool" "$@"
