# -*- coding: utf-8 -*-

import asyncio
import ctypes
import os
import shlex
import struct
import sys

from . import utils


class Shell(object):
    def __init__(self, workspace, size=None):
        self._workspace = workspace
        self._size = size or (80, 23)
        self._proc = None
        self._fd = None
        self._stdin = None
        self._stdout = None
        self._stderr = None

    @property
    def stdin(self):
        return self._stdin

    @property
    def stdout(self):
        return self._stdout

    @property
    def stderr(self):
        return self._stderr

    async def create(self):
        if sys.platform == "win32":
            if hasattr(ctypes.windll.kernel32, "CreatePseudoConsole"):
                cmd = (
                    "conhost.exe",
                    "--headless",
                    "--width",
                    str(self._size[0]),
                    "--height",
                    str(self._size[1]),
                    "--",
                    "cmd.exe",
                )
            else:
                cmd = ("cmd.exe",)
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=self._workspace,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                close_fds=False
            )
            self._stdin = proc.stdin
            self._stdout = proc.stdout
            self._stderr = proc.stderr
        else:
            import pty

            cmdline = list(shlex.split(os.environ.get("SHELL", "sh")))
            exe = cmdline[0]
            if exe[0] != "/":
                for it in os.environ["PATH"].split(":"):
                    path = os.path.join(it, exe)
                    if os.path.isfile(path):
                        exe = path
                        break

            utils.logger.info(
                "[%s] Create shell %s" % (self.__class__.__name__, cmdline)
            )
            pid, self._fd = pty.fork()
            if pid == 0:
                # child process
                sys.stdout.flush()
                os.chdir(self._workspace)
                try:
                    os.execve(exe, cmdline, os.environ)
                except Exception as e:
                    sys.stderr.write(str(e))
            else:
                proc = utils.Process(pid)
                self._stdin = utils.AsyncFileDescriptor(self._fd)
                self._stdout = utils.AsyncFileDescriptor(self._fd)
                self.resize(self._size)

        return proc, self._stdin, self._stdout, self._stderr

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
