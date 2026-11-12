# The shell a developer builds a Linux desktop app in: the C toolchain and GTK that `flutter build
# linux` needs, a virtual display, and Mesa's software OpenGL, so the app can start on a runner
# without a GPU. The Flutter shell hook runs inside it (smoke-test.sh), as in a developer's shell.
{ pkgs }:
pkgs.mkShell {
  packages = with pkgs; [
    clang
    cmake
    ninja
    pkg-config
    gtk3
    xvfb-run
  ];
  LIBGL_DRIVERS_PATH = "${pkgs.mesa}/lib/dri";
  __EGL_VENDOR_LIBRARY_DIRS = "${pkgs.mesa}/share/glvnd/egl_vendor.d";
  __GLX_VENDOR_LIBRARY_NAME = "mesa";
  LD_LIBRARY_PATH = "${pkgs.mesa}/lib";
}
