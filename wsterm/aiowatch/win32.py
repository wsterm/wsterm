# -*- coding: utf-8 -*-

import asyncio
import ctypes
import os

import pywintypes
import win32con
import win32event
import win32file

from . import WatcherBackendBase, WatchEvent

FILE_LIST_DIRECTORY = 1
FILE_ACTION_ADDED = 1
FILE_ACTION_REMOVED = 2
FILE_ACTION_MODIFIED = 3
FILE_ACTION_RENAMED_OLD_NAME = 4
FILE_ACTION_RENAMED_NEW_NAME = 5


class EnumWatchType(object):

    WATCH_DIRECTORY = 1
    WATCH_FILE = 2


class Win32Watcher(WatcherBackendBase):
    def __init__(self, loop=None):
        super(Win32Watcher, self).__init__(loop)
        self._watch_list = []
        asyncio.ensure_future(self.polling_task())

    def _create_watch_handle(self, path):
        return win32file.CreateFile(
            path,
            FILE_LIST_DIRECTORY,
            win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE,
            None,
            win32con.OPEN_EXISTING,
            win32con.FILE_FLAG_BACKUP_SEMANTICS | win32con.FILE_FLAG_OVERLAPPED,
            None,
        )

    def _add_file_watch(self, handle, buffer, ov):
        win32file.ReadDirectoryChangesW(
            handle,
            buffer,
            True,
            win32con.FILE_NOTIFY_CHANGE_FILE_NAME
            | win32con.FILE_NOTIFY_CHANGE_ATTRIBUTES
            | win32con.FILE_NOTIFY_CHANGE_SIZE
            | win32con.FILE_NOTIFY_CHANGE_LAST_WRITE
            | win32con.FILE_NOTIFY_CHANGE_SECURITY,
            ov,
        )

    def _add_dir_watch(self, handle, buffer, ov):
        win32file.ReadDirectoryChangesW(
            handle, buffer, True, win32con.FILE_NOTIFY_CHANGE_DIR_NAME, ov,
        )

    def add_dir_watch(self, path):
        # Add directory watch
        handle_dir = self._create_watch_handle(path)
        ov_dir = pywintypes.OVERLAPPED()
        ov_dir.hEvent = win32event.CreateEvent(None, 0, 0, None)
        buffer_dir = win32file.AllocateReadBuffer(8192)
        self._add_dir_watch(handle_dir, buffer_dir, ov_dir)
        self._watch_list.append(
            (EnumWatchType.WATCH_DIRECTORY, path, handle_dir, ov_dir, buffer_dir)
        )

        # Add file watch
        handle_file = self._create_watch_handle(path)
        ov_file = pywintypes.OVERLAPPED()
        ov_file.hEvent = win32event.CreateEvent(None, 0, 0, None)
        buffer_file = win32file.AllocateReadBuffer(8192)
        self._add_file_watch(handle_file, buffer_file, ov_file)
        self._watch_list.append(
            (EnumWatchType.WATCH_FILE, path, handle_file, ov_file, buffer_file)
        )

    async def polling_task(self):
        while True:
            for item in self._watch_list:
                try:
                    length = win32file.GetOverlappedResult(item[2], item[3], 0)
                except pywintypes.error as e:
                    if e.args[0] == 996:
                        # Overlapped I/O event is not in a signaled state
                        continue
                    else:
                        raise e
                else:
                    for action, name in win32file.FILE_NOTIFY_INFORMATION(
                        item[-1], length
                    ):  #
                        target = os.path.join(item[1], name)
                        self._event_queue.put_nowait((target, item[0], action))

                    self._add_dir_watch(item[2], item[4], item[3])
                    self._add_file_watch(item[2], item[4], item[3])

            await asyncio.sleep(0.005)

    async def read_event(self):
        while True:
            target, watch_type, action = await self._event_queue.get()
            isdir = watch_type == EnumWatchType.WATCH_DIRECTORY
            if action in (FILE_ACTION_ADDED, FILE_ACTION_RENAMED_NEW_NAME):
                if isdir:
                    return WatchEvent(WatchEvent.DIRECTORY_CREATED, target)
                else:
                    return WatchEvent(WatchEvent.FILE_CREATED, target)
            elif action == FILE_ACTION_MODIFIED:
                if not isdir:
                    return WatchEvent(WatchEvent.FILE_MODIFIED, target)
            elif action in (FILE_ACTION_REMOVED, FILE_ACTION_RENAMED_OLD_NAME):
                if isdir:
                    return WatchEvent(WatchEvent.DIRECTORY_REMOVED, target)
                else:
                    return WatchEvent(WatchEvent.FILE_REMOVED, target)
