#!/usr/bin/env bash
# Smoke tests one channel the way a developer uses it: unpacks the SDK on first run, creates a
# project, runs its tests, builds it for the web (and for macOS on a Mac), and generates code with
# build_runner, whose analyzer must see dart:ui.
#
# Usage: smoke-test.sh <channel>, from the repository root.
set -euo pipefail

channel=$1
work=$(mktemp -d)
root=$PWD

nix build ".#$channel" --out-link "$work/flutter"
export PATH="$work/flutter/bin:$PATH"
# A fresh SDK home, so the first run unpacks the archive.
export FLUTTER_OVERLAY_HOME="$work/sdk"
flutter --version

# The version is pinned by Nix, so the launcher refuses to change it, flags or not.
for command in upgrade "-v upgrade" "channel main"; do
  # shellcheck disable=SC2086
  if flutter $command 2>/dev/null; then
    echo "flutter $command should be refused" >&2
    exit 1
  fi
done

# The shell hook exports FLUTTER_ROOT for IDEs and tools; it must name the unpacked SDK.
eval "$(nix eval --raw ".#$channel.shellHook")"
[[ $FLUTTER_ROOT == "$FLUTTER_OVERLAY_HOME"/* && -x $FLUTTER_ROOT/bin/flutter ]] || {
  echo "the shell hook set FLUTTER_ROOT to '$FLUTTER_ROOT'" >&2
  exit 1
}

cd "$work"
flutter create --no-pub t
cd t
flutter test
flutter build web

# The macOS build copies FlutterMacOS.framework out of the SDK and thins it in place with lipo,
# which needs the SDK to be writable.
if [[ $(uname) == Darwin ]]; then
  flutter build macos --debug
fi

# A Linux app built in a Nix shell starts only when the shell hook puts every library the engine
# links on the library path, and newer engines link more. It runs until the timeout ends it (124);
# a missing library ends it at once.
if [[ $(uname) == Linux ]]; then
  shell="(builtins.getFlake \"path:$root\").inputs.nixpkgs.legacyPackages.x86_64-linux.callPackage $root/.github/scripts/linux-desktop.nix { }"
  # shellcheck disable=SC2016
  nix develop --impure --expr "$shell" --command bash -c '
    set -euo pipefail
    eval "$1"
    flutter build linux --debug
    status=0
    timeout 20 xvfb-run --auto-servernum build/linux/x64/debug/bundle/t || status=$?
    [[ $status == 124 ]] || { echo "the Linux app exited with $status instead of running" >&2; exit 1; }
  ' bash "$(nix eval --raw "$root#$channel.shellHook")"
fi

# A field common to every variant of a freezed union lands on the shared mixin. Its type comes from
# dart:ui, which build_runner only resolves when the running `dart` sits inside the Flutter tree;
# otherwise freezed writes `InvalidType`.
flutter pub add freezed_annotation dev:build_runner dev:freezed
cat > lib/shape.dart <<'EOF'
import 'dart:ui';

import 'package:freezed_annotation/freezed_annotation.dart';

part 'shape.freezed.dart';

@freezed
sealed class Shape with _$Shape {
  const factory Shape.circle({required Color color, required double radius}) = Circle;
  const factory Shape.square({required Color color, required double side}) = Square;
}
EOF
dart run build_runner build

generated=lib/shape.freezed.dart
if grep --line-number InvalidType "$generated"; then
  echo "build_runner did not resolve dart:ui: $generated has InvalidType" >&2
  exit 1
fi
grep --quiet 'Color get color' "$generated" || {
  echo "$generated lacks the shared Color field" >&2
  exit 1
}
echo "build_runner resolved dart:ui"
