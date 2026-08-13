"""Non-secret client settings, persisted as JSON under the per-user app data
directory.

The device token is never stored here, and never held by this module at
all: see credentials.py, which is the only place that touches it and keeps
it exclusively in the OS credential store.
"""

from __future__ import annotations

import dataclasses
import ipaddress
import json
import math
import os
import tempfile
import urllib.parse
from pathlib import Path

import httpx

APP_DIR_NAME = "WuwaTerm"
CONFIG_FILE_NAME = "config.json"

# There is deliberately NO default server address. See ClientConfig.base_url.
DEFAULT_REQUEST_TIMEOUT_SECONDS = 10.0
DEFAULT_TRANSLATE_TIMEOUT_SECONDS = 60.0
# The same bounds the Settings dialog enforces. A hand-edited config file is
# the only way a value outside them can arrive, and "never raises" must not
# mean "passes a zero or a string straight into the HTTP client".
MIN_TIMEOUT_SECONDS = 1.0
MAX_TIMEOUT_SECONDS = 600.0

# Appearance is a three-state preference, not a boolean: "follow the system"
# is a distinct choice from either fixed value, and the owner has to be able
# to pin one when the system setting is not what they want in front of them.
# The same three literals appear in theme.py, which deliberately imports
# nothing from here - see the note there, and the test that compares them.
APPEARANCE_SYSTEM = "system"
APPEARANCE_LIGHT = "light"
APPEARANCE_DARK = "dark"
APPEARANCE_VALUES = (APPEARANCE_SYSTEM, APPEARANCE_LIGHT, APPEARANCE_DARK)
DEFAULT_APPEARANCE = APPEARANCE_SYSTEM


def app_data_dir() -> Path:
    """Per-user app data directory. ``%APPDATA%/WuwaTerm`` on Windows.

    ``%APPDATA%`` is the ROAMING profile (``…/AppData/Roaming``), which is
    where a setting that has to outlive a restart belongs. It is deliberately
    neither ``%LOCALAPPDATA%/Temp`` nor anything else a disk-cleanup tool
    treats as disposable: this file is the only record of which server the
    client talks to, and losing it now costs the owner that address rather
    than being papered over by a fallback.
    """
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / APP_DIR_NAME
    return Path.home() / ".config" / APP_DIR_NAME


def config_path(base_dir: Path | None = None) -> Path:
    return (base_dir if base_dir is not None else app_data_dir()) / CONFIG_FILE_NAME


