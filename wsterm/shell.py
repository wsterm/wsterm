# -*- coding: utf-8 -*-

import asyncio
import ctypes
import os
import shlex
import struct
import subprocess
import sys
import uuid

from . import utils


class Shell(object):
    def __init__(self, workspace, size, proc, stdin, stdout, stderr, fd):
        self._workspace = workspace
        self._size = None
        self._proc = proc
        self._fd = fd
        self._stdin = stdin
        self._stdout = stdout
        self._stderr = stderr
        self.resize(size)

    @property
    def process(self):
        return self._proc

    @property
    def stdin(self):
        return self._stdin

    @property
    def stdout(self):
        return self._stdout

    @property
    def stderr(self):
        return self._stderr

    @classmethod
    async def create(cls, workspace, size=None):
        size = size or (80, 23)
        if sys.platform == "win32":
            import win32con

            if hasattr(ctypes.windll.kernel32, "CreatePseudoConsole"):
                cmd = (
                    "conhost.exe",
                    "--headless",
                    "--width",
                    str(size[0]),
                    "--height",
                    str(size[1]),
                    "--",
                    "cmd.exe",
                )

                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    cwd=workspace,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    close_fds=True,
                )
                stdin = proc.stdin
            else:
                pipe_name = str(uuid.uuid4())
                cmd = (
                    sys.executable,
                    "-u",
                    "-m",
                    "wsterm.win32pty",
                    "--size",
                    "%d,%d" % tuple(size),
                    "--pipe",
                    pipe_name,
                    "cmd.exe",
                )
                CREATE_NEW_CONSOLE = 0x10
                CREATE_NO_WINDOW = 0x08000000
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = win32con.SW_HIDE
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    cwd=workspace,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    creationflags=CREATE_NEW_CONSOLE,
                    close_fds=True,
                    startupinfo=si,
                )

                pipe = utils.Win32NamedPipe(pipe_name)

                utils.safe_ensure_future(pipe.connect(10))
                stdin = pipe

            stdout = proc.stdout
            stderr = proc.stderr
            fd = None
        else:
            import pty

            cmdline = list(shlex.split(os.environ.get("SHELL") or "bash"))
            exe = cmdline[0]
            if exe[0] != "/":
                for it in os.environ["PATH"].split(":"):
                    path = os.path.join(it, exe)
                    if os.path.isfile(path):
                        exe = path
                        break
                else:
                    exe = "/bin/sh"

            utils.logger.info("[%s] Create shell %s" % (cls.__name__, cmdline))
            pid, fd = pty.fork()
            if pid == 0:
                # child process
                sys.stdout.flush()
                os.chdir(workspace)
                try:
                    os.execve(exe, cmdline, os.environ)
                except Exception as e:
                    sys.stderr.write(str(e))
            else:
                proc = utils.Process(pid)
                stdin = utils.AsyncFileDescriptor(fd)
                stdout = utils.AsyncFileDescriptor(fd)
                stderr = None

        return cls(workspace, size, proc, stdin, stdout, stderr, fd)

    def write(self, buffer):
        self._stdin.write(buffer)

    def resize(self, size):
        if sys.platform == "win32":
            pass
        else:
            import fcntl
            import termios

            winsize = struct.pack("HHHH", size[1], size[0], 0, 0)
            fcntl.ioctl(self._fd, termios.TIOCSWINSZ, winsize)
        self._size = size
        return True

    def exit(self):
        self._stdin.write(b"exit\n")
        utils.logger.info("[%s] Shell exit" % self.__class__.__name__)
