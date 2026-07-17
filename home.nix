{ pkgs, ... }:

{
  home.username = "neraverin";
  home.homeDirectory = "/home/neraverin";
  home.stateVersion = "26.05";

  home.packages = with pkgs; [
    bat
    curl
    fd
    git
    htop
    jq
    ripgrep
    tree
    unzip
    wget
  ];

  home.sessionVariables = {
    EDITOR = "vim";
  };

  programs.home-manager.enable = true;

  programs.bash = {
    enable = true;
    shellAliases = {
      cat = "bat";
      grep = "rg";
      ll = "ls -alF";
      ls = "ls --color=auto";
    };
  };

  programs.direnv = {
    enable = true;
    nix-direnv.enable = true;
  };

  programs.fzf.enable = true;

  programs.git = {
    enable = true;
    settings.user.name = "neraverin";
  };

  programs.starship = {
    enable = true;
    enableBashIntegration = true;
  };

  programs.vim = {
    enable = true;
    defaultEditor = true;
  };

  programs.zoxide = {
    enable = true;
    enableBashIntegration = true;
  };
}
