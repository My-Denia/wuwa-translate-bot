"""Entry script for the packaged (PyInstaller) build.

PyInstaller runs its entry script as a top-level module named ``__main__``
with no package context, so a module that reaches its siblings through
relative imports cannot be the entry point: the frozen program raises
``ImportError: attempted relative import with no known parent package``
before a single window appears. ``wuwaterm_client/__main__.py`` is exactly
such a module and stays as it is, because it is what ``python -m
wuwaterm_client`` needs.

This file is the packaging entry instead. It imports absolutely, which works
both frozen and from a source checkout.
"""

from __future__ import annotations

import sys

from wuwaterm_client.app import run

if __name__ == "__main__":
    sys.exit(run())
