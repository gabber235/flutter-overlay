{
  description = "Flutter for Nix on every release channel, updated automatically";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

  outputs =
    { self, nixpkgs }:
    let
      inherit (nixpkgs) lib;
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "aarch64-darwin"
      ];
      forAllSystems = f: lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
      channelsFor = pkgs: import ./nix { inherit pkgs; };
    in
    {
      overlays.default = final: _prev: { flutterChannels = channelsFor final; };

      packages = forAllSystems (
        pkgs:
        let
          flutter = channelsFor pkgs;
        in
        {
          inherit (flutter) stable beta;
          default = flutter.stable;
          updater = pkgs.callPackage ./updater { };
        }
      );

      # Every kept release, keyed by its exact Flutter version. These live in legacyPackages
      # because flake `packages` must be a flat set of derivations.
      legacyPackages = forAllSystems (pkgs: {
        inherit (channelsFor pkgs) versions;
      });

      checks = forAllSystems (pkgs: {
        inherit (self.packages.${pkgs.stdenv.hostPlatform.system}) stable beta;
        updater = pkgs.callPackage ./updater/tests.nix { };
      });

      apps = forAllSystems (pkgs: {
        updater = {
          type = "app";
          program = lib.getExe self.packages.${pkgs.stdenv.hostPlatform.system}.updater;
          meta.description = "Package the current release of a Flutter channel";
        };
      });

      formatter = forAllSystems (pkgs: pkgs.nixfmt);
    };
}
