{ lib, pkgs, unstable, ... }:

{
  home.username = "neraverin";
  home.homeDirectory = "/home/neraverin";
  home.stateVersion = "26.05";

  home.packages = with pkgs; [
    bat
    unstable.claude-code
    unstable.codex
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

  home.activation.seedCodexConfig = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    config="$HOME/.codex/config.toml"

    if [ ! -e "$config" ]; then
      mkdir -p "$(dirname "$config")"
      cp ${./codex/config.toml} "$config"
      chmod u+w "$config"
    fi
  '';

  home.activation.seedClaudeConfig = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    settings="$HOME/.claude/settings.json"
    statusline="$HOME/.claude/statusline-command.sh"

    if [ ! -e "$settings" ]; then
      mkdir -p "$(dirname "$settings")"
      cp ${./claude/settings.json} "$settings"
      chmod u+w "$settings"
    fi

    if [ ! -e "$statusline" ]; then
      mkdir -p "$(dirname "$statusline")"
      cp ${./claude/statusline-command.sh} "$statusline"
      chmod u+wx "$statusline"
    fi
  '';

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
