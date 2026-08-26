{
  description = "Nix home-manager configuration for development machines";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
    nixpkgs-unstable.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

    herdr = {
      url = "github:ogulcancelik/herdr/v0.7.4";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    home-manager = {
      url = "github:nix-community/home-manager/release-26.05";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { nixpkgs, nixpkgs-unstable, herdr, home-manager, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        config.allowUnfree = true;
      };
      unstable = import nixpkgs-unstable {
        inherit system;
        config.allowUnfree = true;
      };

      mkHome = module: home-manager.lib.homeManagerConfiguration {
        inherit pkgs;

        modules = [
          {
            _module.args = {
              inherit unstable;
              herdr = herdr.packages.${system}.default;
            };
          }
          module
        ];
      };
    in
    {
      homeConfigurations = {
        # Servers and WSL: the base profile.
        "neraverin@work-wsl" = mkHome ./home/common.nix;

        # Graphical workstations: base profile plus desktop extras.
        "neraverin@workstation" = mkHome ./home/workstation.nix;
      };
    };
}
