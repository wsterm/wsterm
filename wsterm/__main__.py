# -*- coding: utf-8 -*-


import argparse
import asyncio
import logging
import logging.handlers
import os
import shutil
import sys
import traceback
import urllib.parse

import tornado.ioloop

from . import client, server, utils


async def connect_server(url, workspace, token=None):
    print("Connecting to remote terminal %s" % url)
    cli = client.WSTerminalClient(url, token=token)
    if not await cli.connect():
        print("Connect websocket server failed", file=sys.stderr)
        return False

    if workspace:
        print("Sync workspace to remote host...")
        await cli.sync_workspace(workspace)
        print("Sync workspace complete")
    terminal_size = shutil.get_terminal_size((120, 30))
    await cli.create_shell((terminal_size.columns, terminal_size.lines))
    return True


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
    parser.add_argument("--log-file", help="Path to save log")
    parser.add_argument(
        "-d", "--daemon", help="Run as daemon", action="store_true", default=False
    )

    args = sys.argv[1:]
    if not args:
        parser.print_help()
        return 0

    args = parser.parse_args(args)

    url = urllib.parse.urlparse(args.url)
    if url.scheme != "ws":
        print("Error: Invalid websocket url %s" % args.url, file=sys.stderr)
        return -1

    log_file = None
    if args.log_file:
        log_file = os.path.abspath(args.log_file)

    if args.daemon:
        if not args.server:
            print("Error: -d/--daemon only supported on server", file=sys.stderr)
            return -1

        if sys.platform != "win32":
            import daemon

            daemon.DaemonContext(stderr=open("error.txt", "w")).open()
        else:
            utils.win32_daemon()
            return 0

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
    if args.server:
        utils.logger.addHandler(handler)
    else:
        log_file = log_file or "wsterm.log"

    if log_file:
        handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=4
        )
        formatter = logging.Formatter(
            "[%(asctime)s][%(levelname)s][%(filename)s][%(lineno)d]%(message)s"
        )
        handler.setFormatter(formatter)
        utils.logger.addHandler(handler)

    if args.server:
        host = url.hostname
        port = url.port or 80
        if sys.platform == "win32":
            loop = asyncio.ProactorEventLoop()
            asyncio.set_event_loop(loop)
        server.start_server((host, port), url.path, args.token)
    else:
        if sys.platform == "win32":
            utils.enable_native_ansi()

        if not asyncio.get_event_loop().run_until_complete(
            connect_server(args.url, args.workspace, args.token)
        ):
            return -1

    def handle_exception(loop, context):
        print("Exception caught:\n", file=sys.stderr)
        message = context["message"]
        exp = context.get("exception")
        if exp:
            message = "".join(
                traceback.format_exception(
                    etype=type(exp), value=exp, tb=exp.__traceback__
                )
            )
        print(message, file=sys.stderr)
        if not args.server:
            loop.stop()

    loop = asyncio.get_event_loop()
    loop.set_exception_handler(handle_exception)

    try:
        tornado.ioloop.IOLoop.current().start()
    except KeyboardInterrupt:
        print("Process exit warmly.")


if __name__ == "__main__":
    sys.exit(main())
