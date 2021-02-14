# -*- coding: utf-8 -*-

import asyncio
import ctypes
import ctypes.util
import errno
import functools
import os
import struct

from . import WatcherBackendBase, WatchEvent


def _load_libc():
    libc_path = None
    try:
        libc_path = ctypes.util.find_library("c")
    except (OSError, RuntimeError):
        # Note: find_library will on some platforms raise these undocumented
        # errors, e.g.on android OSError "No usable temporary directory found"
        # will be raised.
        pass

    if libc_path is not None:
        return ctypes.CDLL(libc_path)

    # Fallbacks
    try:
        return ctypes.CDLL("libc.so")
    except OSError:
        pass

    try:
        return ctypes.CDLL("libc.so.6")
    except OSError:
        pass

    # uClibc
    try:
        return ctypes.CDLL("libc.so.0")
    except OSError as err:
        raise err


libc = _load_libc()

if (
    not hasattr(libc, "inotify_init")
    or not hasattr(libc, "inotify_add_watch")
    or not hasattr(libc, "inotify_rm_watch")
):
    raise RuntimeError("Unsupported libc version found: %s" % libc._name)


class InotifyConstants(object):
    # User-space events
    IN_ACCESS = 0x00000001  # File was accessed.
    IN_MODIFY = 0x00000002  # File was modified.
    IN_ATTRIB = 0x00000004  # Meta-data changed.
    IN_CLOSE_WRITE = 0x00000008  # Writable file was closed.
    IN_CLOSE_NOWRITE = 0x00000010  # Unwritable file closed.
    IN_OPEN = 0x00000020  # File was opened.
    IN_MOVED_FROM = 0x00000040  # File was moved from X.
    IN_MOVED_TO = 0x00000080  # File was moved to Y.
    IN_CREATE = 0x00000100  # Subfile was created.
    IN_DELETE = 0x00000200  # Subfile was deleted.
    IN_DELETE_SELF = 0x00000400  # Self was deleted.
    IN_MOVE_SELF = 0x00000800  # Self was moved.

    # Helper user-space events.
    IN_CLOSE = IN_CLOSE_WRITE | IN_CLOSE_NOWRITE  # Close.
    IN_MOVE = IN_MOVED_FROM | IN_MOVED_TO  # Moves.

    # Events sent by the kernel to a watch.
    IN_UNMOUNT = 0x00002000  # Backing file system was unmounted.
    IN_Q_OVERFLOW = 0x00004000  # Event queued overflowed.
    IN_IGNORED = 0x00008000  # File was ignored.

    # Special flags.
    IN_ONLYDIR = 0x01000000  # Only watch the path if it's a directory.
    IN_DONT_FOLLOW = 0x02000000  # Do not follow a symbolic link.
    IN_EXCL_UNLINK = 0x04000000  # Exclude events on unlinked objects
    IN_MASK_ADD = 0x20000000  # Add to the mask of an existing watch.
    IN_ISDIR = 0x40000000  # Event occurred against directory.
    IN_ONESHOT = 0x80000000  # Only send event once.

    # All user-space events.
    IN_ALL_EVENTS = functools.reduce(
        lambda x, y: x | y,
        [
            IN_ACCESS,
            IN_MODIFY,
            IN_ATTRIB,
            IN_CLOSE_WRITE,
            IN_CLOSE_NOWRITE,
            IN_OPEN,
            IN_MOVED_FROM,
            IN_MOVED_TO,
            IN_DELETE,
            IN_CREATE,
            IN_DELETE_SELF,
            IN_MOVE_SELF,
        ],
    )

    # Flags for ``inotify_init1``
    IN_CLOEXEC = 0x02000000
    IN_NONBLOCK = 0x00004000

    @staticmethod
    def parse(mask):
        result = []
        for key in dir(InotifyConstants):
            if not key.startswith("IN_"):
                continue
            if mask & getattr(InotifyConstants, key):
                result.append(key)
        return result


class inotify_event_struct(ctypes.Structure):
    """
    Structure representation of the inotify_event structure
    (used in buffer size calculations)::

        struct inotify_event {
            __s32 wd;            /* watch descriptor */
            __u32 mask;          /* watch mask */
            __u32 cookie;        /* cookie to synchronize two events */
            __u32 len;           /* length (including nulls) of name */
            char  name[0];       /* stub for possible name */
        };
    """

    _fields_ = [
        ("wd", ctypes.c_int),
        ("mask", ctypes.c_uint32),
        ("cookie", ctypes.c_uint32),
        ("len", ctypes.c_uint32),
        ("name", ctypes.c_char_p),
    ]


