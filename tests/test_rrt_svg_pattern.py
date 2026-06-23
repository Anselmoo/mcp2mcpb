"""Verify that the rrt version pattern matches exactly once in each SVG asset."""

import re
import tomllib
from pathlib import Path

SVG_VERSION_PATTERN = re.compile(r'(fill="#F0FDFA">v)(\d+\.\d+\.\d+)(</text>)')


def _current_version() -> str:
    with open("pyproject.toml", "rb") as f:
        return tomllib.load(f)["project"]["version"]


def test_social_preview_has_exactly_one_version_match() -> None:
    content = Path("assets/social-preview.svg").read_text()
    matches = SVG_VERSION_PATTERN.findall(content)
    assert len(matches) == 1, f"expected 1 match, got {matches}"
    assert matches[0][1] == _current_version()


def test_hero_has_exactly_one_version_match() -> None:
    content = Path("assets/hero.svg").read_text()
    matches = SVG_VERSION_PATTERN.findall(content)
    assert len(matches) == 1, f"expected 1 match, got {matches}"
    assert matches[0][1] == _current_version()
