local wezterm = require("wezterm")

local config = wezterm.config_builder()

config.font = wezterm.font("Hack Nerd Font")
config.font_size = 15.0
config.window_background_opacity = 0.95

-- GNOME/Mutter does not implement zxdg_decoration_manager_v1, so the default
-- "TITLE | RESIZE" leaves WezTerm waiting for a frame the compositor never draws.
-- "TITLE" makes WezTerm draw its own client-side decorations instead.
config.window_decorations = "TITLE"

return config