EVENT_SIZE = ctypes.sizeof(inotify_event_struct)
DEFAULT_NUM_EVENTS = 2048
DEFAULT_EVENT_BUFFER_SIZE = DEFAULT_NUM_EVENTS * (EVENT_SIZE + 16)


class INotifyWatcher(WatcherBackendBase):
    """inotify implementation"""

    def __init__(self, loop=None):
        super(INotifyWatcher, self).__init__(loop)
        self._inotify_fd = libc.inotify_init()
        if self._inotify_fd == -1:
            INotifyWatcher._raise_error()
        self._loop.add_reader(self._inotify_fd, self._read_event)
        self._watch_list = {}

    async def read_event(self):
        move_from = None
        while True:
            target, mask = await self._event_queue.get()
            isdir = mask & InotifyConstants.IN_ISDIR
            if mask & InotifyConstants.IN_CREATE:
                if isdir:
                    return WatchEvent(WatchEvent.DIRECTORY_CREATED, target)
                else:
                    return WatchEvent(WatchEvent.FILE_CREATED, target)
            elif mask & InotifyConstants.IN_MODIFY:
                if not isdir:
                    return WatchEvent(WatchEvent.FILE_MODIFIED, target)
            elif mask & InotifyConstants.IN_DELETE:
                if isdir:
                    return WatchEvent(WatchEvent.DIRECTORY_REMOVED, target)
                else:
                    return WatchEvent(WatchEvent.FILE_REMOVED, target)
            elif (
                mask & InotifyConstants.IN_MOVE
                and mask & InotifyConstants.IN_MOVED_FROM
            ):
                move_from = target
            elif (
                mask & InotifyConstants.IN_MOVE and mask & InotifyConstants.IN_MOVED_TO
            ):
                return WatchEvent(WatchEvent.ITEM_MOVED, (move_from, target))

    def add_dir_watch(self, path, mask=InotifyConstants.IN_ALL_EVENTS):
        assert os.path.isdir(path)
        for it in os.listdir(path):
            subpath = os.path.join(path, it)
            if os.path.isdir(subpath):
                self.add_dir_watch(subpath, mask)
        self.add_watch(path, mask)

    def add_watch(self, path, mask=InotifyConstants.IN_ALL_EVENTS):
        wd = libc.inotify_add_watch(self._inotify_fd, path.encode(), mask)
        if wd == -1:
            INotifyWatcher._raise_error()
        self._watch_list[wd] = path

    def _read_event(self, event_buffer_size=DEFAULT_EVENT_BUFFER_SIZE):
        event_buffer = os.read(self._inotify_fd, event_buffer_size)
        i = 0
        while i + 16 <= len(event_buffer):
            wd, mask, _, length = struct.unpack_from("iIII", event_buffer, i)
            name = event_buffer[i + 16 : i + 16 + length].rstrip(b"\0")
            i += 16 + length
            target = self._watch_list.get(wd)
            if name:
                target = os.path.join(target, name.decode())

            self._event_queue.put_nowait((target, mask))
            if mask & InotifyConstants.IN_CREATE and mask & InotifyConstants.IN_ISDIR:
                # Handle mkdir -p
                def _handle_sub_dir(path):
                    for it in os.listdir(path):
                        mask = InotifyConstants.IN_CREATE
                        subpath = os.path.join(path, it)
                        if os.path.isdir(subpath):
                            mask |= InotifyConstants.IN_ISDIR
                        self._event_queue.put_nowait((subpath, mask))
                        if os.path.isdir(subpath):
                            _handle_sub_dir(subpath)

                _handle_sub_dir(target)
                self.add_dir_watch(target)

    @staticmethod
    def _raise_error():
        """
        Raises errors for inotify failures.
        """
        err = ctypes.get_errno()
        if err == errno.ENOSPC:
            raise OSError(errno.ENOSPC, "inotify watch limit reached")
        elif err == errno.EMFILE:
            raise OSError(errno.EMFILE, "inotify instance limit reached")
        elif err == errno.EACCES:
            # Prevent raising an exception when a file with no permissions
            # changes
            pass
        else:
            raise OSError(err, os.strerror(err))
