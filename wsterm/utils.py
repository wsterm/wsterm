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
import time

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
            buffer = self._buffer + b"\r\n"
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


class Win32ConsoleInputPipe(object):
    """Win32 Console Input Pipe
    """

    def __init__(self):
        import msvcrt
        import win32api
        import win32con
        import win32console

        stdin = win32console.GetStdHandle(win32console.STD_INPUT_HANDLE)
        self._stdin = win32console.PyConsoleScreenBufferType(
            win32api.DuplicateHandle(
                win32api.GetCurrentProcess(),
                stdin,
                win32api.GetCurrentProcess(),
                0,
                0,
                win32con.DUPLICATE_SAME_ACCESS,
            )
        )
        stdin.Detach()
        stdin.Close()
        self._fd = msvcrt.open_osfhandle(int(self._stdin), os.O_APPEND)

    def fileno(self):
        return self._fd

    def write(self, buffer):
        import win32console

        key_events = []
        if isinstance(buffer, bytes):
            buffer = buffer.decode()

        for key in buffer:
            event = win32console.PyINPUT_RECORDType(win32console.KEY_EVENT)
            event.Char = key
            event.KeyDown = True
            event.RepeatCount = 1
            key_events.append(event)
        self._stdin.WriteConsoleInput(key_events)


class Win32ConsoleOutputPipe(object):
    """Console Output Pipe
    """

    def __init__(self, size):
        import msvcrt
        import win32console
        import win32security

        self._width, self._height = self._size = size
        self._height *= 256
        self._max_size = self._width * self._height
        sa = win32security.SECURITY_ATTRIBUTES()
        self._console = win32console.CreateConsoleScreenBuffer(SecurityAttributes=sa)
        self._console.SetConsoleCursorPosition(win32console.PyCOORDType(0, 0))
        self._console.SetConsoleScreenBufferSize(
            win32console.PyCOORDType(self._width, self._height)
        )
        self._console.FillConsoleOutputCharacter(
            "\x00", self._max_size, win32console.PyCOORDType(0, 0)
        )
        self._console.SetConsoleActiveScreenBuffer()
        self._fd = msvcrt.open_osfhandle(int(self._console), os.O_APPEND)
        self._running = True
        self._buffer = []
        self._last_read_pos = (0, 0)
        safe_ensure_future(self.processing_screen_buffer())

    def fileno(self):
        return self._fd

    def close(self):
        self._running = False

    def length(self, buffer):
        result = 0
        for c in buffer:
            if ord(c) < 128:
                result += 1
            else:
                result += 2
        return result

    def _merge_buffer(self, buffer):
        buffer = buffer.encode("gbk")
        if self._buffer and self.length(self._buffer[-1]) < self._width:
            extra_size = self._width - self.length(self._buffer[-1])
            self._buffer[-1] += buffer[:extra_size].decode("gbk")
            buffer = buffer[extra_size:]
        offset = 0
        while offset < len(buffer):
            self._buffer.append(buffer[offset : offset + self._width].decode("gbk"))
            offset += self._width

    def _handle_line(self, line):
        buffer = b""
        for c in line:
            if c == "\x00":
                buffer += b"\n"
                return buffer
            else:
                buffer += c.encode()
        return buffer

    async def processing_screen_buffer(self):
        import win32console

        last_pos = (0, 0)
        while self._running:
            csbi = self._console.GetConsoleScreenBufferInfo()
            if (
                csbi["CursorPosition"].X != last_pos[0]
                or csbi["CursorPosition"].Y != last_pos[1]
            ):
                width = csbi["Size"].X
                count = (
                    (csbi["CursorPosition"].Y - last_pos[1]) * width
                    + csbi["CursorPosition"].X
                    - last_pos[0]
                )

                output = self._console.ReadConsoleOutputCharacter(
                    count, win32console.PyCOORDType(*last_pos)
                )

                self._merge_buffer(output)
                last_pos = (csbi["CursorPosition"].X, csbi["CursorPosition"].Y)
                if last_pos[1] > self._size[1] * 128 and last_pos[0] == 0:
                    csbi = self._console.GetConsoleScreenBufferInfo()
                    if (
                        csbi["CursorPosition"].X == last_pos[0]
                        or csbi["CursorPosition"].Y == last_pos[1]
                    ):
                        # Ensure cursor not move
                        self._console.SetConsoleCursorPosition(
                            win32console.PyCOORDType(0, 0)
                        )
                        self._console.FillConsoleOutputCharacter(
                            "\x00",
                            self._width * last_pos[1],
                            win32console.PyCOORDType(0, 0),
                        )

                        last_pos = (0, 0)

            await asyncio.sleep(0.1)

    async def read(self, size, timeout=None):
        last_read_time = time.time()
        while self._running:
            if self._buffer and (
                self._last_read_pos[1] < len(self._buffer) - 1
                or self._last_read_pos[0] < len(self._buffer[-1])
                and self._last_read_pos[1] == len(self._buffer) - 1
            ):
                buffer = b""
                if (
                    self._last_read_pos[0] > 0
                    and len(self._buffer[self._last_read_pos[1]])
                    > self._last_read_pos[0]
                ):
                    # Read last line
                    buffer += self._handle_line(
                        self._buffer[self._last_read_pos[1]][self._last_read_pos[0] :]
                    )
                    self._last_read_pos = (0, self._last_read_pos[1] + 1)
                for line in self._buffer[self._last_read_pos[1] :]:
                    buffer += self._handle_line(line)

                if buffer[-1] != 10:
                    self._last_read_pos = len(self._buffer[-1]), len(self._buffer) - 1
                else:
                    self._last_read_pos = 0, len(self._buffer)
                return buffer
            if timeout and time.time() - last_read_time >= timeout:
                return b""
            await asyncio.sleep(0.1)


