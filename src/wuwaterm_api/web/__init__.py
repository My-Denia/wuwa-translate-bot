"""Owner-private web presentation layer, mounted inside the API process.

Deliberately empty of re-exports. ``create_web_app`` is imported from
``.app`` at the point of use, inside ``wuwaterm_api.app.create_app``, because
that module imports this package - pulling the symbol up to here would turn a
one-directional lazy import into a cycle at package import time.
"""
