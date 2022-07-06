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


class WSTermRuntimeError(RuntimeError):
    def __init__(self, code, message):
        self._code = code
        self._message = message

    @property
    def code(self):
        return self._code

    @property
    def message(self):
        return self._message

    def __str__(self):
        return "[%d] %s" % (self._code, self._message)


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


class Singleton(object):
    """Singleton Decorator"""

    def __init__(self, cls):
        self.__instance = None
        self.__cls = cls

    def __call__(self, *args, **kwargs):
        if not self.__instance:
            self.__instance = self.__cls(*args, **kwargs)
        return self.__instance


class LineEditor(object):
    def __init__(self):
        self._buffer = b""
        self._prev_buffer = b""
        self._cursor = 0
        self._prev_cursor = 0
        self._history = []
        self._history_index = 0

    def _clear_buffer(self, size):
        bs_key = b"\x08" if sys.platform == "win32" else b"\x1b[D"
        sys.stdout.buffer.write(bs_key * size)
        sys.stdout.buffer.write(b" " * size)
        sys.stdout.buffer.write(bs_key * size)

    def input(self, char):
        bs_key = b"\x08" if sys.platform == "win32" else b"\x1b[D"
        if char in (b"\x1bOD", b"\x1b[D"):
            if self._cursor > 0:
                self._cursor -= 1
        elif char in (b"\x1bOC", b"\x1b[C"):
            if self._cursor < len(self._buffer):
                self._cursor += 1
        elif char in (b"\x1bOA", b"\x1b[A"):
            if abs(self._history_index) >= len(self._history):
                return
            self._history_index -= 1
            self._clear_buffer(self._prev_cursor)
            sys.stdout.buffer.write(self._history[self._history_index])
            sys.stdout.buffer.flush()
            self._buffer = self._prev_buffer = self._history[self._history_index]
            self._cursor = self._prev_cursor = len(self._buffer)
            return
        elif char in (b"\x1bOB", b"\x1b[B"):
            if self._history_index >= -1:
                return
            self._history_index += 1
            self._clear_buffer(self._prev_cursor)
            sys.stdout.buffer.write(self._history[self._history_index])
            sys.stdout.buffer.flush()
            self._buffer = self._prev_buffer = self._history[self._history_index]
            self._cursor = self._prev_cursor = len(self._buffer)
            return
        elif char in (b"\x08", b"\x7f"):
            if self._cursor > 0:
                self._buffer = (
                    self._buffer[: self._cursor - 1] + self._buffer[self._cursor :]
                )
                self._cursor -= 1
        elif char in (b"\r", b"\n"):
            if self._buffer:
                self._history.append(self._buffer)
                self._history_index = 0
            buffer = self._buffer + b"\n"
            self._buffer = b""
            self._cursor = 0
            if self._prev_cursor:
                sys.stdout.buffer.write(bs_key * self._prev_cursor)
                self._prev_buffer = b""
                self._prev_cursor = 0

            return buffer
        elif char == b"\x03":
            raise KeyboardInterrupt()
        else:
            self._buffer = (
                self._buffer[: self._cursor] + char + self._buffer[self._cursor :]
            )
            self._cursor += 1

        if self._prev_cursor:
            sys.stdout.buffer.write(bs_key * self._prev_cursor)

        sys.stdout.buffer.write(self._buffer)
        if len(self._buffer) < len(self._prev_buffer):
            # Remove deleted chars
            sys.stdout.buffer.write(b" " * (len(self._prev_buffer) - len(self._buffer)))
            sys.stdout.buffer.write(
                bs_key * (len(self._prev_buffer) - len(self._buffer))
            )

        if self._cursor < len(self._buffer):
            sys.stdout.buffer.write(bs_key * (len(self._buffer) - self._cursor))
        sys.stdout.buffer.flush()
        self._prev_buffer = self._buffer
        self._prev_cursor = self._cursor


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


def safe_import(module_name, attr):
    sys_path = sys.path
    module = None
    for path in sys_path:
        sys.path = [path]
        try:
            module = __import__(module_name)
        except ImportError:
            pass
        else:
            if getattr(module, attr, None):
                break
            else:
                del sys.modules[module_name]

    sys.path = sys_path
    if not module:
        raise ImportError("No module named %r" % module_name)
    return module


def write_stdout_inplace(content):
    sys.stdout.write("\r" + " " * 80)
    sys.stdout.write("\r" + content[:80])
    sys.stdout.flush()
