"""Server-held browser sessions for the owner-private web presentation layer.

The property this module exists to make structural, rather than to ask the
operator to maintain: NO CREDENTIAL EVER REACHES THE BROWSER. The device token
is read from the process environment and stays there. What the browser receives
is an opaque identifier with no derivable relationship to the token - a random
string that means nothing anywhere except in this process's memory, and only
for as long as the process lives.

Sessions are in-process and deliberately NOT persisted. Persisting them would
create a second on-disk store of credential-equivalent material next to the
device store, which is exactly the kind of thing ADR 0010 keeps to one place.
The cost is that restarting the service ends every session; for a single-owner
surface reached through an edge that already authenticates, re-establishing one
is a page reload.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass


# 32 bytes from the system CSPRNG, URL-safe encoded. The identifier is the
# entire bearer of authority for a browser request, so it is sized like a
# credential rather than like a database key.
_SESSION_ID_BYTES = 32


@dataclass(frozen=True)
class WebSession:
    """A live browser session bound to one device principal.

    ``device`` is the verified principal SNAPSHOT taken when the session was
    created, held as ``object`` so this module needs no import from the
    credential layer. It is a snapshot, so its revoked flag is stale by
    definition - callers re-check liveness against the store on every request
    and must not trust this field for that. What it is trusted for is the
    granted scope set, which cannot change for a live device: the store issues
    and revokes, and has no operation that edits scopes.
    """

    session_id: str
    device: object
    expires_at: float

    @property
    def device_id(self) -> str:
        return getattr(self.device, "device_id", "")


class SessionStore:
    """A bounded, in-memory map from opaque identifier to device principal.

    Expiry is measured on ``time.monotonic``, not wall clock: a session must not
    become valid again because the host's clock stepped backwards, and must not
    expire early because it stepped forwards. Monotonic time is immune to both,
    and the only thing it costs is that sessions cannot outlive the process -
    which is already true here, because the map is in memory.
    """

    def __init__(self, *, ttl_seconds: int, max_sessions: int) -> None:
        self._ttl = ttl_seconds
        self._max = max_sessions
        # Insertion-ordered, which is what makes the eviction below "oldest
        # first" without storing a separate ordering.
        self._sessions: dict[str, WebSession] = {}

    def create(self, device: object, *, now: float | None = None) -> WebSession:
        """Mint a session for an ALREADY-VERIFIED device principal.

        This function does not verify anything. It is called only after the
        caller has run the credential through the device store, and naming that
        here is the point: a future edit that starts calling ``create`` from
        somewhere that has not verified would be adding an authentication
        bypass, and there is no check inside this class that would catch it.
        """
        current = self._now(now)
        self._drop_expired(current)
        # Bound the map. Evicting the oldest live session rather than refusing
        # to create a new one keeps the owner able to log in from a second
        # device; refusing would turn a full map into a lockout, and the map
        # only fills at all if something has gone wrong.
        while len(self._sessions) >= self._max:
            self._sessions.pop(next(iter(self._sessions)))
        session = WebSession(
            session_id=secrets.token_urlsafe(_SESSION_ID_BYTES),
            device=device,
            expires_at=current + self._ttl,
        )
        self._sessions[session.session_id] = session
        return session

    def resolve(self, session_id: str | None, *, now: float | None = None):
        """Return the live session for an identifier, or None.

        Returns None for: absent, unknown, and expired. The caller cannot
        distinguish those three, and must not be able to - an unauthenticated
        caller learning that an identifier WAS valid but expired is a small
        oracle, and there is no reason to build it.
        """
        if not session_id:
            return None
        current = self._now(now)
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if session.expires_at <= current:
            # Drop on read as well as on create: a session that is never looked
            # at again should not be kept alive by the absence of traffic.
            self._sessions.pop(session_id, None)
            return None
        return session

    def discard(self, session_id: str | None) -> None:
        if session_id:
            self._sessions.pop(session_id, None)

    def clear(self) -> None:
        self._sessions.clear()

    def __len__(self) -> int:
        return len(self._sessions)

    def _drop_expired(self, now: float) -> None:
        expired = [
            key for key, value in self._sessions.items() if value.expires_at <= now
        ]
        for key in expired:
            self._sessions.pop(key, None)

    @staticmethod
    def _now(now: float | None) -> float:
        return time.monotonic() if now is None else now
