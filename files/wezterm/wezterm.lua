local wezterm = require("wezterm")

local config = wezterm.config_builder()

config.font = wezterm.font("Hack Nerd Font")
config.font_size = 15.0
config.window_background_opacity = 0.95

-- GNOME/Mutter does not implement zxdg_decoration_manager_v1, so the default
-- "TITLE | RESIZE" leaves WezTerm waiting for a frame the compositor never draws.
-- "TITLE" makes WezTerm draw its own client-side decorations instead.
config.window_decorations = "TITLE"

-- Sized in terminal cells, not pixels: changing font_size above moves the
-- window size with it, so these two want revisiting together.
config.initial_cols = 167
config.initial_rows = 46

return config
