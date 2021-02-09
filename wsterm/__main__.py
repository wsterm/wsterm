# -*- coding: utf-8 -*-


import argparse
import asyncio
import logging
import sys
import urllib.parse

import tornado.ioloop

from . import client, server, utils


async def connect_server(url, workspace):
    cli = client.WSTerminalClient(url)
    await cli.connect()
    if workspace:
        print("Sync workspace to remote host...")
        await cli.sync_workspace(workspace)
        print("Sync workspace complete")
    await cli.create_shell((100, 40))


def main():
    parser = argparse.ArgumentParser(
        prog="wsterm", description="Websocket terminal tool."
    )
    parser.add_argument("--url", help="Websocket url", required=True)
    parser.add_argument(
        "--server", help="Run as websocket server", action="store_true", default=False
    )
    parser.add_argument("--token", help="Authorization token")
    parser.add_argument("--workspace", help="Workspace path")
    parser.add_argument(
        "--log-level",
        help="log level, default is info",
        choices=("debug", "info", "warn", "error"),
        default="info",
    )

    args = sys.argv[1:]
    if not args:
        parser.print_help()
        return 0

    args = parser.parse_args(args)

    handler = logging.StreamHandler()
    formatter = logging.Formatter("[%(asctime)s][%(levelname)s]%(message)s")
    handler.setFormatter(formatter)

    if args.log_level == "debug":
        utils.logger.setLevel(logging.DEBUG)
    elif args.log_level == "info":
        utils.logger.setLevel(logging.INFO)
    elif args.log_level == "warn":
        utils.logger.setLevel(logging.WARN)
    elif args.log_level == "error":
        utils.logger.setLevel(logging.ERROR)

    utils.logger.propagate = 0
    utils.logger.addHandler(handler)

    url = urllib.parse.urlparse(args.url)
    if url.scheme != "ws":
        raise ValueError("Invalid url protocol %s" % url.scheme)

    if args.server:
        host = url.hostname
        port = url.port or 80
        server.start_server((host, port), url.path)
    else:
        if sys.platform == "win32":
            utils.enable_native_ansi()
        asyncio.get_event_loop().run_until_complete(
            connect_server(args.url, args.workspace)
        )

    try:
        tornado.ioloop.IOLoop.current().start()
    except KeyboardInterrupt:
        print("Process exit warmly.")

if __name__ == "__main__":
    sys.exit(main())
