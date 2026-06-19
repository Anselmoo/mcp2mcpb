"""Domain exception hierarchy for mcp2mcpb."""

from __future__ import annotations


class McpbConversionError(RuntimeError):
    """Base class for all converter domain errors.

    Raised when a conversion step fails in an expected way that can be
    shown to the user with an actionable message. Unexpected errors
    (programming errors, permission issues) propagate as-is.
    """


class RegistryFetchError(McpbConversionError):
    """Could not fetch metadata from PyPI or npm registry."""


class EntryPointError(McpbConversionError):
    """Could not detect a usable entry point from the package."""


class BundleError(McpbConversionError):
    """Dependency installation or file bundling failed."""


class PackError(McpbConversionError):
    """Could not produce the final .mcpb ZIP archive."""


class ConfigError(McpbConversionError):
    """A [tool.mcpb]/.mcpb.toml config value was invalid."""
