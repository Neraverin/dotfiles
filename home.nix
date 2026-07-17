{ pkgs, unstable, ... }:

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

  home.file.".codex/config.toml" = {
    force = true;
    text = ''
      approval_policy = "never"
      sandbox_mode = "danger-full-access"

      [projects."/home/neraverin/sources/dotfiles"]
      trust_level = "trusted"
    '';
  };

  home.file.".claude/settings.json" = {
    force = true;
    text = builtins.toJSON {
      "$schema" = "https://json.schemastore.org/claude-code-settings.json";
      theme = "dark";
      model = "sonnet[1m]";
      permissions = {
        defaultMode = "bypassPermissions";
        skipDangerousModePermissionPrompt = true;
      };
      statusLine = {
        type = "command";
        command = "bash /home/neraverin/.claude/statusline-command.sh";
      };
    };
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
