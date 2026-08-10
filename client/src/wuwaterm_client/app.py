"""Entry point wiring: PySide6 + qasync so httpx runs on the Qt event loop
and an in-flight request can be cancelled from the UI thread."""

from __future__ import annotations

import asyncio
import sys

import qasync
from PySide6.QtWidgets import QApplication

from . import strings
from .ui.main_window import MainWindow


SELF_CHECK_FLAG = "--self-check"


def run(argv: list[str] | None = None) -> int:
    arguments = list(argv if argv is not None else sys.argv)
    self_check = SELF_CHECK_FLAG in arguments
    if self_check:
        arguments = [item for item in arguments if item != SELF_CHECK_FLAG]

    app = QApplication(arguments)
    app.setApplicationName(strings.APP_TITLE)

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    window = MainWindow()

    if self_check:
        # Start-up rehearsal for a packaged build: everything a normal start
        # imports and constructs has now been imported and constructed, and
        # nothing has been shown, no credential has been requested and no
        # request has been sent. A frozen build that cannot import its own
        # package, or is missing a Qt plugin, fails before this point, which
        # is what makes the artifact testable without a human at the screen.
        window.close()
        loop.close()
        return 0

    if not window.ensure_credential():
        return 0
    window.show()

    app_close_event = asyncio.Event()
    app.aboutToQuit.connect(app_close_event.set)

    with loop:
        loop.run_until_complete(app_close_event.wait())
    return 0
