"""Owner-only Windows desktop client for the wuwaterm HTTP API.

This package only calls the API and renders what comes back: dictionary
lookup, direction detection, and every other translation pipeline step live
solely in the service it talks to. Never published to any package index; see
pyproject.toml.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