class Win32NamedPipe(object):
    def __init__(self, pipe_name, server_side=False):
        self._pipe_name = r"\\.\pipe\%s" % pipe_name
        self._pipe = None

    async def listen(self, timeout=None):
        import _winapi

        flags = _winapi.PIPE_ACCESS_INBOUND | _winapi.FILE_FLAG_OVERLAPPED
        self._pipe = _winapi.CreateNamedPipe(
            self._pipe_name,
            flags,
            _winapi.PIPE_TYPE_MESSAGE
            | _winapi.PIPE_READMODE_MESSAGE
            | _winapi.PIPE_WAIT,
            _winapi.PIPE_UNLIMITED_INSTANCES,
            65536,
            65536,
            _winapi.NMPWAIT_WAIT_FOREVER,
            _winapi.NULL,
        )

        ov = _winapi.ConnectNamedPipe(self._pipe, overlapped=True)
        time0 = time.time()
        while not timeout or time.time() - time0 < timeout:
            _, ret = ov.GetOverlappedResult(False)
            if not ret:
                return
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError("Wait for pipe %s client timeout" % self._pipe_name)

    async def connect(self, timeout=None):
        import _winapi

        time0 = time.time()
        while not timeout or time.time() - time0 < timeout:
            try:
                self._pipe = _winapi.CreateFile(
                    self._pipe_name,
                    _winapi.GENERIC_WRITE,
                    0,
                    _winapi.NULL,
                    _winapi.OPEN_EXISTING,
                    _winapi.FILE_FLAG_OVERLAPPED,
                    _winapi.NULL,
                )
            except FileNotFoundError:
                await asyncio.sleep(0.1)
            else:
                break
        else:
            raise RuntimeError("Connect pipe %s timeout" % self._pipe_name)

    async def read(self, size):
        import _overlapped

        ov = _overlapped.Overlapped(0)
        ov.ReadFile(self._pipe, size)
        while True:
            try:
                return ov.getresult()
            except OSError as ex:
                if ex.args[3] == 996:
                    await asyncio.sleep(0.01)
                else:
                    raise ex

    def write(self, buffer):
        import _overlapped

        ov = _overlapped.Overlapped(0)
        ov.WriteFile(self._pipe, buffer)


def dup_stdin_handle(target_process):
    import win32api
    import win32con
    import win32console

    stdin = win32api.GetStdHandle(win32console.STD_INPUT_HANDLE)
    target_process_handle = win32api.OpenProcess(
        win32con.PROCESS_ALL_ACCESS, False, target_process
    )
    handle = win32api.DuplicateHandle(
        win32api.GetCurrentProcess(),
        stdin,
        target_process_handle,
        0,
        0,
        win32con.DUPLICATE_SAME_ACCESS,
    )
    return handle


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
