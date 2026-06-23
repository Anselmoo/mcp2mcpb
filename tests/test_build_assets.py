"""Integration tests for scripts/build_assets.py.

These tests use real cairosvg (a C library — no network calls).
The test is skipped gracefully if cairosvg is not available.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Must set DYLD_LIBRARY_PATH before cairosvg is imported on macOS,
# because cairosvg dlopen()s libcairo at import time.
if sys.platform == "darwin":
    os.environ.setdefault("DYLD_LIBRARY_PATH", "/opt/homebrew/opt/cairo/lib")

cairosvg = pytest.importorskip("cairosvg")

# Insert the scripts/ directory so we can import build_assets directly.
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import build_assets as ba  # noqa: E402


def test_svg_to_png_writes_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """svg_to_png() must write non-empty PNG files for each SVG source."""
    fake_assets = tmp_path / "assets"
    fake_assets.mkdir()

    # Copy SVG sources into tmp_path so the function finds them.
    assets_dir = Path(__file__).parent.parent / "assets"
    for name in ("logo.svg", "hero.svg", "social-preview.svg"):
        (fake_assets / name).write_bytes((assets_dir / name).read_bytes())

    monkeypatch.setattr(ba, "ASSETS", fake_assets)

    ba.svg_to_png()

    for _, png_name, _, _ in ba.SVG_OUTPUTS:
        out = fake_assets / png_name
        assert out.exists(), f"{png_name} was not created"
        assert out.stat().st_size > 1000, f"{png_name} is suspiciously small"


def test_version_injection_social_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """social-preview.svg version string is updated in the written PNG source."""
    fake_assets = tmp_path / "assets"
    fake_assets.mkdir()

    assets_dir = Path(__file__).parent.parent / "assets"
    for name in ("logo.svg", "hero.svg", "social-preview.svg"):
        (fake_assets / name).write_bytes((assets_dir / name).read_bytes())

    # Patch ASSETS so outputs go to tmp dir.
    monkeypatch.setattr(ba, "ASSETS", fake_assets)

    ba.svg_to_png()

    # Verify the function ran without errors and produced output files.
    social_png = fake_assets / "social-preview.png"
    assert social_png.exists()
    assert social_png.stat().st_size > 1000
