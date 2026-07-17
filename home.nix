{ pkgs, ... }:

{
  home.username = "neraverin";
  home.homeDirectory = "/home/neraverin";
  home.stateVersion = "26.05";

  home.packages = with pkgs; [
    bat
    claude-code
    codex
    curl
    fd
    git
    glances
    htop
    jq
    procs
    ripgrep
    tree
    unzip
    wget
    witr
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
    settings = {
      format = "$time $username $custom $all";

      time = {
        disabled = false;
        format = "[$time]($style)";
        time_format = "%H:%M";
      };

      username = {
        show_always = true;
        format = "[$user]($style)";
        style_user = "#808080";
        style_root = "red";
      };

      custom.nix = {
        command = "printf Nix";
        format = "[$output]($style)";
        style = "bold blue";
        when = "true";
      };
    };
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
