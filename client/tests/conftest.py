"""Shared pytest setup.

Sets QT_QPA_PLATFORM=offscreen before any PySide6 import so Qt-constructing
tests run headlessly, without a real display. Only the tests that actually
import PySide6 pay this cost; the rest of the suite (api, config,
credentials, errors, and the static strings-source check) never imports Qt
and needs no event loop at all.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
