"""Pydantic v2 domain models for mcp2mcpb.

Responsibilities:
    - Define every data shape used across the pipeline
    - Validate all data at the boundary (never inside pipeline functions)
    - Provide serialisation to JSON via model_dump_json()
    - Expose StrEnum constants shared with Typer CLI declarations

Design rules:
    - All models use ConfigDict(frozen=True) unless they need mutation
    - All fields have explicit types — never bare `Any`
    - Validators use @field_validator + @classmethod, never @validator
    - model_config always declared first in the class body
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ── Constants (StrEnum — works as both Pydantic field type and Typer option) ──


class Registry(StrEnum):
    PYPI = "pypi"
    NPM = "npm"


class ServerType(StrEnum):
    NODE = "node"
    PYTHON = "python"
    BINARY = "binary"
    UV = "uv"


class BundleMode(StrEnum):
    REFERENCE = "reference"
    COMPLETE = "complete"


class Runner(StrEnum):
    UVX = "uvx"
    NPX = "npx"
    UV_RUN = "uv-run"
    PYTHON = "python"
    NODE = "node"


class Transport(StrEnum):
    STDIO = "stdio"
    NONE = "none"
    AUTO = "auto"


# ── Input models ──────────────────────────────────────────────────────────────


class PackageSource(BaseModel):
    """Fully-qualified package reference from a specific registry."""

    model_config = ConfigDict(frozen=True)

    registry: Registry
    name: str
    version: str | None = None  # None → resolve latest at fetch time

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("package name must not be blank")
        return stripped

    @property
    def pinned(self) -> str:
        """Return the pinned package specifier for the installer CLI.

        Examples:
            pypi: 'mcp-server-fetch==1.2.0' or 'mcp-server-fetch'
            npm:  '@scope/pkg@2.0.0' or '@scope/pkg'
        """
        if self.registry == Registry.PYPI:
            return f"{self.name}=={self.version}" if self.version else self.name
        return f"{self.name}@{self.version}" if self.version else self.name


# ── Launch recipe models ──────────────────────────────────────────────────────


class LaunchSpec(BaseModel):
    """Fully-resolved description of how to launch the MCP server."""

    model_config = ConfigDict(frozen=True)

    runner: Runner
    entry_script: str | None = None
    extras: list[str] = Field(default_factory=list)
    subcommand: list[str] = Field(default_factory=list)
    transport: Transport = Transport.AUTO
    # Tri-state control over uvx's `--from`: None = auto-derive, True = force,
    # False = bare `uv tool run <pkg>`.
    from_spec: bool | None = None


class LaunchOverrides(BaseModel):
    """Partial launch recipe from one source; None means 'not set'."""

    model_config = ConfigDict(frozen=True)

    runner: Runner | None = None
    entry_script: str | None = None
    extras: list[str] | None = None
    subcommand: list[str] | None = None
    transport: Transport | None = None
    from_spec: bool | None = None


# ── Pipeline intermediate models ──────────────────────────────────────────────


class EntryPoint(BaseModel):
    """Normalised server entry point, resolved from actual package contents."""

    model_config = ConfigDict(frozen=True)

    command: str
    args: list[str]
    entry_file: str  # Relative path inside bundle, e.g. "server/index.js"


class PackageMeta(BaseModel):
    """Normalised package metadata after registry fetch + entry inspection."""

    model_config = ConfigDict(frozen=True)

    name: str
    version: str
    description: str
    author: str
    homepage: str | None = None
    license_id: str | None = None
    server_type: ServerType
    entry: EntryPoint
    detected_env_vars: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    repository_url: str | None = None
    requires_python: str | None = None
    node_engine: str | None = None


# ── Manifest models (mcpb spec v0.4) ──────────────────────────────────────────


class ManifestAuthor(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str


class RepositoryRef(BaseModel):
    """Source repository reference for the manifest."""

    model_config = ConfigDict(frozen=True)

    type: str = "git"
    url: str


class Compatibility(BaseModel):
    """Platform/runtime compatibility hints for MCPB clients.

    ``claude_desktop`` is intentionally omitted: we have no reliable basis to
    assert a minimum client version from registry metadata. Authors can add it
    via in-package config.
    """

    model_config = ConfigDict(frozen=True)

    platforms: list[str] = Field(default_factory=list)
    runtimes: dict[str, str] = Field(default_factory=dict)


class UserConfigField(BaseModel):
    """A user-configurable field shown at install time in Claude Desktop."""

    model_config = ConfigDict(frozen=True)

    type: Literal["string", "number", "boolean"] = "string"
    title: str
    description: str
    sensitive: bool = False
    required: bool = True


class McpConfig(BaseModel):
    """The command Claude uses to launch the MCP server process."""

    model_config = ConfigDict(frozen=True)

    command: str
    args: list[str]
    env: dict[str, str] = Field(default_factory=dict)


class ServerConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: ServerType
    entry_point: str
    mcp_config: McpConfig


class Manifest(BaseModel):
    """Root manifest.json model per MCPB spec v0.4."""

    model_config = ConfigDict(frozen=True)

    manifest_version: str = "0.4"
    name: str
    display_name: str | None = None
    version: str
    description: str
    author: ManifestAuthor
    repository: RepositoryRef | None = None
    homepage: str | None = None
    license: str | None = None
    keywords: list[str] = Field(default_factory=list)
    server: ServerConfig
    user_config: dict[str, UserConfigField] = Field(default_factory=dict)
    compatibility: Compatibility | None = None

    def write_to(self, path: Path) -> None:
        """Serialise to manifest.json at the given path."""
        path.write_text(
            self.model_dump_json(indent=2, exclude_none=True),
            encoding="utf-8",
        )
