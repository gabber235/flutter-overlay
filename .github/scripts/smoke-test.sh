#!/usr/bin/env bash
# Smoke tests one channel the way a developer uses it: unpacks the SDK on first run, creates a
# project, runs its tests, builds it for the web (and for macOS on a Mac), and generates code with
# build_runner, whose analyzer must see dart:ui.
#
# Usage: smoke-test.sh <channel>, from the repository root.
set -euo pipefail

channel=$1
work=$(mktemp -d)

nix build ".#$channel" --out-link "$work/flutter"
export PATH="$work/flutter/bin:$PATH"
# A fresh SDK home, so the first run unpacks the archive.
export FLUTTER_OVERLAY_HOME="$work/sdk"
flutter --version

# The version is pinned by Nix, so the launcher refuses to change it.
if flutter upgrade 2>/dev/null; then
  echo "flutter upgrade should be refused" >&2
  exit 1
fi

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