def _sane_timeout(value: object, fallback: float) -> float:
    """A timeout from disk, clamped, or the default if it is not a number.

    Python's JSON parser accepts `NaN` and `Infinity`, and `min`/`max` pass
    NaN straight through, so a non-finite value would reach httpx as a
    deadline that never expires. Finiteness is checked, not assumed.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return fallback
    number = float(value)
    if not math.isfinite(number):
        return fallback
    return float(min(max(number, MIN_TIMEOUT_SECONDS), MAX_TIMEOUT_SECONDS))


def _is_loopback(hostname: str) -> bool:
    host = hostname.strip("[]").lower()
    if host in ("localhost", "::1"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def endpoint_is_confidential(value: object) -> bool:
    """Whether requests sent to `value` are protected in transit.

    True for `https://` (the client always verifies the server certificate,
    see api.py) and for plain `http://` to this machine's own loopback
    address, where the bytes never leave the host. False for everything
    else, including an address this function cannot parse: the client
    refuses what it cannot show to be safe rather than assuming it is.

    This is the whole of the client's transport-confidentiality policy, and
    it is deliberately independent of how the network path is arranged. The
    device token travels in a request header on every call, so an address
    that carries it in the clear to another machine is refused wherever it
    arrives from - the settings field, a hand-edited config file, or a
    caller constructing the transport directly.
    """
    if not isinstance(value, str):
        return False
    try:
        parsed = urllib.parse.urlsplit(value.strip())
    except ValueError:
        return False
    if not parsed.hostname:
        return False
    if parsed.scheme == "https":
        return True
    if parsed.scheme == "http":
        return _is_loopback(parsed.hostname)
    return False


def usable_base_url(value: object) -> bool:
    """Whether an address can actually be used to reach the service.

    A plain text field accepts `http://127.0.0.1:notaport` happily, and the
    result is a setting that fails every request until someone works out that
    the setting itself is wrong. Checked once, where it is entered and where
    it is read back from disk.
    """
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    if not candidate:
        return False
    try:
        parsed = urllib.parse.urlsplit(candidate)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        parsed.port
    except ValueError:
        return False
    # Plain HTTP is only acceptable to this machine. Any other host must be
    # reached over the configured secure endpoint (https, certificate
    # verified); anything else carries the bearer credential over the wire in
    # the clear, and a mistyped or hand-edited address is exactly how that
    # happens by accident.
    if not endpoint_is_confidential(candidate):
        return False
    # A base address is a scheme, a host, a port and an optional path prefix.
    # A query or fragment on it is silently dropped when a request path is
    # joined onto it, so accepting one stores a setting that does not mean
    # what it says.
    if parsed.query or parsed.fragment:
        return False
    # Credentials do not belong in a stored address. This client authenticates
    # with a device token held in the credential store, and a username or
    # password embedded here would be written to a plain JSON file and sent as
    # a second, unmanaged credential.
    if parsed.username is not None or parsed.password is not None:
        return False
    # Last: hand it to the parser the client actually uses. urlsplit discards
    # an embedded control character silently, and httpx then refuses the same
    # string - which would have meant a saved address that prevents the
    # application from starting until the file is repaired by hand.
    try:
        httpx.URL(candidate)
    except (httpx.InvalidURL, ValueError, UnicodeError):
        return False
    return True


def _stored_appearance(value: object) -> str:
    """A stored appearance preference, or the default if it is not one of the
    three the application knows.

    A hand-edited file is the only way an unknown value can arrive, and the
    answer is the same one the rest of this module gives: fall back rather
    than carry something unusable into the UI. Unlike the address, falling
    back here costs nothing - it is a matter of taste, and the client can
    pick for itself.
    """
    if isinstance(value, str) and value in APPEARANCE_VALUES:
        return value
    return DEFAULT_APPEARANCE


def _stored_base_url(value: object) -> str | None:
    """A stored address, or None when there is not a usable one.

    Annotations are not runtime validation: a list here would reach httpx.
    What is NOT here is a fallback. This used to substitute a development
    address on this machine for anything it could not accept, and the result
    was the worst kind of failure - a client that looked configured, pointed
    at a port nothing was listening on, and reported "could not reach the
    server" for a configuration problem. A missing or unusable address is now
    reported as exactly that.
    """
    if not usable_base_url(value):
        return None
    return value.strip()


@dataclasses.dataclass(frozen=True)
class ClientConfig:
    """Non-secret client settings.

    ``base_url`` is ``None`` when this client has no server address it can
    use: the configuration file is missing, unreadable, malformed, or the
    address it holds is not one this client will send a device token to.
    That is the UNCONFIGURED state, and it is explicit on purpose - the file
    really did go missing on the owner's machine once (a reboot, an external
    cleanup tool; the credential in the OS store survived), and the silent
    substitution of a local development address turned "your setting is gone"
    into "the server is down".
    """

    base_url: str | None = None
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS
    translate_timeout_seconds: float = DEFAULT_TRANSLATE_TIMEOUT_SECONDS
    appearance: str = DEFAULT_APPEARANCE

    @property
    def is_configured(self) -> bool:
        """Whether this configuration names a server address to talk to."""
        return self.base_url is not None

    @classmethod
    def load(cls, base_dir: Path | None = None) -> "ClientConfig":
        """Load from disk. Never raises.

        Timeouts and the appearance preference fall back to their defaults,
        which are a matter of taste and which the client can pick for
        itself. The address does not: anything
        missing, unreadable, malformed or unusable leaves ``base_url`` as
        ``None`` and the client unconfigured.

        The read is ATTEMPTED rather than preceded by an existence check. A
        `path.exists()` in front of it was a second way to touch the file
        system, outside this try, and `stat` fails for reasons of its own -
        a denying ACL, an unreadable parent, an I/O error - none of which are
        "the file is not there". The exception escaped, and a client whose
        contract is "never raises, fall back to unconfigured" instead failed
        to start at all. Asking for the bytes answers both questions at once:
        `FileNotFoundError` and `PermissionError` are both `OSError`, and
        both mean this launch has no usable configuration.
        """
        path = config_path(base_dir)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        known_fields = {field.name for field in dataclasses.fields(cls)}
        filtered = {key: value for key, value in raw.items() if key in known_fields}
        defaults = cls()
        for name, fallback in (
            ("request_timeout_seconds", defaults.request_timeout_seconds),
            ("translate_timeout_seconds", defaults.translate_timeout_seconds),
        ):
            if name in filtered:
                filtered[name] = _sane_timeout(filtered[name], fallback)
        if "base_url" in filtered:
            filtered["base_url"] = _stored_base_url(filtered["base_url"])
        if "appearance" in filtered:
            filtered["appearance"] = _stored_appearance(filtered["appearance"])
        try:
            return cls(**filtered)
        except TypeError:
            return cls()

    def save(self, base_dir: Path | None = None) -> None:
        """Write the settings to disk atomically.

        The bytes go to a temporary file in the SAME directory, are flushed
        to the device, and only then replace the target in one ``os.replace``
        call. What this buys: the target is never opened for truncation, so
        a crash, a power loss or a full disk part-way through a save cannot
        leave a truncated ``config.json`` behind. ``load`` would read that as
        malformed - and a malformed file now costs the owner their server
        address rather than being papered over by a fallback.

        What it does NOT claim: crash-ordering guarantees. There is no
        parent-directory fsync (a directory cannot be opened for one on
        Windows), so this bounds what a HALF-FINISHED WRITE can leave on
        disk, not what survives a power cut at an arbitrary instant.
        """
        path = config_path(base_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = dataclasses.asdict(self)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{CONFIG_FILE_NAME}.", dir=path.parent
        )
        try:
            # `mkstemp` returns a RAW descriptor, and `fdopen` is what takes
            # ownership of it - so a failure IN `fdopen` leaves the descriptor
            # open and owned by nobody. The cleanup below would then unlink a
            # file this process still holds a handle to, which on Windows
            # fails and leaks both. Ownership is transferred or the descriptor
            # is closed here; there is no third outcome.
            try:
                handle = os.fdopen(descriptor, "w", encoding="utf-8")
            except BaseException:
                os.close(descriptor)
                raise
            with handle:
                handle.write(json.dumps(payload, indent=2, sort_keys=True))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except BaseException:
            # A half-written temporary file left behind would accumulate in
            # the owner's profile, one per failed save.
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
