"""Operator entry point for the HTTP adapter.

    wuwaterm-api serve
    wuwaterm-api device issue --name "owner laptop" [--scopes translate,meta]
    wuwaterm-api device list
    wuwaterm-api device revoke --device-id <id>

There is deliberately no self-service registration route: device tokens are
created by an operator with shell access (over SSH, typically as
``docker compose run --rm wuwaterm-api device issue --name ...``) and shown
exactly once.
"""

from __future__ import annotations

import argparse
import sys

from .auth import DeviceStore, DeviceStoreError
from .settings import ApiConfigError, ApiSettings


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .app import create_app

    settings = ApiSettings.from_env()
    store = DeviceStore(settings.device_db_path)
    store.initialize()
    app = create_app(settings, device_store=store)
    uvicorn.run(
        app,
        host=args.host or settings.bind,
        port=args.port or settings.port,
        log_level=args.log_level,
        access_log=False,
    )
    return 0


def _device_issue(args: argparse.Namespace) -> int:
    settings = ApiSettings.from_env()
    store = DeviceStore(settings.device_db_path)
    device, token = store.issue(args.name, args.scopes)
    print(f"device_id: {device.device_id}")
    print(f"device_name: {device.device_name}")
    print(f"scopes: {','.join(device.scopes)}")
    print(f"created_at: {device.created_at}")
    print("token (shown once, store it in the OS credential manager):")
    # Written straight to the terminal rather than through a logging-shaped
    # call: the operator has to see the secret exactly once, and it must not
    # travel through anything that could be captured, formatted or persisted.
    sys.stdout.write(token)
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 0


def _device_list(args: argparse.Namespace) -> int:
    settings = ApiSettings.from_env()
    store = DeviceStore(settings.device_db_path)
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
    store = DeviceStore(settings.device_db_path)
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

    issue = device_sub.add_parser("issue", help="create a device and print its token")
    issue.add_argument("--name", required=True, help="human label for the device")
    issue.add_argument(
        "--scopes",
        default=None,
        help="comma separated scopes (default: translate,meta)",
    )
    issue.set_defaults(func=_device_issue)

    listing = device_sub.add_parser("list", help="list devices (never prints tokens)")
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
