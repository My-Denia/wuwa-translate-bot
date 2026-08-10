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
import sys

from .auth import DeviceStore, DeviceStoreError
from .settings import ApiConfigError, ApiSettings, validate_loopback_bind


def _serve(args: argparse.Namespace) -> int:
    # A --host override goes through the SAME loopback guard the environment
    # bind does, before anything is built or bound: the override must not be a
    # hole that reopens the public-interface exposure the setting closes.
    host = validate_loopback_bind(args.host) if args.host is not None else None

    import uvicorn

    from .app import create_app

    settings = ApiSettings.from_env()
    store = DeviceStore(
        settings.device_db_path,
        guard_legacy_default=settings.device_db_is_default,
    )
    store.initialize()
    app = create_app(settings, device_store=store)
    uvicorn.run(
        app,
        host=host or settings.bind,
        port=args.port or settings.port,
        log_level=args.log_level,
        access_log=False,
        # A total in-flight ceiling so no request class can open unbounded
        # concurrent work on the shared worker pool. The bounded auth executor
        # sheds credential-verification load specifically; this is the coarser
        # bound over everything.
        limit_concurrency=settings.max_concurrent_requests,
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
