"""RestX theme configuration for terminal colors and styles.

Edit THEME values to customize the CLI appearance. Rich accepts color names
(for example ``grey50``, ``bold blue``) and hex codes (for example ``#E6E6FA``).

Lumo-inspired suggestions (light, airy palette):
  - Accent navy:    #003366  (status text, headers on light backgrounds)
  - Soft lavender:  #E6E6FA  (search highlight fill)
  - Muted grey:     #808080  (secondary help text)
  - Pale surface:   #F0F0F0  (optional app background tint)
"""

from __future__ import annotations

THEME: dict[str, str] = {
    # Status bar — Lumo-inspired dark navy on transparent background (#003366)
    "STATUS_TEXT": "#003366",
    "STATUS_SEPARATOR": "#e8e8e8",
    # Search match highlight — light lavender background (#E6E6FA), dark text
    "SEARCH_HIGHLIGHT_BG": "#E6E6FA",
    "SEARCH_HIGHLIGHT_FG": "#333333",
    # Help screen — bold blue commands, grey descriptions, bold white headers
    "HELP_HEADER": "bold white",
    "HELP_COMMAND": "bold blue",
    "HELP_DESC": "grey50",
    # HTTP method colors (readable on light terminal backgrounds)
    "METHOD_GET": "dark_green",
    "METHOD_POST": "gold1",
    "METHOD_PUT": "blue",
    "METHOD_DELETE": "red",
    "METHOD_PATCH": "purple",
    "PATH": "dark_cyan",
    # General text
    "TEXT": "default",
    "TEXT_MUTED": "grey50",
    "PARAM_NAME": "bold",
    "PAGER_SELECTED_BG": "#E8E8E8",
    "PAGER_FOOTER": "grey50",
}
