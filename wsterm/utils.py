# -*- coding: utf-8 -*-

"""
"""

import asyncio
import ctypes
import hashlib
import logging
import os
import subprocess
import sys

logger = logging.getLogger("wsterm")


class ConnectWebsocketServerFailed(RuntimeError):
    pass


class ConnectionClosedError(RuntimeError):
    pass


class AsyncFileDescriptor(object):
    """Async File Descriptor"""

    def __init__(self, fd):
        self._loop = asyncio.get_event_loop()
        self._fd = fd
        self._event = asyncio.Event()
        self._buffer = b""
        self._loop.add_reader(self._fd, self.read_callback)
        self._closed = False

    def close(self):
        self._loop.remove_reader(self._fd)

    async def read(self, bytes=4096):
        await self._event.wait()
        self._event.clear()
        buffer = self._buffer
        self._buffer = b""
        return buffer

    def write(self, buffer):
        os.write(self._fd, buffer)

    def read_callback(self, *args):
        try:
            buffer = os.read(self._fd, 4096)
        except OSError:
            self.close()
            self._closed = True
            self._event.set()
            return

        self._buffer += buffer
        self._event.set()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, exc_trackback):
        self.close()
        self._closed = True


class Process(object):
    def __init__(self, pid):
        self._pid = pid
        self._returncode = None
        safe_ensure_future(self._wait_for_exit())

    @property
    def returncode(self):
        return self._returncode

    async def _wait_for_exit(self):
        while True:
            try:
                pid, returncode = os.waitpid(self._pid, os.WNOHANG)
            except ChildProcessError:
                logger.warn(
                    "[%s] Process %d already exited" % (self.__class__.__name__, pid)
                )
                self._returncode = -1
                break

            if not pid:
                await asyncio.sleep(0.01)
            else:
                self._returncode = returncode
                break


class UnixStdIn(object):
    def __init__(self, stdin=None):
        self._fd = stdin or sys.stdin.fileno()
        import termios

        self._settings = termios.tcgetattr(self._fd)

    def __enter__(self):
        import tty

        tty.setraw(self._fd)
        return self

    def __exit__(self, exc_type, exc_value, exc_trackback):
        import termios

        termios.tcsetattr(self._fd, termios.TCSADRAIN, self._settings)

    def fileno(self):
        return self._fd

    def read(self, n):
        return os.read(self._fd, n)


def safe_ensure_future(coro, loop=None):
    loop = loop or asyncio.get_event_loop()
    fut = loop.create_future()

    async def _wrap():
        try:
            fut.set_result(await coro)
        except Exception as e:
            fut.set_exception(e)

    asyncio.ensure_future(_wrap())
    return fut


def enable_native_ansi():
    """Enables native ANSI sequences in console. Windows 10 only.
    Returns whether successful.
    """
    import ctypes.wintypes

    ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x04

    out_handle = ctypes.windll.kernel32.GetStdHandle(subprocess.STD_OUTPUT_HANDLE)

    # GetConsoleMode fails if the terminal isn't native.
    mode = ctypes.wintypes.DWORD()
    if ctypes.windll.kernel32.GetConsoleMode(out_handle, ctypes.byref(mode)) == 0:
        return False

    if not (mode.value & ENABLE_VIRTUAL_TERMINAL_PROCESSING):
        if (
            ctypes.windll.kernel32.SetConsoleMode(
                out_handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
            )
            == 0
        ):
            print(
                "kernel32.SetConsoleMode to enable ANSI sequences failed",
                file=sys.stderr,
            )
            return False

    return True


def make_short_hash(string):
    if not isinstance(string, bytes):
        string = string.encode("utf8")
    return hashlib.sha1(string).hexdigest()[:8]


def diff(data1, data2):
    """Get diff of data2 and data1"""
    result = {}
    for key in data1:
        if key not in data2:
            result[key] = data1[key]
        else:
            res = None
            if isinstance(data1[key], dict) and isinstance(data2[key], dict):
                res = diff(data1[key], data2[key])
            elif data1[key] != data2[key]:
                res = data1[key]
            if res:
                result[key] = res
    for key in data2:
        if key not in data1:
            # 已删除的节点
            result[key] = "-"
    return result


def win32_daemon():
    cmdline = []
    for it in sys.argv:
        if it not in ("-d", "--daemon"):
            cmdline.append(it)

    DETACHED_PROCESS = 8
    subprocess.Popen(cmdline, creationflags=DETACHED_PROCESS, close_fds=True)
