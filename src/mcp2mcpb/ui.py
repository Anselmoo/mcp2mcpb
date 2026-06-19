"""Terminal output for mcp2mcpb.

Five functions cover all output cases:
    info()    — informational progress line
    success() — completed step
    warning() — non-fatal issue
    error()   — fatal error (stderr)
    section() — visual separator between pipeline stages

This is the ONLY module permitted to call print() / write to stderr.

Glyphs degrade to ASCII on legacy Windows terminals (cmd.exe / old
PowerShell), where Unicode symbols may not render. Modern terminals
(Windows Terminal, iTerm2, etc.) get the Unicode glyphs.
"""

from __future__ import annotations

import shutil
import sys

_LEGACY_TERMINAL = sys.platform == "win32"


def _g(ascii_fallback: str, unicode_glyph: str) -> str:
    """Return the ASCII fallback on legacy Windows terminals, Unicode elsewhere."""
    return ascii_fallback if _LEGACY_TERMINAL else unicode_glyph


_ARROW = _g("->", "→")
_OK = _g("OK", "✔")
_WARN = _g("!", "▲")
_ERROR = _g("x", "✖")
_RULE = _g("-", "─")

_W = shutil.get_terminal_size().columns


def info(msg: str) -> None:
    """Print an informational progress line."""
    print(f"{_ARROW} {msg}")


def success(msg: str) -> None:
    """Print a step-completed line."""
    print(f"{_OK} {msg}")


def warning(msg: str) -> None:
    """Print a non-fatal warning."""
    print(f"{_WARN} {msg}")


def error(msg: str) -> None:
    """Print a fatal error to stderr."""
    print(f"{_ERROR} {msg}", file=sys.stderr)


def section(title: str) -> None:
    """Print a visual section separator with a left-aligned title."""
    if title:
        prefix = f"{_RULE * 3} {title} "
        padding = _RULE * max(0, _W - len(prefix))
        print(f"\n{prefix}{padding}")
    else:
        print("\n" + _RULE * _W)
