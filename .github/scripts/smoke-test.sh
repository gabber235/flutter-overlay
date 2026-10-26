#!/usr/bin/env bash
# Smoke tests one channel the way a developer uses it: creates a project, runs its tests, builds it
# for the web (and for macOS on a Mac from 3.50), and generates code with build_runner, whose analyzer must see dart:ui.
#
# Usage: smoke-test.sh <channel>, from the repository root.
set -euo pipefail

channel=$1
work=$(mktemp -d)

nix build ".#$channel" --out-link "$work/flutter"
export PATH="$work/flutter/bin:$PATH"
flutter --version

cd "$work"
flutter create --no-pub t
cd t
flutter test
flutter build web

# The macOS build copies FlutterMacOS.framework out of the read only store and thins the copy in
# place with lipo, which fails unless the copy is writable. Before 3.50, flutter_tools relies on
# `rsync --chmod` for that, which openrsync (/usr/bin/rsync since macOS 15.4) ignores, so the build
# fails there (https://github.com/flutter/flutter/issues/195257). Build only on 3.50 and later.
version=$(flutter --version --machine | sed -n 's/.*"frameworkVersion": *"\([^"]*\)".*/\1/p')
IFS=. read -r major minor _ <<<"$version"
if [[ $(uname) == Darwin ]] && ((major > 3 || (major == 3 && minor >= 50))); then
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
