# -*- coding: utf-8 -*-

import argparse
import asyncio
import shutil
import sys
import traceback

import win32console

from . import utils


async def forward_stdin(proc, pipe, stdin):
    while proc.returncode is None:
        buffer = await pipe.read(4096)
        stdin.write(buffer)


async def create_process(*cmd, size=None, pipe=None):
    if not sys.stdin.isatty():
        print("Stdin is not a tty", file=sys.stderr)
        return -1

    if pipe:
        pipe = utils.Win32NamedPipe(pipe)
        await pipe.listen(10)
        print("client connected")

    if not size:
        terminal_size = shutil.get_terminal_size()
        size = terminal_size.columns, terminal_size.lines

    stdin = utils.Win32ConsoleInputPipe()
    stdout = stderr = utils.Win32ConsoleOutputPipe(size)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=stdin.fileno(),
        stdout=stdout.fileno(),
        stderr=stderr.fileno(),
        close_fds=False,
    )

    utils.safe_ensure_future(forward_stdin(proc, pipe, stdin))

    while proc.returncode is None:
        buffer = await stdout.read(4096, 0.1)
        if buffer:
            sys.stdout.buffer.write(buffer)
            sys.stdout.flush()

    stdout.close()


def main():
    parser = argparse.ArgumentParser(
        prog="python -m wsterm.rtcon", description="Real Time Console."
    )
    parser.add_argument("--size", help="Console window size")
    parser.add_argument("--pipe", help="Stdin pipe name")
    parser.add_argument("command", help="Command")
    args_index = 1
    while args_index < len(sys.argv):
        if sys.argv[args_index] in ("--size", "--pipe"):
            args_index += 2
        else:
            break

    args = parser.parse_args(sys.argv[1 : args_index + 1])
    cmdline = sys.argv[args_index:]
    size = None
    if args.size:
        size = args.size.split(",")
        size = (int(size[0]), int(size[1]))

    stdout = win32console.GetStdHandle(win32console.STD_OUTPUT_HANDLE)
    loop = asyncio.ProactorEventLoop()
    asyncio.set_event_loop(loop)

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

    loop.set_exception_handler(handle_exception)
    loop.run_until_complete(create_process(*cmdline, size=size, pipe=args.pipe))
    try:
        stdout.SetConsoleActiveScreenBuffer()
    except:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
