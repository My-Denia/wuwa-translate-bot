"""Operator entry point for the HTTP adapter.

    wuwaterm-api serve
    wuwaterm-api device issue --name "owner laptop" [--scopes translate,meta]
    wuwaterm-api device list
    wuwaterm-api device revoke --device-id <id>

There is deliberately no self-service registration route: devices are
registered by an operator with shell access on the host, over SSH.

``device issue`` reads the secret from standard input and never prints it.
Nothing in this service emits a credential, so no credential can reach a log,
a terminal recording or a captured command output through it. The operator
generates the secret where it will be stored, pastes or pipes it in, and keeps
it in the OS credential manager on the client machine. The command prints the
device id, which is not a secret, and the token is simply
``wtd1.<device_id>.<that secret>``.
"""

from __future__ import annotations

import argparse
import logging
import sys

from .auth import DeviceStore, DeviceStoreError
from .settings import (
    ApiConfigError,
    ApiSettings,
    validate_loopback_bind,
    validate_port,
)

LOGGER = logging.getLogger("wuwaterm_api")


def _resolve_bind(args: argparse.Namespace, settings: ApiSettings) -> str:
    """The address to bind, with --host as a deliberate escape hatch.

    `--host` is how an operator recovers a machine whose environment carries a
    bind this service refuses: the override is validated on its own and the
    configured value is never consulted, so a bad `WUWATERM_API_BIND` cannot
    keep the service down. That escape hatch is only safe because the override
    goes through the SAME guard, so it can relax nothing.

    When the override replaces a value that would have been REFUSED, say so at
    WARNING: silently ignoring a configured bind is how an operator ends up
    believing the environment took effect. The offending value is not echoed —
    settings never reflect a raw environment value back.
    """
    if args.host is None:
        return validate_loopback_bind(settings.bind)
    host = validate_loopback_bind(args.host)
    try:
        validate_loopback_bind(settings.bind)
    except ApiConfigError:
        LOGGER.warning(
            "--host overrides the configured API bind, which is not a numeric "
            "loopback address and would have been refused; the configured "
            "value is ignored for this run"
        )
    return host


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .app import create_app

    settings = ApiSettings.from_env()
    # The loopback guard lives HERE, on the only path that binds a socket — not
    # in from_env, which every operator subcommand calls: `device revoke` must
    # never be blocked by serve-time network configuration. Both the configured
    # bind and a --host override go through the same check, before anything is
    # built or bound, so the override cannot reopen the exposure the setting
    # closes. ApiConfigError propagates to main() -> exit 2.
    host = _resolve_bind(args, settings)
    # Same class for the port: the environment variable is range-checked in
    # settings, so the override is too. `args.port or settings.port` used to
    # send 999999 and -1 straight to uvicorn and to swallow an explicit 0.
    port = settings.port if args.port is None else validate_port(args.port)
    store = DeviceStore(
        settings.device_db_path,
        guard_legacy_default=settings.device_db_is_default,
    )
    store.initialize()
    app = create_app(settings, device_store=store)
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=args.log_level,
        access_log=False,
    )
    return 0


def _read_secret(stream) -> str:
    """Read the operator-supplied secret without ever echoing it."""
    if stream.isatty():
        import getpass

        return getpass.getpass("device secret (input hidden): ")
    return stream.readline().rstrip("\r\n")


def _device_issue(args: argparse.Namespace) -> int:
    settings = ApiSettings.from_env()
    store = DeviceStore(
        settings.device_db_path,
        guard_legacy_default=settings.device_db_is_default,
    )
    device = store.issue(args.name, args.scopes, secret=_read_secret(sys.stdin))
    print(f"device_id: {device.device_id}")
    print(f"device_name: {device.device_name}")
    print(f"scopes: {','.join(device.scopes)}")
    print(f"created_at: {device.created_at}")
    print(
        "registered. The token is wtd1.<device_id>.<the secret you supplied>;"
        " this service never prints it."
    )
    return 0


def _device_list(args: argparse.Namespace) -> int:
    settings = ApiSettings.from_env()
    store = DeviceStore(
        settings.device_db_path,
        guard_legacy_default=settings.device_db_is_default,
    )
    devices = store.list_devices()
    if not devices:
        print("no devices")
        return 0
    for device in devices:
        state = f"revoked {device.revoked_at}" if device.revoked else "active"
        print(
            f"{device.device_id}  {state}  scopes={','.join(device.scopes)}  "
            f"created={device.created_at}  last_used={device.last_used_at or '-'}  "
            f"name={device.device_name}"
        )
    return 0


def _device_revoke(args: argparse.Namespace) -> int:
    settings = ApiSettings.from_env()
    store = DeviceStore(
        settings.device_db_path,
        guard_legacy_default=settings.device_db_is_default,
    )
    device = store.revoke(args.device_id)
    print(f"revoked {device.device_id} at {device.revoked_at}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wuwaterm-api", description="wuwaterm HTTP adapter"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the HTTP server")
    serve.add_argument("--host", default=None, help="override WUWATERM_API_BIND")
    serve.add_argument("--port", type=int, default=None, help="override WUWATERM_API_PORT")
    serve.add_argument("--log-level", default="info")
    serve.set_defaults(func=_serve)

    device = sub.add_parser("device", help="manage device credentials")
    device_sub = device.add_subparsers(dest="device_command", required=True)

    issue = device_sub.add_parser(
        "issue",
        help="register a device for a secret read from standard input",
    )
    issue.add_argument("--name", required=True, help="human label for the device")
    issue.add_argument(
        "--scopes",
        default=None,
        help="comma separated scopes (default: translate,meta)",
    )
    issue.set_defaults(func=_device_issue)

    listing = device_sub.add_parser(
        "list", help="list devices (no credential material is ever printed)"
    )
    listing.set_defaults(func=_device_list)

    revoke = device_sub.add_parser("revoke", help="revoke a device by id")
    revoke.add_argument("--device-id", required=True)
    revoke.set_defaults(func=_device_revoke)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (DeviceStoreError, ApiConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
