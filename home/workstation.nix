{ config, pkgs, ... }:

let
  # WezTerm links against Nix's libglvnd, which looks for EGL vendor files under
  # /run/opengl-driver (a NixOS-only path). On Ubuntu that lookup fails, eglGetDisplay
  # returns no display and no window is ever created. Prepending the host library
  # directory lets it use the distro's libglvnd/Mesa; scoped to this package so the
  # rest of the profile keeps using Nix libraries.
  wezterm-host-gl = pkgs.symlinkJoin {
    name = "wezterm-host-gl";
    paths = [ pkgs.wezterm ];
    nativeBuildInputs = [ pkgs.makeWrapper ];
    postBuild = ''
      for bin in wezterm wezterm-gui wezterm-mux-server; do
        wrapProgram $out/bin/$bin --prefix LD_LIBRARY_PATH : /usr/lib/x86_64-linux-gnu
      done
    '';
  };

  weztermDesktopItem = pkgs.makeDesktopItem {
    name = "org.wezfurlong.wezterm";
    desktopName = "WezTerm";
    genericName = "Terminal Emulator";
    comment = "Wez's Terminal Emulator";
    icon = "${wezterm-host-gl}/share/icons/hicolor/128x128/apps/org.wezfurlong.wezterm.png";
    exec = "${wezterm-host-gl}/bin/wezterm start --cwd .";
    terminal = false;
    categories = [ "System" "TerminalEmulator" "Utility" ];
    keywords = [ "shell" "prompt" "command" "commandline" "cmd" ];
    startupWMClass = "org.wezfurlong.wezterm";
  };
in
{
  imports = [ ./common.nix ];

  # Packages that only make sense on a graphical workstation.
  # Everything from common.nix is inherited and merged with this list.
  home.packages = [
    wezterm-host-gl
  ];

  # gnome-shell's environment has neither ~/.nix-profile/bin nor ~/.nix-profile/share,
  # so the entry shipped inside the package is invisible to the app grid and its
  # TryExec would not resolve. xdg.desktopEntries would not help either: Home Manager
  # installs those into the Nix profile too. Link our own entry, with absolute paths,
  # into ~/.local/share/applications, which XDG always searches.
  home.file.".local/share/applications/org.wezfurlong.wezterm.desktop".source =
    "${weztermDesktopItem}/share/applications/org.wezfurlong.wezterm.desktop";

  # gnome-shell and every app it launches inherit the systemd user manager's
  # environment, which knows nothing about the Nix profile. Extend it so desktop
  # entries and binaries shipped by Nix packages are found without absolute paths.
  # Takes effect on the next login.
  systemd.user.sessionVariables.XDG_DATA_DIRS =
    "${config.home.profileDirectory}/share:/nix/var/nix/profiles/default/share:\${XDG_DATA_DIRS}";

  # PATH cannot go into the block above: Home Manager writes it to
  # environment.d/10-home-manager.conf, and Ubuntu's environment.d/99-environment.conf
  # (a link to /etc/environment) later overwrites PATH wholesale. Sort after it.
  xdg.configFile."environment.d/99z-nix-path.conf".text =
    "PATH=${config.home.profileDirectory}/bin:${config.home.homeDirectory}/.local/bin:\${PATH}\n";

  # GNOME: switch the input source with Shift+Alt and back with Alt+Shift.
  dconf.settings."org/gnome/desktop/wm/keybindings" = {
    switch-input-source = [ "<Shift>Alt_L" ];
    switch-input-source-backward = [ "<Alt>Shift_L" ];
  };
}
