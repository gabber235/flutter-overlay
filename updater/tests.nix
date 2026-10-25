# Runs the updater's unit tests. They use no network and no Nix.
{ python3, runCommand }:
runCommand "flutter-overlay-updater-tests" { nativeBuildInputs = [ python3 ]; } ''
  cp ${./flutter_overlay_update.py} flutter_overlay_update.py
  cp ${./test_flutter_overlay_update.py} test_flutter_overlay_update.py
  python3 -m unittest -v test_flutter_overlay_update 2>&1
  touch $out
''
